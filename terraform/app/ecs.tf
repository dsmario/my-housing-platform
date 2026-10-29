resource "aws_ecs_cluster" "this" {
  name = local.name
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/mhp/${var.environment}/api"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "web" {
  name              = "/mhp/${var.environment}/web"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_group" "vault_worker" {
  name              = "/mhp/${var.environment}/vault-worker"
  retention_in_days = 7
}

# ---------------------------------------------------------------------------
# Roles. Execution role: pull images, write logs, read the DB secret.
# Task roles, one per job (Doc Vault §8, least privilege):
#   api           signs uploads into quarantine, reads the vault, uses the key.
#   vault-worker  reads and deletes quarantine, writes the vault, uses the key,
#                 takes scan results from the queue.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ecs_tasks_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "execution" {
  name               = "${local.name}-ecs-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_db_secret" {
  name = "read-db-secret"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = "secretsmanager:GetSecretValue"
      Resource = [
        aws_db_instance.this.master_user_secret[0].secret_arn,
        aws_secretsmanager_secret.web_auth.arn,
      ]
    }]
  })
}

resource "aws_iam_role" "api_task" {
  name               = "${local.name}-api"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "api_vault" {
  name = "doc-vault"
  role = aws_iam_role.api_task.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # The browser's presigned POST is authorized as this role.
        Sid      = "SignUploadsIntoQuarantine"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "arn:aws:s3:::${local.quarantine_bucket}/uploads/*"
      },
      {
        Sid      = "ReadVaultDocuments"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::${local.vault_bucket}/documents/*"
      },
      {
        # Per-youth data keys, and S3's own encryption with the vault key.
        Sid      = "UseVaultKey"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = local.vault_key_arn
      },
    ]
  })
}

resource "aws_iam_role" "vault_worker" {
  name               = "${local.name}-vault-worker"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_trust.json
}

resource "aws_iam_role_policy" "vault_worker" {
  name = "doc-vault-worker"
  role = aws_iam_role.vault_worker.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TakeFromQuarantine"
        Effect = "Allow"
        Action = [
          "s3:GetObject", "s3:GetObjectVersion",
          "s3:GetObjectTagging", "s3:GetObjectVersionTagging",
          "s3:DeleteObject", "s3:DeleteObjectVersion",
        ]
        Resource = "arn:aws:s3:::${local.quarantine_bucket}/uploads/*"
      },
      {
        Sid      = "WriteVaultDocuments"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "arn:aws:s3:::${local.vault_bucket}/documents/*"
      },
      {
        # Purging deleted documents (§10): every version goes, not just the latest.
        Sid      = "PurgeVaultDocuments"
        Effect   = "Allow"
        Action   = ["s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = "arn:aws:s3:::${local.vault_bucket}/documents/*"
      },
      {
        Sid      = "ListVersionsToPurge"
        Effect   = "Allow"
        Action   = "s3:ListBucketVersions"
        Resource = ["arn:aws:s3:::${local.vault_bucket}", "arn:aws:s3:::${local.quarantine_bucket}"]
        Condition = {
          StringLike = { "s3:prefix" = ["documents/*", "uploads/*"] }
        }
      },
      {
        Sid      = "UseVaultKey"
        Effect   = "Allow"
        Action   = ["kms:GenerateDataKey", "kms:Decrypt"]
        Resource = local.vault_key_arn
      },
      {
        Sid      = "ScanResults"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"]
        Resource = local.scan_queue_arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# Task definitions. api and web listen on 8000, the port the tasks security
# group admits from the ALB. vault-worker listens on nothing.
# ---------------------------------------------------------------------------
locals {
  db_env = [
    { name = "DB_HOST", value = aws_db_instance.this.address },
    { name = "DB_PORT", value = tostring(aws_db_instance.this.port) },
    { name = "DB_NAME", value = aws_db_instance.this.db_name },
  ]
  db_secrets = [
    { name = "DB_USER", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:username::" },
    { name = "DB_PASSWORD", valueFrom = "${aws_db_instance.this.master_user_secret[0].secret_arn}:password::" },
  ]
  # Doc Vault (§4): per-youth keys come from the vault KMS key.
  vault_env = [
    { name = "AWS_DEFAULT_REGION", value = var.region },
    { name = "VAULT_KEY_PROVIDER", value = "kms" },
    { name = "VAULT_KMS_KEY_ID", value = local.vault_key_arn },
    { name = "VAULT_QUARANTINE_BUCKET", value = local.quarantine_bucket },
    { name = "VAULT_BUCKET", value = local.vault_bucket },
  ]
}
resource "aws_ecs_task_definition" "api" {
  family                   = "${local.name}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.api_task.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "api"
    image        = local.api_image
    essential    = true
    portMappings = [{ containerPort = local.app_port, protocol = "tcp" }]
    environment = concat(local.db_env, local.vault_env, [
      { name = "IDENTITY_PROVIDER", value = "cognito" },
      # Dev holds test data only, so the seed script runs. Production leaves this unset.
      { name = "SEED_DATA", value = var.environment == "dev" ? "true" : "false" },
      { name = "COGNITO_ISSUER", value = local.identity.users_issuer },
      { name = "COGNITO_CLIENT_ID", value = local.identity.web_client_id },
      { name = "STAFF_COGNITO_ISSUER", value = local.identity.staff_issuer },
      { name = "STAFF_COGNITO_CLIENT_ID", value = local.identity.staff_web_client_id },
    ])
    secrets = concat(local.db_secrets, [
      { name = "API_FORWARD_SECRET", valueFrom = "${aws_secretsmanager_secret.web_auth.arn}:API_FORWARD_SECRET::" },
    ])
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.api.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "api"
      }
    }
  }])
}

resource "aws_ecs_task_definition" "web" {
  family                   = "${local.name}-web"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.execution.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "web"
    image        = local.web_image
    essential    = true
    portMappings = [{ containerPort = local.app_port, protocol = "tcp" }]
    environment = [
      { name = "PORT", value = tostring(local.app_port) },
      # Server-side calls go through the public HTTPS address and the ALB.
      { name = "API_URL", value = "https://${local.hostname}" },
      { name = "AUTH_URL", value = "https://${local.hostname}/auth" },
      { name = "AUTH_COGNITO_ID", value = local.identity.web_client_id },
      { name = "AUTH_COGNITO_ISSUER", value = local.identity.users_issuer },
      { name = "AUTH_COGNITO_DOMAIN", value = local.identity.login_domain },
      { name = "AUTH_STAFF_COGNITO_ID", value = local.identity.staff_web_client_id },
      { name = "AUTH_STAFF_COGNITO_ISSUER", value = local.identity.staff_issuer },
      { name = "AUTH_STAFF_COGNITO_DOMAIN", value = local.identity.staff_login_domain },
    ]
    secrets = [
      { name = "AUTH_SECRET", valueFrom = "${aws_secretsmanager_secret.web_auth.arn}:AUTH_SECRET::" },
      { name = "AUTH_COGNITO_SECRET", valueFrom = "${aws_secretsmanager_secret.web_auth.arn}:AUTH_COGNITO_SECRET::" },
      { name = "AUTH_STAFF_COGNITO_SECRET", valueFrom = "${aws_secretsmanager_secret.web_auth.arn}:AUTH_STAFF_COGNITO_SECRET::" },
      { name = "API_FORWARD_SECRET", valueFrom = "${aws_secretsmanager_secret.web_auth.arn}:API_FORWARD_SECRET::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.web.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "web"
      }
    }
  }])
}

# ---------------------------------------------------------------------------
# Services. One task each; a failed deployment rolls back automatically.
# ---------------------------------------------------------------------------
resource "aws_ecs_service" "api" {
  name                              = "api"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.api.arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  network_configuration {
    subnets          = local.net.public_subnet_ids
    security_groups  = [local.net.tasks_sg_id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = local.app_port
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener_rule.api]
}

resource "aws_ecs_service" "web" {
  name                              = "web"
  cluster                           = aws_ecs_cluster.this.id
  task_definition                   = aws_ecs_task_definition.web.arn
  desired_count                     = 1
  launch_type                       = "FARGATE"
  health_check_grace_period_seconds = 60
  network_configuration {
    subnets          = local.net.public_subnet_ids
    security_groups  = [local.net.tasks_sg_id]
    assign_public_ip = true
  }
  load_balancer {
    target_group_arn = aws_lb_target_group.web.arn
    container_name   = "web"
    container_port   = local.app_port
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  depends_on = [aws_lb_listener.https]
}

# ---------------------------------------------------------------------------
# Doc Vault worker (§2, §3): the API image, running vault_worker.py. Takes scan
# results from the queue; no load balancer, nothing listens.
# ---------------------------------------------------------------------------
resource "aws_ecs_task_definition" "vault_worker" {
  family                   = "${local.name}-vault-worker"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 512 # rebuilding images and PDFs (sanitize.py) needs more than the API
  memory                   = 1024
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.vault_worker.arn
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name      = "vault-worker"
    image     = local.api_image
    essential = true
    command   = ["python", "vault_worker.py"]
    environment = concat(local.db_env, local.vault_env, [
      { name = "VAULT_SCAN_QUEUE_URL", value = local.scan_queue_url },
    ])
    secrets = local.db_secrets
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.vault_worker.name
        awslogs-region        = var.region
        awslogs-stream-prefix = "vault-worker"
      }
    }
  }])
}

resource "aws_ecs_service" "vault_worker" {
  name            = "vault-worker"
  cluster         = aws_ecs_cluster.this.id
  task_definition = aws_ecs_task_definition.vault_worker.arn
  desired_count   = 1
  launch_type     = "FARGATE"
  network_configuration {
    subnets          = local.net.public_subnet_ids
    security_groups  = [local.net.tasks_sg_id]
    assign_public_ip = true
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
}

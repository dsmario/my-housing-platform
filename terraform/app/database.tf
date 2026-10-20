# RDS PostgreSQL 16. Dev shape per the deck: db.t4g.micro, single AZ, 20 GB,
# private data subnets, AWS-managed encryption key, 7-day backups.
# The master password is generated and stored by RDS in Secrets Manager.
resource "aws_db_instance" "this" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "16"
  instance_class              = var.db_instance_class
  allocated_storage           = 20
  storage_type                = "gp3"
  storage_encrypted           = true
  db_name                     = "mhp"
  username                    = "mhp_admin"
  manage_master_user_password = true
  db_subnet_group_name        = local.net.db_subnet_group
  vpc_security_group_ids      = [local.net.db_sg_id]
  publicly_accessible         = false
  multi_az                    = false
  backup_retention_period     = 7
  auto_minor_version_upgrade  = true
  apply_immediately           = true
  # Dev holds test data only. Production turns both of these around.
  deletion_protection = false
  skip_final_snapshot = true
}

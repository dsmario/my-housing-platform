output "ecr_repository_url" { value = aws_ecr_repository.api.repository_url }
output "github_deploy_role_arn" { value = aws_iam_role.github_deploy.arn }
output "web_ecr_repository_url" { value = aws_ecr_repository.web.repository_url }

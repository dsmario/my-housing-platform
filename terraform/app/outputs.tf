output "url" {
  value = "https://${local.hostname}"
}

output "cluster_name" {
  value = aws_ecs_cluster.this.name
}

output "db_endpoint" {
  value = aws_db_instance.this.address
}

output "db_secret_arn" {
  value = aws_db_instance.this.master_user_secret[0].secret_arn
}

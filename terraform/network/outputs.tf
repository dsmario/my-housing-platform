output "vpc_id" { value = aws_vpc.this.id }
output "public_subnet_ids" { value = aws_subnet.public[*].id }
output "data_subnet_ids" { value = aws_subnet.data[*].id }
output "alb_sg_id" { value = aws_security_group.alb.id }
output "tasks_sg_id" { value = aws_security_group.tasks.id }
output "db_sg_id" { value = aws_security_group.db.id }
output "db_subnet_group" { value = aws_db_subnet_group.this.name }
output "s3_endpoint_id" { value = aws_vpc_endpoint.s3.id }

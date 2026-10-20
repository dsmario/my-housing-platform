output "zone_id" {
  value = aws_route53_zone.env.zone_id
}

output "zone_name" {
  value = aws_route53_zone.env.name
}

output "name_servers" {
  description = "Add each as an NS record named after the environment in GoDaddy DNS."
  value       = aws_route53_zone.env.name_servers
}

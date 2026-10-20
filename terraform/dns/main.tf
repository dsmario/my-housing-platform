# Route 53 zone for dev.myhousingplatform.org. The parent domain stays at GoDaddy;
# GoDaddy holds four NS records named "dev" that point here (see outputs).
resource "aws_route53_zone" "env" {
  name    = "${var.environment}.${var.parent_domain}"
  comment = "MHP ${var.environment}. Delegated from GoDaddy by NS records."
}

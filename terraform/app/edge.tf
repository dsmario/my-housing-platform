# ---------------------------------------------------------------------------
# Certificate (ACM, DNS-validated in the delegated Route 53 zone)
# ---------------------------------------------------------------------------
resource "aws_acm_certificate" "this" {
  domain_name       = local.hostname
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "cert_validation" {
  for_each = {
    for o in aws_acm_certificate.this.domain_validation_options : o.domain_name => o
  }
  zone_id = local.zone_id
  name    = each.value.resource_record_name
  type    = each.value.resource_record_type
  records = [each.value.resource_record_value]
  ttl     = 300
}

resource "aws_acm_certificate_validation" "this" {
  certificate_arn         = aws_acm_certificate.this.arn
  validation_record_fqdns = [for r in aws_route53_record.cert_validation : r.fqdn]
}

# ---------------------------------------------------------------------------
# Load balancer. HTTPS only: the network stack's ALB security group allows 443.
# ---------------------------------------------------------------------------
resource "aws_lb" "this" {
  name                       = local.name
  load_balancer_type         = "application"
  security_groups            = [local.net.alb_sg_id]
  subnets                    = local.net.public_subnet_ids
  drop_invalid_header_fields = true
}

resource "aws_lb_target_group" "web" {
  name        = "${local.name}-web"
  port        = local.app_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = local.net.vpc_id
  health_check {
    path    = "/"
    matcher = "200"
  }
  deregistration_delay = 30
}

resource "aws_lb_target_group" "api" {
  name        = "${local.name}-api"
  port        = local.app_port
  protocol    = "HTTP"
  target_type = "ip"
  vpc_id      = local.net.vpc_id
  health_check {
    path    = "/api/health"
    matcher = "200"
  }
  deregistration_delay = 30
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.this.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.web.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern {
      values = ["/api/*", "/docs", "/openapi.json"]
    }
  }
}

resource "aws_route53_record" "app" {
  zone_id = local.zone_id
  name    = local.hostname
  type    = "A"
  alias {
    name                   = aws_lb.this.dns_name
    zone_id                = aws_lb.this.zone_id
    evaluate_target_health = false
  }
}

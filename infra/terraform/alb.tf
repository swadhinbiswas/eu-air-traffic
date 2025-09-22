# ─────────────────────────────────────────────────────────────────────────────
# ALB in front of the collector's live API.
#
# The container serves /live/snapshot and /health on 8090; the ALB terminates
# TLS and forwards plain HTTP to the target group. With no ACM certificate the
# ALB still comes up on port 80 so the stack is testable end-to-end before a
# domain is chosen.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_lb" "main" {
  name               = "${local.name_prefix}-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  enable_deletion_protection = var.alb_deletion_protection
  idle_timeout               = 60

  tags = {
    Name = "${local.name_prefix}-alb"
  }
}

resource "aws_lb_target_group" "collector" {
  name        = "${local.name_prefix}-collector"
  port        = var.collector_container_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  # /health is the same endpoint the container healthcheck uses.
  health_check {
    enabled             = true
    path                = "/health"
    port                = "traffic-port"
    protocol            = "HTTP"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }

  deregistration_delay = 30

  tags = {
    Name = "${local.name_prefix}-collector"
  }
}

# HTTPS listener — only when a certificate is supplied. The ACM cert must be in
# the same region as the ALB (eu-central-1 by default).
resource "aws_lb_listener" "https" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = var.alb_ssl_policy
  certificate_arn   = var.acm_certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.collector.arn
  }

  tags = {
    Name = "${local.name_prefix}-https"
  }
}

# Port 80 redirects to 443 once TLS exists.
resource "aws_lb_listener" "redirect" {
  count = var.acm_certificate_arn != "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }

  tags = {
    Name = "${local.name_prefix}-http-redirect"
  }
}

# No certificate yet: serve the API directly on 80 so the stack is reachable.
resource "aws_lb_listener" "http" {
  count = var.acm_certificate_arn == "" ? 1 : 0

  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.collector.arn
  }

  tags = {
    Name = "${local.name_prefix}-http"
  }
}

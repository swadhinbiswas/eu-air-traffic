# ─────────────────────────────────────────────────────────────────────────────
# Network: VPC, public/private/database subnets, NAT, S3 gateway endpoint,
# security groups.
#
# Hand-rolled rather than using terraform-aws-modules/vpc so the routing and
# the security-group graph are explicit in review. Three AZs gives MSK the
# quorum spread it wants and lets Aurora and Redshift sit in a separate subnet
# tier.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${local.name_prefix}-vpc"
  }
}

# ── Subnets ──────────────────────────────────────────────────────────────────
# public   x/24      ALB + NAT gateways
# private  x+10/24   ECS tasks + MSK brokers
# database x+20/24   Aurora + Redshift (no internet route dependencies)

resource "aws_subnet" "public" {
  count = var.az_count

  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(var.vpc_cidr, 8, count.index)
  availability_zone       = local.azs[count.index]
  map_public_ip_on_launch = false

  tags = {
    Name = "${local.name_prefix}-public-${local.azs[count.index]}"
    Tier = "public"
  }
}

resource "aws_subnet" "private" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 10)
  availability_zone = local.azs[count.index]

  tags = {
    Name = "${local.name_prefix}-private-${local.azs[count.index]}"
    Tier = "private"
  }
}

resource "aws_subnet" "database" {
  count = var.az_count

  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(var.vpc_cidr, 8, count.index + 20)
  availability_zone = local.azs[count.index]

  tags = {
    Name = "${local.name_prefix}-database-${local.azs[count.index]}"
    Tier = "database"
  }
}

# ── Internet egress ──────────────────────────────────────────────────────────

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id

  tags = {
    Name = "${local.name_prefix}-igw"
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = {
    Name = "${local.name_prefix}-public"
  }
}

resource "aws_route_table_association" "public" {
  count = var.az_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = {
    Name = "${local.name_prefix}-nat-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

resource "aws_nat_gateway" "main" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  tags = {
    Name = "${local.name_prefix}-nat-${count.index}"
  }

  depends_on = [aws_internet_gateway.main]
}

# One private route table per AZ. With single_nat_gateway all of them point at
# NAT[0]; otherwise each points at its own NAT for AZ-local egress.
resource "aws_route_table" "private" {
  count = var.az_count

  vpc_id = aws_vpc.main.id

  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[min(count.index, local.nat_gateway_count - 1)].id
  }

  tags = {
    Name = "${local.name_prefix}-private-${local.azs[count.index]}"
  }
}

resource "aws_route_table_association" "private" {
  count = var.az_count

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

resource "aws_route_table_association" "database" {
  count = var.az_count

  subnet_id      = aws_subnet.database[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# ── VPC endpoints ────────────────────────────────────────────────────────────
# S3 is a free Gateway endpoint and offloads the biggest data path (the lake)
# from NAT. The Interface endpoints are opt-in because each one costs ~$7/mo
# per AZ; NAT already provides a working path.

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = aws_route_table.private[*].id

  tags = {
    Name = "${local.name_prefix}-s3-gateway"
  }
}

resource "aws_vpc_endpoint" "interface" {
  for_each = var.enable_interface_endpoints ? toset([
    "ecr.api",
    "ecr.dkr",
    "secretsmanager",
    "logs",
    "sts",
    "kms",
  ]) : toset([])

  vpc_id              = aws_vpc.main.id
  service_name        = "com.amazonaws.${var.aws_region}.${each.value}"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.vpc_endpoints.id]
  private_dns_enabled = true

  tags = {
    Name = "${local.name_prefix}-${replace(each.value, ".", "-")}"
  }
}

# ── Security groups ──────────────────────────────────────────────────────────
# Ingress rules are separate aws_vpc_security_group_ingress_rule resources.
# The ALB group's egress is an inline block because it must reference the ECS
# group; keeping ingress out of the group resources is what avoids an ALB<->ECS
# dependency cycle. The other groups declare their allow-all egress inline too,
# which also removes the allow-all rule AWS attaches to every new group.

resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb"
  description = "Public ingress for the collector live API"
  vpc_id      = aws_vpc.main.id

  # Scoped egress: the ALB only ever speaks to collector tasks. Declaring egress
  # inline also removes the allow-all egress rule AWS adds to a new group, so
  # this group is genuinely least-privilege rather than default-plus-a-rule.
  egress {
    description     = "Forward to collector tasks"
    from_port       = var.collector_container_port
    to_port         = var.collector_container_port
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs.id]
  }

  tags = {
    Name = "${local.name_prefix}-alb"
  }
}

resource "aws_security_group" "ecs" {
  name        = "${local.name_prefix}-ecs"
  description = "Collector and lake ECS tasks"
  vpc_id      = aws_vpc.main.id

  # ECS tasks need broad egress: upstream APIs via NAT, MSK, S3, ECR, Secrets
  # Manager. The default allow-all is made explicit here.
  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-ecs"
  }
}

resource "aws_security_group" "msk" {
  name        = "${local.name_prefix}-msk"
  description = "MSK brokers; only the ECS tasks may connect"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-msk"
  }
}

resource "aws_security_group" "aurora" {
  name        = "${local.name_prefix}-aurora"
  description = "Aurora PostgreSQL serving cluster"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-aurora"
  }
}

resource "aws_security_group" "redshift" {
  name        = "${local.name_prefix}-redshift"
  description = "Redshift Serverless workgroup"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-redshift"
  }
}

resource "aws_security_group" "vpc_endpoints" {
  name        = "${local.name_prefix}-vpc-endpoints"
  description = "Interface VPC endpoints"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All egress"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = {
    Name = "${local.name_prefix}-vpc-endpoints"
  }
}

# ALB: public 80/443 in, collector port out.
resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from anywhere"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from anywhere"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# ECS: accept from the ALB, unrestricted egress (upstream APIs via NAT,
# MSK, S3, ECR, Secrets Manager).
resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "Live API from the ALB"
  ip_protocol                  = "tcp"
  from_port                    = var.collector_container_port
  to_port                      = var.collector_container_port
  referenced_security_group_id = aws_security_group.alb.id
}

# MSK: SCRAM/TLS (9096) from ECS and broker-to-broker.
resource "aws_vpc_security_group_ingress_rule" "msk_from_ecs" {
  security_group_id            = aws_security_group.msk.id
  description                  = "SASL/SCRAM over TLS from ECS tasks"
  ip_protocol                  = "tcp"
  from_port                    = local.msk_client_port
  to_port                      = local.msk_client_port
  referenced_security_group_id = aws_security_group.ecs.id
}

resource "aws_vpc_security_group_ingress_rule" "msk_internal" {
  security_group_id            = aws_security_group.msk.id
  description                  = "Broker replication within the cluster"
  ip_protocol                  = "tcp"
  from_port                    = local.msk_client_port
  to_port                      = local.msk_client_port
  referenced_security_group_id = aws_security_group.msk.id
}

# Aurora: Postgres from ECS only (the lake publishes, the reader serves).
resource "aws_vpc_security_group_ingress_rule" "aurora_from_ecs" {
  security_group_id            = aws_security_group.aurora.id
  description                  = "PostgreSQL from ECS tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
  referenced_security_group_id = aws_security_group.ecs.id
}

# Redshift: the Postgres-wire port from ECS.
resource "aws_vpc_security_group_ingress_rule" "redshift_from_ecs" {
  security_group_id            = aws_security_group.redshift.id
  description                  = "Redshift from ECS tasks"
  ip_protocol                  = "tcp"
  from_port                    = 5439
  to_port                      = 5439
  referenced_security_group_id = aws_security_group.ecs.id
}

# Interface endpoints: HTTPS from inside the VPC.
resource "aws_vpc_security_group_ingress_rule" "vpc_endpoints_https" {
  security_group_id = aws_security_group.vpc_endpoints.id
  description       = "HTTPS from the VPC"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = var.vpc_cidr
}

# main.tf
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

#############################
# VPC + Subnets (minimal but production-ready)
#############################
data "aws_availability_zones" "available" {}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags = { Name = "restaurant-vpc" }
}

resource "aws_subnet" "public" {
  count                   = 2
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags = { Name = "public-subnet-${count.index}" }
}

resource "aws_subnet" "private" {
  count             = 2
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags = { Name = "private-subnet-${count.index}" }
}

resource "aws_internet_gateway" "gw" {
  vpc_id = aws_vpc.main.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.gw.id
  }
}

resource "aws_route_table_association" "public" {
  count          = 2
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

#############################
# ECR Repository
#############################
resource "aws_ecr_repository" "streamlit" {
  name                 = "restaurant-streamlit"
  image_tag_mutability = "MUTABLE"
}

#############################
# RDS PostgreSQL (in private subnets)
#############################
resource "aws_security_group" "rds" {
  name   = "rds-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.ecs_tasks.id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "main"
  subnet_ids = aws_subnet.private[*].id
}

resource "aws_db_instance" "postgres" {
  identifier              = "restaurant-db"
  engine                  = "postgres"
  engine_version          = "16"
  instance_class          = "db.t4g.medium"  # or db.t3.micro for free tier
  allocated_storage       = 20
  storage_type            = "gp3"
  db_name                 = "restaurantdb"
  username                = "postgres"
  password                = var.db_password
  db_subnet_group_name    = aws_db_subnet_group.main.name
  vpc_security_group_ids  = [aws_security_group.rds.id]
  publicly_accessible     = false
  skip_final_snapshot     = true
  backup_retention_period = 7
}

#############################
# ACM Certificate + ALB
#############################
resource "aws_acm_certificate" "cert" {
  domain_name       = var.domain_name
  validation_method = "DNS"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_lb" "main" {
  name               = "restaurant-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id
}

resource "aws_security_group" "alb" {
  name   = "alb-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_lb_target_group" "streamlit" {
  name        = "streamlit-tg"
  port        = 8501
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip"

  health_check {
    path                = "/"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-2016-08"
  certificate_arn   = aws_acm_certificate.cert.arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.streamlit.arn
  }
}

resource "aws_lb_listener" "http_redirect" {
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
}

#############################
# ECS Cluster + Fargate Service
#############################
resource "aws_ecs_cluster" "main" {
  name = "restaurant-cluster"
}

resource "aws_security_group" "ecs_tasks" {
  name   = "ecs-tasks-sg"
  vpc_id = aws_vpc.main.id

  ingress {
    from_port       = 8501
    to_port         = 8501
    protocol        = "tcp"
    security_groups = [aws_security_group.alb.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_ecs_task_definition" "streamlit" {
  family                   = "restaurant-task"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = "1024"  # 1 vCPU
  memory                   = "3072"  # 5630 MB

  container_definitions = jsonencode([{
    name      = "streamlit"
    image     = "${aws_ecr_repository.streamlit.repository_url}:latest"
    essential = true
    portMappings = [{
      containerPort = 8501
      hostPort      = 8501
      protocol      = "tcp"
    }]

    environment = [
      { name = "DB_HOST", value = aws_db_instance.postgres.address },
      { name = "DB_PORT", value = "5432" },
      { name = "DB_NAME", value = aws_db_instance.postgres.db_name },
      { name = "DB_USER", value = aws_db_instance.postgres.username }
    ]

    secrets = [
      {
        name      = "DB_PASSWORD"
        valueFrom = aws_ssm_parameter.db_password.arn
      }
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.streamlit.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "streamlit"
      }
    }
  }])
}

resource "aws_ecs_service" "main" {
  name            = "restaurant-service"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.streamlit.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.private[*].id
    security_groups  = [aws_security_group.ecs_tasks.id]
    assign_public_ip = false
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.streamlit.arn
    container_name   = "streamlit"
    container_port   = 8501
  }

  depends_on = [aws_lb_listener.https]
}

#############################
# Secrets & Logs
#############################
resource "aws_ssm_parameter" "db_password" {
  name        = "/restaurant/db/password"
  type        = "SecureString"
  value       = var.db_password
  description = "PostgreSQL password"
}

resource "aws_cloudwatch_log_group" "streamlit" {
  name              = "/ecs/restaurant-streamlit"
  retention_in_days = 30
}

#############################
# variables.tf
#############################
variable "aws_region" {
  default = "us-east-1"
}

variable "domain_name" {
  description = "Your domain (e.g., app.yourcompany.com)"
  type        = string
}

variable "db_password" {
  description = "Strong password for PostgreSQL"
  type        = string
  sensitive   = true
}

#############################
# outputs.tf
#############################
output "app_url" {
  value = "https://${var.domain_name}"
}

output "ecr_repository_url" {
  value = aws_ecr_repository.streamlit.repository_url
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.endpoint
}

output "alb_dns_name" {
  value = aws_lb.main.dns_name
}
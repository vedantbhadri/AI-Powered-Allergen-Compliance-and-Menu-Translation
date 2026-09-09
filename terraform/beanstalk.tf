# Zips the ../app directory (the Flask application) at plan/apply time so
# `terraform apply` alone deploys working code - no separate `eb deploy` /
# manual zip-and-upload step needed.
data "archive_file" "app_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../app"
  output_path = "${path.module}/build/app.zip"
  excludes = [
    ".venv", "__pycache__", "*.pyc", ".DS_Store",
  ]
}

resource "aws_s3_object" "app_bundle" {
  bucket = aws_s3_bucket.eb_app_versions.id
  key    = "app-bundles/app-${data.archive_file.app_zip.output_md5}.zip"
  source = data.archive_file.app_zip.output_path
  etag   = data.archive_file.app_zip.output_md5
}

# Latest Python 3.12 solution stack on Amazon Linux 2023.
data "aws_elastic_beanstalk_solution_stack" "python" {
  most_recent = true
  name_regex  = var.python_version_regex
}

resource "aws_elastic_beanstalk_application" "app" {
  name        = "${local.name_prefix}-app"
  description = "AI-powered allergen compliance & menu translation - Flask app on Elastic Beanstalk"

  # Ensures old application versions don't block `terraform destroy`.

  tags = local.common_tags
}

resource "aws_elastic_beanstalk_application_version" "app_version" {
  name        = "app-${data.archive_file.app_zip.output_md5}"
  application = aws_elastic_beanstalk_application.app.name
  bucket      = aws_s3_bucket.eb_app_versions.id
  key         = aws_s3_object.app_bundle.key

  # NOTE: force_delete lives on aws_elastic_beanstalk_application (below),
  # not on this resource - Terraform will delete this version automatically
  # as part of that when you `terraform destroy`.

  tags = local.common_tags
}

resource "aws_elastic_beanstalk_environment" "env" {
  name                = "${local.name_prefix}-env"
  application         = aws_elastic_beanstalk_application.app.name
  solution_stack_name = data.aws_elastic_beanstalk_solution_stack.python.name
  version_label       = aws_elastic_beanstalk_application_version.app_version.name
  tags                = local.common_tags


  # ---- explicit VPC/subnets (see network.tf) ----
  # Prevents EB from guessing subnets across AZs that may be listed for the
  # account but have no actual default subnet (the cause of the "no default
  # subnet for availability zone" CREATE_FAILED error).
  setting {
    namespace = "aws:ec2:vpc"
    name      = "VPCId"
    value     = data.aws_vpc.default.id
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "Subnets"
    value     = join(",", data.aws_subnets.default.ids)
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "ELBSubnets"
    value     = join(",", data.aws_subnets.default.ids)
  }

  setting {
    namespace = "aws:ec2:vpc"
    name      = "AssociatePublicIpAddress"
    value     = "true"
  }

  # ---- instance / scaling ----
  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "IamInstanceProfile"
    value     = aws_iam_instance_profile.eb_ec2_profile.name
  }

  setting {
    namespace = "aws:autoscaling:launchconfiguration"
    name      = "InstanceType"
    value     = var.instance_type
  }

  setting {
    namespace = "aws:autoscaling:asg"
    name      = "MinSize"
    value     = var.min_instances
  }

  setting {
    namespace = "aws:autoscaling:asg"
    name      = "MaxSize"
    value     = var.max_instances
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "ServiceRole"
    value     = aws_iam_role.eb_service_role.arn
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment"
    name      = "LoadBalancerType"
    value     = "application"
  }

  setting {
    namespace = "aws:elasticbeanstalk:environment:process:default"
    name      = "HealthCheckPath"
    value     = "/health"
  }

  setting {
    namespace = "aws:elasticbeanstalk:healthreporting:system"
    name      = "SystemType"
    value     = "enhanced"
  }

  # ---- app environment variables (read by app/services/*.py) ----
  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "AWS_REGION"
    value     = var.aws_region
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "S3_BUCKET"
    value     = aws_s3_bucket.menu_uploads.bucket
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "DYNAMODB_TABLE"
    value     = aws_dynamodb_table.menu_items.name
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "BEDROCK_MODEL_ID"
    value     = var.bedrock_model_id
  }

  setting {
    namespace = "aws:elasticbeanstalk:application:environment"
    name      = "LOCAL_MODE"
    value     = "false"
  }
}

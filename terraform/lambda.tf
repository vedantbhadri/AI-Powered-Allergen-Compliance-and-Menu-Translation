# readMenu + editMenu Lambda functions and the shared services layer.
#
# The layer holds byte-for-byte copies of app/services/*.py (see
# build/build_layer.py) so both functions import them as `from services import ...`
# without the modules drifting. Both function packages contain only handler.py;
# their co-located tests and caches are excluded from the deployment zips.
#
# NOTE: the `role` attributes below reference IAM roles defined by task 9.2
# (aws_iam_role.read_menu_exec / aws_iam_role.edit_menu_exec). Until that task
# lands, `terraform validate` reports those two references as undeclared - that
# is expected and is the only outstanding error for this task.

# --- Shared services layer -------------------------------------------------

data "archive_file" "layer_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../build/layer"
  output_path = "${path.module}/build/layer.zip"
}

resource "aws_lambda_layer_version" "services" {
  layer_name          = "${local.name_prefix}-services"
  filename            = data.archive_file.layer_zip.output_path
  source_code_hash    = data.archive_file.layer_zip.output_base64sha256
  compatible_runtimes = ["python3.12"]
}

# --- Function packages (handler.py only) -----------------------------------

data "archive_file" "read_menu_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../build/read_menu"
  output_path = "${path.module}/build/read_menu.zip"

  excludes = [
    "test_*.py",
    ".pytest_cache",
    ".hypothesis",
    "__pycache__",
  ]
}

data "archive_file" "edit_menu_zip" {
  type        = "zip"
  source_dir  = "${path.module}/../build/edit_menu"
  output_path = "${path.module}/build/edit_menu.zip"

  excludes = [
    "test_*.py",
    ".pytest_cache",
    ".hypothesis",
    "__pycache__",
  ]
}

# --- Lambda functions ------------------------------------------------------

resource "aws_lambda_function" "read_menu" {
  function_name    = "${local.name_prefix}-read-menu"
  role             = aws_iam_role.read_menu_exec.arn # defined by task 9.2
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.read_menu_zip.output_path
  source_code_hash = data.archive_file.read_menu_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.services.arn]

  environment {
    variables = {
      MENU_TABLE_NAME = aws_dynamodb_table.menu_items.name
    }
  }

  tags = local.common_tags
}

resource "aws_lambda_function" "edit_menu" {
  function_name    = "${local.name_prefix}-edit-menu"
  role             = aws_iam_role.edit_menu_exec.arn # defined by task 9.2
  runtime          = "python3.12"
  handler          = "handler.handler"
  filename         = data.archive_file.edit_menu_zip.output_path
  source_code_hash = data.archive_file.edit_menu_zip.output_base64sha256
  layers           = [aws_lambda_layer_version.services.arn]

  environment {
    variables = {
      MENU_TABLE_NAME = aws_dynamodb_table.menu_items.name
    }
  }

  tags = local.common_tags
}

# --- Outputs ---------------------------------------------------------------
# These logically belong in outputs.tf; kept here to avoid editing that file
# while task 9.1 is in flight. Move them alongside the other outputs later if
# preferred.

output "read_menu_function_name" {
  description = "Name of the readMenu Lambda function."
  value       = aws_lambda_function.read_menu.function_name
}

output "edit_menu_function_name" {
  description = "Name of the editMenu Lambda function."
  value       = aws_lambda_function.edit_menu.function_name
}

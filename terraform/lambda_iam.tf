# ---------------------------------------------------------------------------
# Scoped IAM execution roles for the readMenu and editMenu Lambda functions
# (see lambda.tf, task 9.1). These are deliberately kept separate from the
# Beanstalk roles in iam.tf so the two concerns merge cleanly.
#
# The whole point of this file is infrastructure-level least privilege:
#   - readMenu physically CANNOT write (no PutItem/UpdateItem/DeleteItem).
#   - editMenu physically CANNOT read or put (no GetItem/Query/PutItem);
#     it may only UpdateItem.
# Each role also gets the minimal CloudWatch Logs baseline, scoped to its own
# log group ARN rather than "*".
# ---------------------------------------------------------------------------

# Shared trust policy: both functions are assumed by the Lambda service.
data "aws_iam_policy_document" "lambda_assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    effect  = "Allow"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# Log-group ARNs composed from the account id + provider region, matching the
# Lambda function names in lambda.tf (${local.name_prefix}-read-menu / -edit-menu).
locals {
  read_menu_log_group_arn  = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-read-menu:*"
  edit_menu_log_group_arn  = "arn:aws:logs:${var.aws_region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/${local.name_prefix}-edit-menu:*"
}

# ---------------------------------------------------------------------------
# readMenu execution role - read-only on the menu-items table.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "read_menu_exec" {
  name               = "${local.name_prefix}-read-menu-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "read_menu_permissions" {
  # Read-only access to the menu-items table (base table + any indexes).
  # GetItem + Query back GET /menus/{restaurantId} and the status route; Scan is
  # required by list_menus() for GET /restaurants (list all restaurant/menu IDs).
  # No PutItem / UpdateItem / DeleteItem is granted anywhere.
  statement {
    sid    = "MenuItemsReadOnly"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:Query",
      "dynamodb:Scan",
    ]
    resources = [
      aws_dynamodb_table.menu_items.arn,
      "${aws_dynamodb_table.menu_items.arn}/index/*",
    ]
  }

  # CloudWatch Logs baseline, scoped to this function's own log group.
  statement {
    sid    = "ReadMenuLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [local.read_menu_log_group_arn]
  }
}

resource "aws_iam_policy" "read_menu_permissions" {
  name   = "${local.name_prefix}-read-menu-permissions"
  policy = data.aws_iam_policy_document.read_menu_permissions.json
}

resource "aws_iam_role_policy_attachment" "read_menu_permissions_attach" {
  role       = aws_iam_role.read_menu_exec.name
  policy_arn = aws_iam_policy.read_menu_permissions.arn
}

# ---------------------------------------------------------------------------
# editMenu execution role - UpdateItem only on the menu-items table.
# ---------------------------------------------------------------------------
resource "aws_iam_role" "edit_menu_exec" {
  name               = "${local.name_prefix}-edit-menu-exec"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume_role.json
  tags               = local.common_tags
}

data "aws_iam_policy_document" "edit_menu_permissions" {
  # The single write action editMenu is allowed. No GetItem, no Query, no
  # PutItem, no DeleteItem - the handler persists via a conditional UpdateItem.
  statement {
    sid    = "MenuItemsUpdateOnly"
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
    ]
    resources = [
      aws_dynamodb_table.menu_items.arn,
    ]
  }

  # CloudWatch Logs baseline, scoped to this function's own log group.
  statement {
    sid    = "EditMenuLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = [local.edit_menu_log_group_arn]
  }
}

resource "aws_iam_policy" "edit_menu_permissions" {
  name   = "${local.name_prefix}-edit-menu-permissions"
  policy = data.aws_iam_policy_document.edit_menu_permissions.json
}

resource "aws_iam_role_policy_attachment" "edit_menu_permissions_attach" {
  role       = aws_iam_role.edit_menu_exec.name
  policy_arn = aws_iam_policy.edit_menu_permissions.arn
}

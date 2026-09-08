# API Gateway HTTP API (v2) fronting the readMenu and editMenu Lambda functions.
#
# Routes (route keys must match the handlers' routeKey dispatch exactly):
#   GET   /menus/{restaurantId}          -> read_menu
#   GET   /menus/{uploadId}/status       -> read_menu
#   GET   /restaurants                   -> read_menu
#   PATCH /menus/{menuId}/items/{itemId} -> edit_menu  (Cognito JWT required)
#
# Authentication (Requirement 7): ONLY the PATCH editMenu route requires a valid
# Cognito-issued JWT, enforced by a JWT authorizer at the API Gateway BEFORE the
# edit_menu Lambda is ever invoked. The three GET read_menu routes remain fully
# PUBLIC (authorization_type NONE) so customers can browse menus and poll upload
# status without logging in. A single shared admin account (see
# aws_cognito_user.admin in cognito.tf) is the only credential.
#
# Both functions are wired through AWS_PROXY (payload format 2.0) integrations.
# read_menu is targeted by three routes but a single lambda permission with a
# /*/* wildcard source_arn covers every method+route under this API, so we do
# not create duplicate permission statements per route.
#
# NOTE: aws_lambda_function.read_menu / .edit_menu are defined in lambda.tf
# (task 9.1).

# --- HTTP API --------------------------------------------------------------

resource "aws_apigatewayv2_api" "menu_api" {
  name          = "${local.name_prefix}-menu-api"
  protocol_type = "HTTP"

  tags = local.common_tags
}

# --- Integrations (AWS_PROXY, payload format 2.0) --------------------------

resource "aws_apigatewayv2_integration" "read_menu" {
  api_id                 = aws_apigatewayv2_api.menu_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.read_menu.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "edit_menu" {
  api_id                 = aws_apigatewayv2_api.menu_api.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.edit_menu.invoke_arn
  payload_format_version = "2.0"
}

# --- JWT authorizer (Requirement 7) ----------------------------------------
# Validates Cognito-issued access tokens against the staff user pool. Attached
# ONLY to the PATCH editMenu route below - the GET routes stay public. The
# audience must be the app client id, and the issuer is the pool's Cognito IdP
# URL. Enforcement happens here at the gateway, so unauthenticated PATCH calls
# are rejected (401/403) before edit_menu runs.
resource "aws_apigatewayv2_authorizer" "admin_jwt" {
  api_id           = aws_apigatewayv2_api.menu_api.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name_prefix}-admin-jwt"

  jwt_configuration {
    audience = [aws_cognito_user_pool_client.staff_pool_client.id]
    issuer   = "https://cognito-idp.${var.aws_region}.amazonaws.com/${aws_cognito_user_pool.staff_pool.id}"
  }
}

# --- Routes ----------------------------------------------------------------

resource "aws_apigatewayv2_route" "get_menu_by_restaurant" {
  api_id    = aws_apigatewayv2_api.menu_api.id
  route_key = "GET /menus/{restaurantId}"
  target    = "integrations/${aws_apigatewayv2_integration.read_menu.id}"
}

resource "aws_apigatewayv2_route" "get_upload_status" {
  api_id    = aws_apigatewayv2_api.menu_api.id
  route_key = "GET /menus/{uploadId}/status"
  target    = "integrations/${aws_apigatewayv2_integration.read_menu.id}"
}

# GET /restaurants reuses the SAME read_menu integration (no new Lambda function
# and no new integration). The read_menu aws_lambda_permission below already uses
# a /*/* wildcard source_arn covering every method+route under this API, so this
# third read_menu route needs no additional permission statement.
resource "aws_apigatewayv2_route" "list_restaurants" {
  api_id    = aws_apigatewayv2_api.menu_api.id
  route_key = "GET /restaurants"
  target    = "integrations/${aws_apigatewayv2_integration.read_menu.id}"
}

# The ONLY authenticated route: the Cognito JWT authorizer guards editMenu.
# The three GET routes above are intentionally left public (default NONE).
resource "aws_apigatewayv2_route" "patch_menu_item" {
  api_id             = aws_apigatewayv2_api.menu_api.id
  route_key          = "PATCH /menus/{menuId}/items/{itemId}"
  target             = "integrations/${aws_apigatewayv2_integration.edit_menu.id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.admin_jwt.id
}

# --- Stage (auto-deployed default stage) -----------------------------------

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.menu_api.id
  name        = "$default"
  auto_deploy = true

  tags = local.common_tags
}

# --- Lambda invoke permissions ---------------------------------------------
# One statement per function. The /*/* wildcard source_arn covers every
# method + route under this API, so read_menu's three routes share a single
# permission (no duplicate statement ids).

resource "aws_lambda_permission" "read_menu_apigw" {
  statement_id  = "AllowInvokeFromMenuApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.read_menu.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.menu_api.execution_arn}/*/*"
}

resource "aws_lambda_permission" "edit_menu_apigw" {
  statement_id  = "AllowInvokeFromMenuApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.edit_menu.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.menu_api.execution_arn}/*/*"
}

# --- Output ----------------------------------------------------------------

output "menu_api_invoke_url" {
  description = "Base invoke URL for the menu HTTP API (readMenu + editMenu routes)."
  value       = aws_apigatewayv2_stage.default.invoke_url
}

# "Amazon Cognito: User authentication and access management across all
#  35 location branches" - provisioned here to cover that item from the
# architecture proposal. NOTE: the demo Flask app/UI does NOT currently
# enforce a Cognito login (kept out of scope so you can test the AI
# pipeline immediately) - this pool is ready to wire in via
# flask-cognito / API Gateway + a Cognito authorizer when you're ready
# to lock the dashboard down for real restaurant-staff use across the
# 35 branches.
resource "aws_cognito_user_pool" "staff_pool" {
  name = "${local.name_prefix}-staff-pool"

  password_policy {
    minimum_length    = 10
    require_lowercase = true
    require_uppercase = true
    require_numbers   = true
    require_symbols   = false
  }

  tags = local.common_tags
}

resource "aws_cognito_user_pool_client" "staff_pool_client" {
  name         = "${local.name_prefix}-staff-client"
  user_pool_id = aws_cognito_user_pool.staff_pool.id

  explicit_auth_flows = [
    "ALLOW_USER_PASSWORD_AUTH",
    "ALLOW_REFRESH_TOKEN_AUTH",
  ]
}

# --- Shared editMenu admin account (Requirement 7) -------------------------
# The single shared admin account that guards the PATCH editMenu route via the
# JWT authorizer in apigateway.tf. This is the first real use of the staff pool.
# There is exactly ONE admin credential (shared) - restaurants are chosen via
# the menu_id path param, NOT per-restaurant logins. The staff pool defaults to
# admin-only user creation (no self-signup is enabled anywhere), which is what we
# want for a locked-down single-credential demo login.
#
# We set a PERMANENT password directly via the `password` argument so the
# credential is immediately usable for ALLOW_USER_PASSWORD_AUTH - no
# temporary-password / FORCE_CHANGE_PASSWORD or message_action delivery flow.
resource "aws_cognito_user" "admin" {
  user_pool_id = aws_cognito_user_pool.staff_pool.id
  username     = var.admin_username

  # Permanent, ready-to-use password (not a temporary one). Supplied via
  # var.admin_password (sensitive, no default - see variables.tf).
  password = var.admin_password
}

# --- Outputs (kept local to this file, matching how lambda.tf keeps its ---
# outputs beside the resources they describe). cognito_user_pool_id is already
# exported from outputs.tf; the client id is added here because the JWT
# authorizer audience needs it and it is required to obtain a login token.
output "cognito_staff_pool_client_id" {
  description = "App client id for the staff user pool - used as the JWT authorizer audience and to obtain the shared admin login token."
  value       = aws_cognito_user_pool_client.staff_pool_client.id
}

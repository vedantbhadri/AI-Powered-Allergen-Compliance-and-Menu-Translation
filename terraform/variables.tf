variable "aws_region" {
  description = "AWS region to deploy into. Must be a region where Bedrock and Textract are both available."
  type        = string
  default     = "ap-southeast-2" # Sydney - closest Bedrock-enabled region to NZ at time of writing
}

variable "project_name" {
  description = "Short name used as a prefix for all resources."
  type        = string
  default     = "allergen-demo"
}

variable "environment" {
  description = "Environment name tag (dev/test/prod)."
  type        = string
  default     = "dev"
}

variable "instance_type" {
  description = "EC2 instance type for the Beanstalk environment."
  type        = string
  default     = "t3.small"
}

variable "min_instances" {
  type    = number
  default = 1
}

variable "max_instances" {
  type    = number
  default = 2
}

variable "bedrock_model_id" {
  description = "Bedrock model ID used for allergen extraction + translation."
  type        = string
  default     = "anthropic.claude-3-haiku-20240307-v1:0"
}

variable "python_version_regex" {
  description = "Regex used to pick the Elastic Beanstalk Python solution stack."
  type        = string
  default     = "^64bit Amazon Linux 2023.*Python 3\\.12$"
}

# --- Shared editMenu admin login (Requirement 7) ---------------------------
# A single shared admin account guards ONLY the PATCH editMenu route. There is
# no per-restaurant login: restaurants are selected via the menu_id path param,
# not separate identities. See cognito.tf (aws_cognito_user.admin) and the JWT
# authorizer on aws_apigatewayv2_route.patch_menu_item in apigateway.tf.

variable "admin_username" {
  description = "Username for the single shared editMenu admin login (Cognito user in the staff pool)."
  type        = string
  default     = "admin"
}

variable "admin_password" {
  description = "Password for the single shared editMenu admin login. Supply via a .tfvars file or TF_VAR_admin_password env var; there is intentionally no default so the secret is never committed. Must satisfy the staff_pool password policy (>=10 chars, upper+lower+number)."
  type        = string
  sensitive   = true
}

variable "create_knowledge_base" {
  description = "OPT-IN: provision the Bedrock Knowledge Base for the compliance RAG layer (see bedrock_kb.tf). Off by default so the demo stack is unchanged."
  type        = bool
  default     = false
}

variable "knowledge_base_id" {
  description = "Bedrock Knowledge Base id the app retrieves from for compliance verification. Empty = app runs rules-only (with local keyword retrieval over bundled docs/)."
  type        = string
  default     = ""
}

variable "bedrock_embedding_model_arn" {
  description = "Bedrock embedding model ARN used to index the knowledge base. Default is Amazon Titan Text Embeddings v2 in the default region (ap-southeast-2); change if you deploy elsewhere."
  type        = string
  default     = "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.titan-embed-text-v2:0"
}

variable "aws_profile" {
  description = "AWS CLI named profile to use for credentials. Set to \"default\" if you configured credentials without a named profile."
  type        = string
  default     = "default"
}

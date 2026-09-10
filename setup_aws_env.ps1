# AWS IAM user environment setup script.
# Uses long-term AKIA-prefixed access keys (no SSO login required).

# ================================================
# Core configuration
# ================================================
# Per-service regions. The deployment is split across two regions:
#   * DynamoDB / S3 / Textract live in us-east-1 (Elastic Beanstalk stack)
#   * Bedrock (inference profile) + Knowledge Base live in ap-southeast-2
# A single AWS_REGION cannot serve both, so each service reads its own
# override (falling back to AWS_REGION). LocalMode uses these too.
$env:AWS_REGION          = "ap-southeast-2"   # general default
$env:BEDROCK_REGION      = "ap-southeast-2"   # Bedrock + KB region
$env:KB_REGION           = "ap-southeast-2"   # Knowledge Base region
$env:DYNAMODB_REGION     = "us-east-1"        # DynamoDB table region
$env:S3_REGION           = "us-east-1"        # menu uploads bucket region
$env:TEXTRACT_REGION     = "us-east-1"        # Textract region

# Resource names (from terraform, confirmed present in AWS)
$env:S3_BUCKET           = "allergen-demo-dev-menu-uploads-669232219904"
$env:DYNAMODB_TABLE      = "allergen-demo-dev-menu-items"

# Bedrock Knowledge Base for the compliance RAG layer (aws engine).
# Empty/unset = RAG silently degrades to local docs/ search.
$env:KNOWLEDGE_BASE_ID   = "CBFZTLLUHU"

$env:LOCAL_MODE = "false"
# Bedrock model = inference profile ID (NOT the bare foundation-model id).
# Converse/InvokeModel in this account/region rejects the bare id
# ("anthropic.claude-opus-4-6-v1") with ValidationException; the au.* /
# global.* profile ids are the callable form. Keep au.* for lowest latency.
$env:BEDROCK_MODEL_ID = "au.anthropic.claude-opus-4-6-v1"

# ================================================
# IAM user configuration guide
# ================================================
Write-Host "=== AWS IAM user configuration ==="
Write-Host ""

# Check credential status
Write-Host "1. Checking AWS credentials..."
$identity = aws sts get-caller-identity 2>&1
if ($LASTEXITCODE -eq 0) {
    $identity_obj = $identity | ConvertFrom-Json
    Write-Host "   [OK] AWS credentials are valid" -ForegroundColor Green
    Write-Host "       Account: $($identity_obj.Account)"
    Write-Host "       User: $($identity_obj.UserId)"

    # Check whether it is an IAM user
    if ($identity_obj.Arn -like "*:user/*") {
        Write-Host "      [OK] Using an IAM user (long-term credentials)" -ForegroundColor Green
    } else {
        Write-Host "      [WARN] Not an IAM user ARN" -ForegroundColor Yellow
        Write-Host "      Current ARN: $($identity_obj.Arn)"
    }
} else {
    Write-Host "   [WARN] AWS credentials not configured or invalid" -ForegroundColor Yellow
    Write-Host "       Error: $identity"
}

Write-Host ""

# IAM user setup steps
Write-Host "2. IAM user setup steps:"
Write-Host ""
Write-Host "   A. Create an IAM user and access key"
Write-Host "      1. Sign in to the AWS console (https://console.aws.amazon.com)"
Write-Host "      2. Go to the IAM service"
Write-Host "      3. Click 'Users' -> 'Create user'"
Write-Host "      4. Username: allergen-system-dev (or any name)"
Write-Host "      5. Access type: check 'Access key - Programmatic access'"
Write-Host "      6. Permissions: attach the AdministratorAccess policy"
Write-Host "      7. Save the generated Access Key ID (starts with AKIA) and Secret Access Key"
Write-Host ""
Write-Host "   B. Configure the AWS CLI"
Write-Host "      aws configure"
Write-Host "      Enter the following:"
Write-Host "      - AWS Access Key ID: [your AKIA...]"
Write-Host "      - AWS Secret Access Key: [your secret]"
Write-Host "      - Default region name: ap-southeast-2"
Write-Host "      - Default output format: json"
Write-Host ""
Write-Host "   C. Verify the configuration"
Write-Host "      aws sts get-caller-identity"
Write-Host "      Should print your IAM user info"
Write-Host ""

# Check Bedrock access
Write-Host "3. Checking Bedrock access..."
try {
    $model_count = aws bedrock list-foundation-models --region $env:AWS_REGION --query "length(modelSummaries)" --output text 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   [OK] Bedrock access works" -ForegroundColor Green
        Write-Host "       Available model count: $model_count"

        # Show Claude models
        $claude_models = aws bedrock list-foundation-models --region $env:AWS_REGION --query "modelSummaries[?contains(modelId, 'claude')].modelId" --output text 2>&1
        if ($claude_models) {
            Write-Host "      Claude models:"
            foreach ($model in ($claude_models -split "`n")) {
                Write-Host "        - $model"
            }
        }
    } else {
        Write-Host "   [FAIL] Bedrock access failed" -ForegroundColor Red
        Write-Host "       Make sure:"
        Write-Host "       1. The IAM user has Bedrock permissions"
        Write-Host "       2. Bedrock model access is enabled in the AWS console"
        Write-Host "       3. The current region (ap-southeast-2) supports Claude models"
    }
} catch {
    Write-Host "   [FAIL] Bedrock check failed" -ForegroundColor Red
    Write-Host "       Error: $_"
}

Write-Host ""

# App startup instructions
Write-Host "=== Starting the app (real AWS mode, default) ==="
Write-Host ""
Write-Host "1. Start:"
Write-Host "  cd app"
Write-Host "  .\.venv\Scripts\python.exe application.py"
Write-Host ""
Write-Host "2. Open: http://localhost:8000"
Write-Host ""
Write-Host "Local offline mode (no AWS, for development):"
Write-Host "  $env:LOCAL_MODE = 'true'"
Write-Host "  cd app"
Write-Host "  .\.venv\Scripts\python.exe application.py"

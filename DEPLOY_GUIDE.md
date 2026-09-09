# Deployment Guide - Terraform Deployment to AWS

This document provides a complete deployment guide, focusing on deploying the application to AWS Elastic Beanstalk and related services via Terraform.

## Deployment Prerequisites

Before starting deployment, ensure the following conditions are met:

### 1. Tool Requirements
- **Terraform** >= 1.5 - [Installation Guide](https://developer.hashicorp.com/terraform/install)
- **AWS CLI** - Configured with valid credentials
- **Git** - For cloning the code repository

### 2. AWS Credential Configuration
**Important**: AWS credentials must be configured before deployment. Refer to [`ONBOARDING.md`](ONBOARDING.md#2-configure-aws-credentials-connect-to-shared-account) to complete the following steps:

1. Run `aws configure` to configure access keys
2. Verify credentials: `aws sts get-caller-identity`
3. Ensure IAM user has sufficient permissions to create the following resources

### 3. Bedrock Model Access (One-time Configuration)
Bedrock model access must be explicitly enabled in the AWS account and region:

1. Log into AWS Console → Amazon Bedrock
2. Select **Model access** in the left navigation
3. Request access to Claude models (e.g., `anthropic.claude-3-haiku-20240307-v1:0`)
4. Wait for status to show **"Access granted"**

> **Note**: This is an account-level console operation that Terraform cannot automate.

### 4. Region Selection
Choose a region that supports both **Bedrock** and **Textract**:
- `ap-southeast-2` (Sydney) - Default, closest to New Zealand
- `us-east-1` - Alternative option
- `us-west-2` - Alternative option

---

## 1. Terraform Deployment Steps

### 1.1 Initialize Configuration
```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
```

Edit the `terraform.tfvars` file (if you need to modify region or instance size):
```hcl
region = "ap-southeast-2"
instance_type = "t3.small"
bedrock_model_id = "anthropic.claude-3-haiku-20240307-v1:0"
```

### 1.2 Initialize Terraform
```bash
terraform init
```

This initializes Terraform and downloads required provider plugins.

### 1.3 Review Deployment Plan
```bash
terraform plan
```

**Important**: Carefully review the plan output to confirm the resources to be created match expectations.

### 1.4 Execute Deployment
```bash
terraform apply
```

Enter `yes` to confirm deployment.

---

## 2. Deployment Process Details

`terraform apply` performs the following operations:

### Phase 1: Application Packaging
1. Terraform packages the `../app` folder into `terraform/build/app.zip`
2. Calculates MD5 hash of the zip file for version identification

### Phase 2: S3 Bucket Creation
1. Creates two S3 buckets:
   - Application version bucket (for Beanstalk application versions)
   - Menu uploads bucket (for user-uploaded menu files)
2. Both buckets have `force_destroy = true`, allowing deletion even when non-empty

### Phase 3: Elastic Beanstalk Configuration
1. Creates Elastic Beanstalk **application**
2. Creates application **version**, pointing to the zip file in S3
3. Creates Beanstalk **environment** (this is the most time-consuming step, ~4-6 minutes)

### Phase 4: Support Services Creation
1. **DynamoDB Table** - Menu item storage
2. **IAM Roles** - EC2 instance role and Beanstalk service role
3. **Cognito User Pool** - Authentication (not yet integrated into UI)
4. **Bedrock Knowledge Base** (optional) - If `bedrock_kb.tf` is configured

### Phase 5: Environment Variable Injection
The Beanstalk environment automatically injects the following environment variables:
- `BEDROCK_MODEL_ID` - From Terraform variables
- `KNOWLEDGE_BASE_ID` - If knowledge base is configured
- Other service-specific region configurations

---

## 3. Deployment Verification

### 3.1 Check Deployment Status
After `terraform apply` completes, the Beanstalk environment is still starting up. Check status:

```bash
# Get environment name
terraform output eb_environment_name

# Check environment status
aws elasticbeanstalk describe-environments \
  --environment-names <name from above> \
  --query "Environments[0].{Status:Status,Health:Health}"
```

Wait for status to become:
- `Status`: `Ready`
- `Health`: `Green` or `Ok`

### 3.2 Get Application URL
```bash
terraform output app_url
```

Open this URL in your browser.

### 3.3 Complete Functionality Testing
Perform the following tests in the application UI:

#### Test 1: Load Sample Menu
Click the **"Load Sample Menu"** button, which will:
1. Process all 8 sample dishes through the complete pipeline
2. OCR skip → Bedrock allergen extraction → FSANZ rules engine verification → 4-language translation → DynamoDB save
3. This is the fastest way to verify the complete pipeline works

#### Test 2: Language Switching
Switch the **"Display language"** dropdown (top right) - dish names and descriptions should switch to Bedrock-translated text.

#### Test 3: Allergen Filtering
Check the **Gluten-Free / Dairy-Free / Vegan** filter checkboxes - the grid should filter in real-time.

#### Test 4: Dish Detail View
Click any dish card to view the detail modal:
- Shows whether the Bedrock LLM and deterministic rules engine agreed
- Allows manual override of confirmed allergen list
- Provides option to delete the dish

#### Test 5: File Upload
Try **"Upload a menu file"**:
- Upload a real menu photo (JPG/PNG)
- Test the Textract OCR path
- Verify complete upload processing flow

#### Test 6: Manual Dish Addition
Use **"Add a dish manually"** to test:
- Single dish description processing
- Test path without file upload

---

## 4. Troubleshooting

### 4.1 Common Deployment Issues

| Symptom | Likely Cause | Solution |
|---------|--------------|----------|
| `app_url` returns 502/503 | Environment is still booting | Wait a few minutes, re-check `describe-environments` status |
| Environment stays unhealthy | Configuration or dependency issue | Check logs: `aws elasticbeanstalk describe-events --environment-name <name> --max-records 20` |
| "Load Sample Menu" works but every dish shows `"llm_source": "offline"` | Bedrock model access not enabled | Enable Bedrock model access in console, or check if `bedrock_model_id` is correct |
| Translations show `[Spanish - offline] ...` | Bedrock access issue, and Amazon Translate fallback failed | Check if instance role has `translate:TranslateText` permission (already granted in `iam.tf`) |
| `terraform apply` fails with IAM permissions error | AWS CLI credentials don't have rights to create IAM roles/policies | Ask AWS admin for `IAMFullAccess`-equivalent rights |
| `terraform apply` fails on solution stack data source (`no matching Elastic Beanstalk Solution Stack found`) | AWS periodically retires old Python platform versions | Loosen `python_version_regex` in `terraform.tfvars` or use `aws elasticbeanstalk list-available-solution-stacks` for exact current names |

### 4.2 Logging and Monitoring

#### Check Beanstalk Events
```bash
aws elasticbeanstalk describe-events \
  --environment-name <environment-name> \
  --max-records 50
```

#### View Application Logs
```bash
# Download recent logs
aws elasticbeanstalk request-environment-info \
  --environment-name <environment-name> \
  --info-type tail

# Retrieve logs
aws elasticbeanstalk retrieve-environment-info \
  --environment-name <environment-name> \
  --info-type tail
```

#### Check Instance Health Status
```bash
aws elasticbeanstalk describe-instances-health \
  --environment-name <environment-name> \
  --attribute-names All
```

---

## 5. Application Updates

### 5.1 Updates After Code Changes
Any change under `app/` changes the zip file's MD5 hash, which Terraform uses in the application version name. Therefore, a normal `terraform apply` automatically picks it up and deploys the new version.

**No manual `eb deploy` step needed**.

### 5.2 Update Process
1. Modify code in `app/`
2. Run `terraform plan` to see changes
3. Run `terraform apply` to deploy updates
4. Wait for Beanstalk environment to complete deployment (usually faster than initial deployment)

### 5.3 Infrastructure Changes
After modifying any `.tf` files in `terraform/`:
1. Run `terraform plan` to see infrastructure changes
2. Run `terraform apply` to apply changes
3. Some changes may require environment rebuild (e.g., instance type changes)

---

## 6. Environment Cleanup

### 6.1 Complete Destruction
```bash
cd terraform
terraform destroy
```

Confirm with `yes`.

### 6.2 Destruction Process
This removes:
1. **Elastic Beanstalk environment** and application
2. **Both S3 buckets** (even though they contain the uploaded zip files/raw menu files, thanks to `force_destroy = true`)
3. **DynamoDB table**
4. **IAM roles and policies**
5. **Cognito user pool**
6. **Bedrock knowledge base** (if configured)

**Nothing is left behind** and nothing needs a manual "empty bucket first" step.

### 6.3 Partial Cleanup
If you need to preserve certain resources:
1. Comment out or delete the corresponding `.tf` files
2. Run `terraform plan` to see what resources will be deleted
3. Run `terraform apply` to perform partial cleanup

---

## 7. Deployment Architecture Advantages

### 7.1 Comparison with Amplify
This architecture replaces the opaque build failures you might encounter with Amplify, providing a stack where every layer is explicit:

| Layer | Inspectability |
|-------|----------------|
| Application zip file | Explicit `build/app.zip`, manually inspectable |
| IAM roles | Explicitly defined in `iam.tf` |
| Environment variables | Explicit in Beanstalk environment configuration |
| Health check path | Defined `/health` endpoint in `application.py` |
| Error diagnosis | Can be inspected via `aws elasticbeanstalk describe-events` |

### 7.2 Multi-Region Support
Deployment supports services across two regions:

| Service | Deployment Region | Reason |
|---------|-------------------|--------|
| Bedrock + Knowledge Base | `ap-southeast-2` | Closest Bedrock region to New Zealand |
| DynamoDB + S3 + Textract | `us-east-1` | Elastic Beanstalk stack region |

### 7.3 Failure Recovery
- **Automatic fallback**: Automatically falls back to offline rules engine when Bedrock fails
- **Translation fallback**: Falls back to Amazon Translate when Bedrock translation fails
- **Knowledge base fallback**: Falls back to local `docs/` documents when AWS knowledge base is unavailable

---

## 8. Production Deployment Recommendations

### 8.1 Security Enhancements
1. **Enable HTTPS**: Configure Beanstalk load balancer with SSL certificate
2. **Database encryption**: Enable DynamoDB encryption
3. **S3 encryption**: Enable S3 bucket encryption
4. **Network isolation**: Place instances in private subnets

### 8.2 Monitoring and Alerting
1. **CloudWatch Alarms**: Set alarms for Beanstalk environment health status
2. **Custom metrics**: Monitor allergen extraction success rates
3. **Log archiving**: Send application logs to CloudWatch Logs

### 8.3 Scalability Considerations
1. **Auto-scaling**: Configure Beanstalk auto-scaling policies
2. **Database optimization**: Consider DynamoDB table partition key design
3. **Caching layer**: Add Redis cache for frequently accessed data

---

## Next Steps

After successful deployment:
1. Configure team member access: Refer to [`ONBOARDING.md`](ONBOARDING.md)
2. Integrate into CI/CD pipeline: Automate testing and deployment
3. Set up monitoring and alerting: Ensure production environment stability
4. Performance testing: Verify system performance under load
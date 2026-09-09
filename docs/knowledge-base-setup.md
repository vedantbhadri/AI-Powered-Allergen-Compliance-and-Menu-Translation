# Knowledge Base Complete Configuration Guide

## 1. What do you need to configure?

Knowledge Base requires the following components:

| Component | Purpose | Manual Configuration Required? |
|------|------|-----------------|
| **S3 Bucket** | Stores regulatory documents | ❌ Created automatically by Terraform |
| **Knowledge Base** | RAG retrieval service | ❌ Created automatically by Terraform |
| **OpenSearch Serverless** | Vector store | ❌ Created automatically by Terraform |
| **Service Role** | Knowledge Base access permissions | ❌ Created automatically by Terraform |
| **Knowledge Base ID** | Application connection ID | ✅ Must be obtained from the Terraform output |
| **Ingestion Job** | Document indexing | ✅ Must be triggered manually |

---

## 2. Step 1: Modify the Terraform configuration

Edit `terraform/terraform.tfvars`:

```hcl
# Enable Knowledge Base creation
create_knowledge_base = true

# (Optional) Specify the embedding model (a default is already configured)
bedrock_embedding_model_arn = "arn:aws:bedrock:ap-southeast-2::foundation-model/amazon.titan-embed-text-v2:0"
```

---

## 3. Step 2: Run Terraform to create the resources

```bash
cd terraform

# Initialize (first run)
terraform init

# Preview the resources that will be created
terraform plan -var="create_knowledge_base=true"

# Apply the configuration
terraform apply -var="create_knowledge_base=true"
```

### Expected output

```
aws_s3_bucket.kb_docs[0]: Creating...
aws_iam_role.kb_service_role[0]: Creating...
aws_opensearchserverless_collection.peal[0]: Creating...
aws_bedrockagent_knowledge_base.peal[0]: Creating...
aws_bedrockagent_data_source.peal[0]: Creating...

Apply complete! Resources: 8 added, 0 changed, 0 destroyed.
```

---

## 4. Step 3: Get the Knowledge Base ID

### Method 1: From the Terraform output

Add to `terraform/outputs.tf`:

```hcl
output "knowledge_base_id" {
  value       = var.create_knowledge_base ? aws_bedrockagent_knowledge_base.peal[0].id : ""
  description = "Bedrock Knowledge Base ID for RAG retrieval"
}

output "knowledge_base_arn" {
  value       = var.create_knowledge_base ? aws_bedrockagent_knowledge_base.peal[0].arn : ""
  description = "Bedrock Knowledge Base ARN"
}

output "kb_docs_bucket" {
  value       = var.create_knowledge_base ? aws_s3_bucket.kb_docs[0].bucket : ""
  description = "S3 bucket containing PEAL reference documents"
}
```

Then run:

```bash
terraform apply -var="create_knowledge_base=true"
terraform output knowledge_base_id
```

### Method 2: From the AWS Console

1. Sign in to the AWS Console
2. Navigate to **Amazon Bedrock** → **Knowledge Bases**
3. Find the Knowledge Base named `allergen-demo-peal-kb`
4. Copy the **Knowledge Base ID** (format: `ABCDEFGHIJ`)

### Method 3: Using the AWS CLI

```bash
aws bedrock-agent list-knowledge-bases \
  --region ap-southeast-2 \
  --query "knowledgeBaseSummaries[?contains(name, 'peal')].{ID:id, Name:name}"
```

---

## 5. Step 4: Connect the application to the Knowledge Base

### Method 1: Set environment variables

```bash
# Set the Knowledge Base ID
export KNOWLEDGE_BASE_ID=ABCDEFGHIJ

# Set the AWS region
export AWS_REGION=ap-southeast-2

# Disable local mode
export LOCAL_MODE=false
```

### Method 2: Modify the Beanstalk environment variables

Edit `terraform/beanstalk.tf` and find the environment variable configuration section:

```hcl
# Add this after the existing environment variables
setting {
  namespace = "aws:elasticbeanstalk:application:environment"
  name      = "KNOWLEDGE_BASE_ID"
  value     = aws_bedrockagent_knowledge_base.peal[0].id
}
```

Then redeploy:

```bash
terraform apply
```

### Method 3: Use a `.env` file (local development)

Create an `app/.env` file:

```env
KNOWLEDGE_BASE_ID=ABCDEFGHIJ
AWS_REGION=ap-southeast-2
LOCAL_MODE=false
```

---

## 6. Step 5: Trigger document ingestion

After the Knowledge Base is created, you need to trigger document indexing:

### Method 1: AWS Console

1. Navigate to **Bedrock** → **Knowledge Bases**
2. Select your Knowledge Base
3. Click the **Data sources** tab
4. Select the data source
5. Click the **Sync** button

### Method 2: AWS CLI

```bash
# Get the Data Source ID
aws bedrock-agent list-data-sources \
  --knowledge-base-id YOUR_KB_ID \
  --region ap-southeast-2

# Trigger the ingestion job
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id YOUR_KB_ID \
  --data-source-id YOUR_DS_ID \
  --region ap-southeast-2
```

### Method 3: Trigger automatically from Terraform (optional)

Add to `terraform/bedrock_kb.tf`:

```hcl
# Note: This may increase Terraform apply time
resource "null_resource" "trigger_ingestion" {
  count = var.create_knowledge_base ? 1 : 0
  
  provisioner "local-exec" {
    command = <<-EOT
      aws bedrock-agent start-ingestion-job \
        --knowledge-base-id ${aws_bedrockagent_knowledge_base.peal[0].id} \
        --data-source-id ${aws_bedrockagent_data_source.peal[0].id} \
        --region ${var.aws_region}
    EOT
  }
  
  depends_on = [
    aws_bedrockagent_data_source.peal
  ]
}
```

---

## 7. Step 6: Verify the configuration

### Check the S3 documents

```bash
# List the uploaded documents
aws s3 ls s3://allergen-demo-kb-docs-ACCOUNT_ID/nz-peal/

# Expected output:
# PRE nz-peal/
# 2024-XX-XX XX:XX:XX XXXX nz_peal_allergens.md
```

### Check the Knowledge Base status

```bash
aws bedrock-agent get-knowledge-base \
  --knowledge-base-id YOUR_KB_ID \
  --region ap-southeast-2
```

### Test retrieval

```bash
# Retrieve directly through the application service function (recommended)
cd app
LOCAL_MODE=false KNOWLEDGE_BASE_ID=$YOUR_KB_ID python -c "
from services import allergen_service as svc
r = svc.retrieve_context('peanut requirements')
print('engine:', r['engine'], '| chunks:', len(r['chunks']))
"

# Or call the Retrieve API directly with the AWS CLI
aws bedrock-agent-runtime retrieve \
  --knowledge-base-id $YOUR_KB_ID \
  --retrieval-query '{"text":"peanut requirements"}' \
  --retrieval-configuration '{"managedSearchConfiguration":{"numberOfResults":3}}' \
  --region ap-southeast-2
```

---

## 8. Complete configuration checklist

### Required configuration

| Config item | Source | Example value |
|--------|------|--------|
| `KNOWLEDGE_BASE_ID` | Terraform output | `ABCDEFGHIJ` |
| `AWS_REGION` | Fixed | `ap-southeast-2` |
| `LOCAL_MODE` | Fixed | `false` |

### AWS resources (created automatically by Terraform)

| Resource | Name pattern | Description |
|------|----------|------|
| S3 Bucket | `allergen-demo-kb-docs-*` | Stores regulatory documents |
| Knowledge Base | `allergen-demo-peal-kb` | RAG service |
| OpenSearch Collection | `allergen-demo-peal` | Vector store |
| IAM Role | `allergen-demo-bedrock-kb-role` | Service role |

### IAM permissions (configured automatically)

Your execution identity needs the following permissions:

```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock-agent:CreateKnowledgeBase",
    "bedrock-agent:CreateDataSource",
    "bedrock-agent:StartIngestionJob",
    "aoss:CreateCollection",
    "aoss:CreateSecurityPolicy"
  ],
  "Resource": "*"
}
```

---

## 9. Frequently asked questions

### 1. Terraform apply fails: insufficient permissions

**Error message**:
```
Error: AccessDeniedException: User is not authorized to perform: bedrock-agent:CreateKnowledgeBase
```

**Solution**:
Make sure your AWS identity has the following permissions:
- `bedrock-agent:*`
- `aoss:*`
- `iam:CreateRole`
- `s3:CreateBucket`

### 2. Knowledge Base ID is empty

**Possible causes**:
- `create_knowledge_base = false` (the default value)
- Terraform apply was not completed

**Solution**:
```bash
# Check the configuration
grep "create_knowledge_base" terraform/terraform.tfvars

# Re-run apply
terraform apply -var="create_knowledge_base=true"
```

### 3. No results after ingestion

**Steps to check**:
```bash
# 1. Check whether the documents were uploaded
aws s3 ls s3://YOUR_KB_BUCKET/nz-peal/

# 2. Check the ingestion job status
aws bedrock-agent list-ingestion-jobs \
  --knowledge-base-id YOUR_KB_ID \
  --data-source-id YOUR_DS_ID

# 3. Check the OpenSearch index
aws opensearchserverless list-collections
```

---

## 10. Quick check checklist

```bash
# 1. Check the Terraform variable
terraform console
> var.create_knowledge_base
true

# 2. Check whether the Knowledge Base was created
aws bedrock-agent list-knowledge-bases --region ap-southeast-2

# 3. Check the S3 documents
aws s3 ls s3://$(terraform output -raw kb_docs_bucket)/nz-peal/

# 4. Test retrieval
export KNOWLEDGE_BASE_ID=$(terraform output -raw knowledge_base_id)
cd app
LOCAL_MODE=false python -c "
from services import allergen_service as svc
r = svc.retrieve_context('allergen declaration requirements')
print('engine:', r['engine'], '| chunks:', len(r['chunks']))
"
```

---

## 11. Next steps

Once the configuration is complete, you can:

1. **Test RAG retrieval**
   ```bash
   export KNOWLEDGE_BASE_ID=$(terraform output -raw knowledge_base_id)
   cd app
   LOCAL_MODE=false python -c "
   from services import allergen_service as svc
   r = svc.retrieve_context('fish allergen requirements')
   assert r['engine'] == 'aws'   # Hit the Bedrock KB
   print('AWS RAG OK,', len(r['chunks']), 'chunks')
   "
   ```

2. **Test the complete flow**
   ```bash
   # Via the application UI or API: POST /api/allergens/extract
   # (The application uses the KB automatically when running in real AWS mode)
   ```

3. **Integrate into the application**
   - The code automatically uses the Knowledge Base (just set `KNOWLEDGE_BASE_ID`; no code changes needed)

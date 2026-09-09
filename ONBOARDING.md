# Team Onboarding Guide - Local Development and AWS Environment Configuration

This document provides a complete project setup guide for new team members, including local development environment configuration and steps to connect to the shared AWS account.

## Quick Start

**If this is your first time working with this project, we recommend following this order:**

1. **Local Development Mode** (No AWS account): First run in local mode to understand project functionality
2. **AWS Credential Configuration**: Configure AWS credentials to connect to the shared account
3. **Real AWS Mode**: Use real AWS services for development

---

## 1. Local Development Mode (No AWS Account)

This is the fastest way to get started, requiring no AWS configuration:

### 1.1 Install Python Dependencies
```powershell
cd app
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 1.2 Run in Local Mode
```powershell
cd app
$env:LOCAL_MODE = "true"
.\.venv\Scripts\python.exe application.py
```

### 1.3 Access the Application
Open your browser and visit http://localhost:8000

### Local Mode Characteristics
- ✅ All AWS services are simulated/stubbed
- ✅ Allergen rules engine works normally
- ✅ Complete UI interface is available
- ✅ Sample data can be loaded
- ❌ No Bedrock AI functionality
- ❌ No real OCR processing
- ❌ Data stored in local JSON files

---

## 2. Configure AWS Credentials (Connect to Shared Account)

To interact with the team's shared AWS account, you need to configure IAM long-term access keys:

### 2.1 Obtain AWS Access Keys
Get the following information from your team administrator:
- **Access Key ID** (starts with `AKIA...`)
- **Secret Access Key** (long random string, shown only once)

> **Security Tip**: The Secret Access Key is only shown once when created. Keep it secure.

### 2.2 Configure AWS CLI
```powershell
aws configure
```

Enter the following information:
```
AWS Access Key ID [None]: AKIAXXXXXXXXXXXXXXXX
AWS Secret Access Key [None]: <paste your Secret Access Key>
Default region name [None]: ap-southeast-2
Default output format [None]: json
```

### 2.3 Verify Configuration
```powershell
aws sts get-caller-identity
```

Should display information similar to:
```
{
    "UserId": "AIDA...",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/your-username"
}
```

### 2.4 Credential File Locations
`aws configure` creates the following files:

| File | Windows Path | Contents |
|------|-------------|----------|
| Credentials File | `C:\Users\<username>\.aws\credentials` | `[default]` section with `aws_access_key_id` and `aws_secret_access_key` |
| Config File | `C:\Users\<username>\.aws\config` | `[default]` section with `region` and `output` |

> **Note**: These files are in your user home directory, **NOT** in the project repository.

---

## 3. Set Application Environment Variables

This project runs services across two AWS regions, requiring region variables for each service:

### 3.1 Use One-Stop Configuration Script (Recommended)
```powershell
# Run from project root directory
.\setup_aws_env.ps1
```

This script will:
1. Check if AWS credentials are valid
2. Set all required environment variables
3. Check Bedrock access permissions
4. Provide application startup instructions

### 3.2 Environment Variables Explained

| Service | Region | Environment Variable | Description |
|---------|--------|---------------------|-------------|
| Bedrock Inference | `ap-southeast-2` | `BEDROCK_REGION` | Bedrock LLM invocation region |
| Bedrock Knowledge Base | `ap-southeast-2` | `KB_REGION` | RAG knowledge base region |
| DynamoDB Table | `us-east-1` | `DYNAMODB_REGION` | Data storage region |
| S3 Uploads Bucket | `us-east-1` | `S3_REGION` | Menu file storage region |
| Textract OCR | `us-east-1` | `TEXTRACT_REGION` | OCR processing region |

### 3.3 Resource Names
```powershell
# Resource names from Terraform deployment
$env:S3_BUCKET = "allergen-demo-dev-menu-uploads-669232219904"
$env:DYNAMODB_TABLE = "allergen-demo-dev-menu-items"
$env:KNOWLEDGE_BASE_ID = "CBFZTLLUHU"
$env:BEDROCK_MODEL_ID = "au.anthropic.claude-opus-4-6-v1"
```

---

## 4. Start Application (Real AWS Mode)

After configuring environment variables, start the application to use real AWS services:

### 4.1 Start Application
```powershell
cd app
.\.venv\Scripts\python.exe application.py
```

### 4.2 Verify AWS Connection
Visit http://localhost:8000/health, should return:
```json
{
  "status": "ok",
  "local_mode": "false"
}
```

### 4.3 Test Complete Functionality
1. Click **"Load Sample Menu"** - Test the complete processing pipeline
2. Switch **"Display language"** dropdown - Test translation functionality
3. Check allergen filters - Test real-time filtering
4. Click any dish card - Test human-in-the-loop review functionality
5. Try **"Upload a menu file"** - Test OCR processing

---

## 5. Verify Service Connection Status

| Metric | Real AWS Mode | Local Mode |
|--------|--------------|------------|
| `/health` response | `"local_mode": "false"` | `"local_mode": "true"` |
| Allergen extraction engine | `"engine": "bedrock-tool-use"` | `"engine": "rules"` |
| Regulatory citations | Shows PDF source citations | No citations or local citations |
| Data storage | DynamoDB table | Local JSON file |
| Translation quality | Bedrock high-quality translation | `[Language - offline]` labels |

---

## 6. Troubleshooting

### 6.1 Common Issues

| Problem | Likely Cause | Solution |
|---------|--------------|----------|
| `No credentials found` | AWS credentials not configured | Re-run `aws configure` |
| `AccessDenied` | Insufficient IAM permissions | Contact administrator to add required permissions |
| DynamoDB `ResourceNotFoundException` | Wrong table name or region | Confirm `DYNAMODB_REGION=us-east-1` and correct table name |
| RAG falls back to local mode | boto3 version too old | `pip install -r requirements.txt` to update to 1.43.88 |
| Translations show `[Spanish - offline]` | Bedrock/Translate unavailable | Check credentials and model access permissions |
| Port 8000 in use | Port conflict | Set `$env:PORT=8080` then restart |
| PowerShell script blocked | Execution policy restriction | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` |

### 6.2 IAM Permission Requirements
The application requires the following AWS service permissions:
- `bedrock:Converse` / `InvokeModel` / `Retrieve` (bedrock-agent-runtime)
- `dynamodb:PutItem` / `GetItem` / `Query` / `Scan` / `UpdateItem` / `DeleteItem`
- `s3:PutObject` / `GetObject` / `ListBucket`
- `textract:DetectDocumentText`
- `translate:TranslateText`

### 6.3 Bedrock Model Access
If you encounter Bedrock access issues:
1. Log into AWS Console → Amazon Bedrock
2. Select **Model access** in the left navigation
3. Request access to Claude models
4. Wait for status to change to **"Access granted"**

---

## 7. Development Workflow

### 7.1 Daily Development
1. Use local mode for feature development
2. Periodically switch to AWS mode to test integration
3. Ensure both modes work before committing code

### 7.2 Code Changes
- Modify code in the `app/` directory
- Run local tests: `python -m pytest tests/`
- Verify local mode functionality
- Switch to AWS mode to validate cloud service integration

### 7.3 Environment Management
- Need to run `.\setup_aws_env.ps1` for each new terminal session
- Or manually set required environment variables
- Keep `.env.example` as a reference (code does not auto-load this file)

---

## 8. Useful Command Reference

### Local Development
```powershell
# Local mode startup
$env:LOCAL_MODE = "true"; cd app; .\.venv\Scripts\python.exe application.py

# Run tests
python -m pytest tests/ -v

# Install/update dependencies
cd app; .\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### AWS Mode
```powershell
# One-stop environment configuration
.\setup_aws_env.ps1

# Start AWS mode application
cd app; .\.venv\Scripts\python.exe application.py

# Verify AWS credentials
aws sts get-caller-identity

# Check Bedrock access
aws bedrock list-foundation-models --region ap-southeast-2 --query "modelSummaries[?contains(modelId, 'claude')].modelId"
```

---

## Next Steps

After completing local development environment setup:
1. Read [`docs/allergen-api.md`](docs/allergen-api.md) to understand API interfaces
2. Review [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) to learn how to deploy to production
3. Explore regulatory documents and knowledge base setup guides in the `docs/` directory
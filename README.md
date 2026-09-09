# AI-Powered Allergen Compliance & Menu Translation

Flask application running on Elastic Beanstalk (deployed via Terraform). Allergen compliance is verified by a **deterministic NZ PEAL rules engine** cross-checked against a **Bedrock LLM** read of the dish, plus an **optional Bedrock RAG** over NZ MPI/FSANZ regulatory documents (KB opt-in via `terraform/bedrock_kb.tf`; falls back to bundled `docs/` when unset). Fully tear-down-able with `terraform destroy`.

## Quick Navigation

- **Team Onboarding**: [`ONBOARDING.md`](ONBOARDING.md) - Team setup and local development
- **Deployment Guide**: [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) - Terraform deployment to AWS
- **API Documentation**: [`docs/allergen-api.md`](docs/allergen-api.md) - Service API interfaces
- **Knowledge Base Setup**: [`docs/knowledge-base-setup.md`](docs/knowledge-base-setup.md) - Bedrock knowledge base configuration

## Project Overview

This is an AI-powered allergen compliance and menu translation system designed for the food service industry. The system can:

### Core Capabilities
- **Intelligent Menu Parsing**: Extract dishes, prices, and descriptions from OCR text
- **Allergen Detection**: Combine Bedrock LLM and deterministic rules engine for allergen detection
- **Compliance Verification**: Regulatory compliance checking based on NZ PEAL standards
- **Multilingual Translation**: Support for Spanish, German, Japanese, and Chinese menu translation
- **RAG Enhancement**: Retrieve regulatory documents via Bedrock Knowledge Base (optional)
- **Human-in-the-Loop Review**: Provide intervention interface for allergen confirmation

### Technical Architecture
- **Frontend**: Responsive interface based on HTML/CSS/JavaScript
- **Backend**: Flask application running on Elastic Beanstalk
- **AI Services**: AWS Bedrock LLM + Knowledge Base RAG
- **Data Storage**: DynamoDB + S3
- **Infrastructure**: Fully deployed via Terraform
- **OCR Processing**: AWS Textract

## Project Structure

```
app/                    # Flask application (deployed to Beanstalk)
  application.py        # Routes and pipeline processing
  services/             # Service modules
    allergen_rules.py   # NZ PEAL rules engine
    allergen_service.py # RAG-enhanced allergen service
    bedrock_service.py  # Bedrock LLM service
    dynamo_service.py   # DynamoDB data access
    s3_service.py       # S3 file storage
    textract_service.py # Textract OCR processing
    menu_parser.py      # Intelligent menu parsing
  static/              # Frontend interface
  sample_data/         # Sample data
  requirements.txt     # Python dependencies

terraform/             # AWS infrastructure
  beanstalk.tf         # Elastic Beanstalk configuration
  s3.tf               # S3 buckets
  dynamodb.tf          # DynamoDB table
  iam.tf              # IAM roles and permissions
  bedrock_kb.tf        # Bedrock Knowledge Base (optional)
  cognito.tf          # Cognito user pool
  network.tf          # Network configuration

docs/                  # Regulatory documents and API documentation
  allergen-api.md      # API interface documentation
  nz_peal_allergens.md # NZ PEAL allergen standards
  knowledge-base-*.md  # Knowledge base configuration guides
  *.pdf               # Regulatory PDF documents

setup_aws_env.ps1      # One-stop environment configuration script
run_local.ps1          # Local run script (Windows)
```

## Quick Start

### 1. Local Development (No AWS Account)
```powershell
cd app
$env:LOCAL_MODE = "true"
.\.venv\Scripts\python.exe application.py
```
Then visit http://localhost:8000

### 2. AWS Deployment
Refer to [`DEPLOY_GUIDE.md`](DEPLOY_GUIDE.md) for complete deployment steps.

### 3. Team Development Setup
Refer to [`ONBOARDING.md`](ONBOARDING.md) for detailed configuration instructions for team collaboration.

## Allergen Standards

Based on NZ MPI [mandatory declarable allergens](https://www.mpi.govt.nz/food-business/labelling-composition-food-drinks/allergen-declarations-warnings-and-advisory-statements-on-food-labels):
Peanuts, tree nuts (each declared individually), crustacean, **molluscs**, fish, milk, egg, wheat, soy, sesame, lupin — plus gluten (from wheat/rye/barley/oats/spelt/triticale) and added sulphites (>10 mg/kg). Implemented in `app/services/allergen_rules.py` in `PEAL_CATEGORIES`.

## Supported Languages

The system supports menu translation in the following languages:
- Spanish (Spanish)
- German (German)  
- Japanese (Japanese)
- Chinese Simplified (Mandarin Chinese Simplified)

## License

This project follows an open-source license. Regulatory document copyrights belong to their respective organizations.
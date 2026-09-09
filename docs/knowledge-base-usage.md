# Knowledge Base Usage Guide

## 1. Using the Knowledge Base in Code

### 1.1 Basic Usage

```python
from services.allergen_service import retrieve_context, is_kb_available

# Check whether the Knowledge Base is available
if is_kb_available():
    print("✅ Knowledge Base available")

# Retrieve regulatory context
query = "What are the allergen declaration requirements for peanuts?"
result = retrieve_context(query, top_k=5)

print(f"Engine: {result['engine']}")  # 'aws', 'local', or 'none'
print(f"Found {len(result['chunks'])} relevant chunks")

# Access the retrieval results
for chunk in result['chunks']:
    print(f"Source: {chunk['source']}")
    print(f"Content: {chunk['text']}")
    print(f"Score: {chunk['score']}")
```

### 1.2 Using It in Compliance Verification

The Knowledge Base is integrated into `allergen_service.verify()`:

```python
from services.allergen_service import verify
from services.allergen_service import retrieve_context

# Option 1: call verify directly (it invokes RAG automatically)
result = verify("Chicken Satay", allergens=["peanuts", "soy"])
print(result.to_json())

# Option 2: call RAG manually
dish_text = "Chicken Satay with peanut sauce"
retrieval = retrieve_context(dish_text)

# retrieval structure:
# {
#     "engine": "aws" | "local" | "none",
#     "chunks": [
#         {
#             "text": "...regulation text...",
#             "source": "s3://bucket/...",
#             "section": "Peanuts",
#             "score": 0.85
#         }
#     ]
# }
```

### 1.3 Using It in the Pipeline

The complete end-to-end flow:

```python
from services.allergen_service import extract, verify, retrieve_context

# Step 1: allergen extraction
dish_name = "Seafood Chowder"
description = "Creamy soup with fish, prawns and milk"
extraction = extract(dish_name, description)

# Step 2: retrieve regulatory evidence via RAG
dish_text = f"{dish_name} {description}"
retrieval = retrieve_context(dish_text, top_k=5)

# Step 3: compliance verdict (incorporating the RAG results)
# compliance_service uses the retrieval result
from services.compliance_service import verify_compliance
compliance = verify_compliance(
    dish_name,
    description,
    extraction.confirmed_names,  # allergens extracted by the LLM
    [],  # rule engine results (optional)
    retrieval  # RAG retrieval results
)
```

## 2. Environment Configuration

### Required Environment Variables

```bash
# Knowledge Base ID (obtained from Terraform or the Console)
export KNOWLEDGE_BASE_ID=ABCDEFGHIJ

# AWS region
export AWS_REGION=ap-southeast-2

# Disable local mode (use AWS)
export LOCAL_MODE=false
```

### Optional: Using an AWS Profile

```bash
# Use a specific AWS profile
export AWS_PROFILE=your-profile-name
```

## 3. Testing

### 3.1 Quick Connectivity Test

```bash
# Set the Knowledge Base ID
export KNOWLEDGE_BASE_ID=your-kb-id

# Run a quick test (via the application API or by calling allergen_service directly)
# Option 1: through the API (requires the app to be running)
# POST /api/allergens/extract  { "dish_name": "...", "description": "..." }
# Option 2: call the function directly
cd app
LOCAL_MODE=false KNOWLEDGE_BASE_ID=your-kb-id python -c "
from services import allergen_service as svc
r = svc.retrieve_context('peanut requirements')
print(r['engine'], len(r['chunks']))
"
```

### 3.2 Full Test

```bash
# Local mode test (uses the local docs/ corpus)
cd app
LOCAL_MODE=true python -c "
from services import allergen_service as svc
r = svc.retrieve_context('milk requirements')
assert r['engine'] == 'local'
print('local RAG OK:', len(r['chunks']), 'chunks')
"

# AWS mode test (requires real credentials)
export KNOWLEDGE_BASE_ID=your-kb-id
cd app
LOCAL_MODE=false python -c "
from services import allergen_service as svc
r = svc.retrieve_context('peanut requirements')
assert r['engine'] == 'aws'
print('aws RAG OK:', len(r['chunks']), 'chunks')
"
```

### 3.3 Unit Tests

```bash
cd app
python -m unittest discover -s tests -p "test_*.py" -v   # if the tests directory exists
```

## 4. Fallback Behavior

The RAG service falls back automatically:

1. **AWS mode**: `KNOWLEDGE_BASE_ID` is set + `LOCAL_MODE=false`
   - Calls the Bedrock Knowledge Base `Retrieve` API
   - Returns `{"engine": "aws", "chunks": [...]}`

2. **Local mode**: automatically falls back in the following cases
   - `LOCAL_MODE=true`
   - `KNOWLEDGE_BASE_ID` is not set
   - AWS calls fail (permissions, network, etc.)
   - Returns `{"engine": "local", "chunks": [...]}`

3. **No results**: no relevant content is found
   - Returns `{"engine": "none", "chunks": []}`

## 5. Checking the Knowledge Base Status

### Checking in Code

```python
from services import allergen_service as svc

if svc.is_kb_available():
    print("Will use AWS Knowledge Base")
else:
    print("Will use local docs/ search")
```

### Checking with the AWS CLI

```bash
# List all Knowledge Bases
aws bedrock-agent list-knowledge-bases \
  --region ap-southeast-2

# Get details of a specific Knowledge Base
aws bedrock-agent get-knowledge-base \
  --knowledge-base-id YOUR_KB_ID \
  --region ap-southeast-2

# Test retrieval
aws bedrock-agent-runtime retrieve \
  --knowledge-base-id YOUR_KB_ID \
  --retrieval-query '{"text": "peanut allergen requirements"}' \
  --region ap-southeast-2
```

## 6. Frequently Asked Questions

### 6.1 Knowledge Base Returns Empty Results

**Possible causes**:
- The Knowledge Base has not been ingested yet
- The document format is not supported
- The query is too vague

**Solutions**:
```bash
# Check the data source status
aws bedrock-agent list-data-sources \
  --knowledge-base-id YOUR_KB_ID

# Trigger an ingestion job
aws bedrock-agent start-ingestion-job \
  --knowledge-base-id YOUR_KB_ID \
  --data-source-id YOUR_DS_ID
```

### 6.2 Permission Errors

**Required IAM permissions**:
```json
{
  "Effect": "Allow",
  "Action": [
    "bedrock:Retrieve",
    "bedrock-agent-runtime:Retrieve"
  ],
  "Resource": "arn:aws:bedrock:ap-southeast-2:*:knowledge-base/*"
}
```

### 6.3 Falling Back to Local Mode

Check the following configuration:
```python
import os

print(f"LOCAL_MODE: {os.environ.get('LOCAL_MODE', 'false')}")
print(f"KNOWLEDGE_BASE_ID: {os.environ.get('KNOWLEDGE_BASE_ID', '')}")
print(f"AWS_REGION: {os.environ.get('AWS_REGION', 'ap-southeast-2')}")
```

## 7. Output Formats

### AWS Mode Output

```json
{
  "engine": "aws",
  "chunks": [
    {
      "text": "Peanuts must be declared...",
      "source": "s3://bucket/mpi-guide.pdf",
      "section": "",
      "score": 0.85
    }
  ]
}
```

### Local Mode Output

```json
{
  "engine": "local",
  "chunks": [
    {
      "text": "Peanuts must be declared on food labels...",
      "source": "nz_peal_allergens.md",
      "section": "Peanuts",
      "score": 1.0
    }
  ]
}
```

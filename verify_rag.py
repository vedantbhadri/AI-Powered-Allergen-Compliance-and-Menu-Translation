#!/usr/bin/env python
"""
Verify RAG functionality completeness
"""

import os
import sys

# Add app directory to Python path
current_dir = os.path.dirname(os.path.abspath(__file__))
app_dir = os.path.join(current_dir, "app")
sys.path.insert(0, app_dir)

print("=== RAG Functionality Completeness Verification ===\n")

# 1. Check docs directory
print("1. Checking documentation directory:")
docs_dir = os.path.join(current_dir, "docs")
print(f"   - docs directory: {docs_dir}")
print(f"   - exists: {os.path.exists(docs_dir)}")

if os.path.exists(docs_dir):
    required_docs = [
        "nz_peal_allergens.md",
        "allergen-api.md",
        "knowledge-base-setup.md",
        "knowledge-base-usage.md"
    ]
    
    for doc in required_docs:
        doc_path = os.path.join(docs_dir, doc)
        print(f"   - {doc}: {'✅' if os.path.exists(doc_path) else '❌'}")

print()

# 2. Check service modules
print("2. Checking service modules:")
try:
    import services.allergen_service as allergen_service
    print("   - allergen_service: ✅")
    
    # Check required functions
    required_functions = [
        "retrieve_context",
        "is_kb_available", 
        "KB_DOCS_DIR",
        "extract",
        "verify",
        "verify_pipeline"
    ]
    
    for func in required_functions:
        if hasattr(allergen_service, func):
            print(f"   - {func}: ✅")
        else:
            print(f"   - {func}: ❌")
    
except ImportError as e:
    print(f"   - Import failed: {e}")

print()

# 3. Check configuration files
print("3. Checking configuration files:")
setup_script = os.path.join(current_dir, "setup_aws_env.ps1")
print(f"   - setup_aws_env.ps1: {'✅' if os.path.exists(setup_script) else '❌'}")

# Check Terraform configuration
terraform_dir = os.path.join(current_dir, "terraform")
tf_files = [
    "bedrock_kb.tf",
    "variables.tf", 
    "outputs.tf"
]

for tf_file in tf_files:
    tf_path = os.path.join(terraform_dir, tf_file)
    print(f"   - terraform/{tf_file}: {'✅' if os.path.exists(tf_path) else '❌'}")

print()

# 4. Check imports in application.py
print("4. Checking application.py imports:")
app_path = os.path.join(app_dir, "application.py")
if os.path.exists(app_path):
    with open(app_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    checks = [
        ("allergen_service import", "from services import allergen_service" in content),
        ("allergen API endpoint", "@application.route(\"/api/allergens/extract\")" in content),
        ("compliance API endpoint", "@application.route(\"/api/compliance/verify\")" in content),
        ("RAG integration", "retrieve_context" in content and "verify_pipeline" in content)
    ]
    
    for check_name, check_result in checks:
        print(f"   - {check_name}: {'✅' if check_result else '❌'}")

print("\n=== Verification Complete ===")
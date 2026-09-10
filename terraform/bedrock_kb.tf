# Optional Bedrock Knowledge Base for compliance RAG layer
# This file is only used when var.create_knowledge_base = true

resource "aws_bedrockagent_knowledge_base" "peal" {
  count = var.create_knowledge_base ? 1 : 0
  
  name = "${var.project_name}-peal-kb"
  description = "NZ PEAL regulatory reference for allergen compliance"
  role_arn    = aws_iam_role.bedrock_kb_role[0].arn
  
  knowledge_base_configuration {
    type = "VECTOR"
    vector_knowledge_base_configuration {
      embedding_model_arn = var.bedrock_embedding_model_arn
    }
  }

  storage_configuration {
    type = "OPENSEARCH_SERVERLESS"
    opensearch_serverless_configuration {
      collection_arn    = "arn:aws:aoss:${var.aws_region}:${data.aws_caller_identity.current.account_id}:collection/peal-collection"
      vector_index_name = "peal-vector-index"
    }
  }

  tags = {
    Name        = "${var.project_name}-peal-kb"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_bedrockagent_data_source" "peal" {
  count = var.create_knowledge_base ? 1 : 0
  
  knowledge_base_id = aws_bedrockagent_knowledge_base.peal[0].id
  name              = "peal-docs"
  description       = "NZ PEAL regulatory documents"

  data_source_configuration {
    type = "S3"
    
    s3_configuration {
      bucket_arn = aws_s3_bucket.kb_docs[0].arn
      
      inclusion_prefixes = [
        "nz_peal_allergens.md"
      ]
    }
  }
}

resource "aws_s3_bucket" "kb_docs" {
  count = var.create_knowledge_base ? 1 : 0
  
  bucket = "${var.project_name}-kb-docs-${random_id.suffix[0].hex}"
  
  force_destroy = true
  
  tags = {
    Name        = "${var.project_name}-kb-docs"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "aws_s3_object" "peal_document" {
  count = var.create_knowledge_base ? 1 : 0
  
  bucket = aws_s3_bucket.kb_docs[0].bucket
  key    = "nz_peal_allergens.md"
  source = "../docs/nz_peal_allergens.md"
  etag   = filemd5("../docs/nz_peal_allergens.md")
}

resource "aws_iam_role" "bedrock_kb_role" {
  count = var.create_knowledge_base ? 1 : 0
  
  name = "${var.project_name}-bedrock-kb-role"
  
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "bedrock.amazonaws.com"
        }
      }
    ]
  })
  
  inline_policy {
    name = "BedrockKBPolicy"
    
    policy = jsonencode({
      Version = "2012-10-17"
      Statement = [
        {
          Effect = "Allow"
          Action = [
            "s3:GetObject",
            "s3:ListBucket"
          ]
          Resource = [
            "${aws_s3_bucket.kb_docs[0].arn}",
            "${aws_s3_bucket.kb_docs[0].arn}/*"
          ]
        }
      ]
    })
  }
  
  tags = {
    Name        = "${var.project_name}-bedrock-kb-role"
    Environment = var.environment
    Project     = var.project_name
  }
}

resource "random_id" "suffix" {
  count = var.create_knowledge_base ? 1 : 0
  
  byte_length = 4
}

# Requirements Document

## Introduction

This feature migrates the existing AI-powered allergen compliance and menu translation application from a single Flask monolith running on AWS Elastic Beanstalk to a serverless architecture composed of six independent AWS Lambda functions fronted by API Gateway. The migration decomposes the synchronous, single-request processing pipeline (upload → OCR → allergen analysis → translation → persist) into an asynchronous, event-driven chain coordinated by a status field on each DynamoDB item.

The existing business logic in the service modules (`textract_service`, `bedrock_service`, `allergen_rules`, `dynamo_service`, `s3_service`) must remain unchanged. The migration changes only what invokes those modules — replacing the Flask route handlers with Lambda handler functions — and updates the Terraform infrastructure and DynamoDB item shape accordingly. All existing behavioral guarantees of the pipeline (allergen reconciliation between the LLM and the deterministic rules engine, display tag derivation, diet tag derivation, and the human-in-the-loop override) must be preserved.

The primary driver for the asynchronous decomposition is the API Gateway 30-second integration timeout, which the current synchronous pipeline (OCR + multiple Bedrock calls per dish) can exceed.

## Glossary

- **Service_Modules**: The existing Python modules under `app/services/` (`textract_service`, `bedrock_service`, `allergen_rules`, `dynamo_service`, `s3_service`) whose logic must not be modified.
- **Upload_Handler**: The Lambda function fronting `POST /menus/upload` that stores the raw file, creates the initial DynamoDB item, and starts an asynchronous Textract job.
- **OCR_Processor**: The Lambda function triggered by Textract asynchronous job completion that retrieves extracted text and invokes the Allergen_Extractor.
- **Allergen_Extractor**: The Lambda function that performs Bedrock allergen extraction and the FSANZ rules-engine cross-check, then invokes the Translator.
- **Translator**: The Lambda function that produces translations for all configured languages, writes the final structured result to DynamoDB, and sets the status to `ready`.
- **Menu_Read_API**: The Lambda function serving read-only endpoints for the customer-facing menu view and the dashboard status polling.
- **Menu_Edit_API**: The Lambda function serving the human-in-the-loop allergen override endpoint.
- **Upload_Id**: A unique identifier returned to the client on upload, used to track the processing status of a submitted menu file.
- **Status_Field**: The DynamoDB item attribute recording pipeline progress, with values `processing`, `ocr_done`, `analyzing`, `translating`, and `ready`.
- **PEAL_Categories**: The FSANZ Standard 1.2.3 mandatory declarable allergen categories defined in `allergen_rules.PEAL_CATEGORIES`.
- **Configured_Languages**: The translation target languages defined in `bedrock_service.LANGUAGES` (Spanish, German, Japanese, Mandarin Chinese Simplified).
- **Reconciliation**: The combination of LLM-extracted and rules-engine-extracted allergen categories via `allergen_rules.reconcile_allergens`, taking the union as confirmed.
- **Translations_Map**: A DynamoDB item attribute embedding all language translations for a dish directly within the dish item, keyed by language code.
- **Infrastructure_Definition**: The Terraform configuration under `terraform/` that provisions the AWS resources.
- **Execution_Role**: An IAM role assigned to a single Lambda function granting only the permissions that function requires.

## Requirements

### Requirement 1: Preserve existing business logic

**User Story:** As a maintainer, I want the existing service modules to remain unchanged during the migration, so that the validated allergen compliance and translation behavior is preserved.

#### Acceptance Criteria

1. THE Service_Modules under `app/services/` SHALL retain byte-for-byte identical source content after migration, such that their public function signatures, return structures, and offline/fallback branches are unchanged from the pre-migration versions.
2. WHERE allergen extraction is performed, THE Allergen_Extractor SHALL invoke `bedrock_service.extract_allergens` passing the dish name as the first argument and the dish description as the second argument, and SHALL read the `categories` list from the returned result.
3. WHERE the deterministic allergen cross-check is performed, THE Allergen_Extractor SHALL invoke `allergen_rules.scan_text_for_allergens` on the dish text and then invoke `allergen_rules.reconcile_allergens` passing the LLM-extracted categories and the rules-engine categories.
4. WHERE translation is performed, THE Translator SHALL invoke `bedrock_service.translate_dish` passing the dish name as the first argument and the dish description as the second argument.
5. THE Translator SHALL derive the set of translation target languages as exactly the set of language codes present as keys in `bedrock_service.LANGUAGES` (currently the four codes: `es`, `de`, `ja`, `zh`), with no codes added or removed by the invoking layer.
6. THE Allergen_Extractor SHALL treat the confirmed allergen set as the value of the `confirmed` key returned by `allergen_rules.reconcile_allergens`, which equals the union of LLM-extracted and rules-engine-extracted categories restricted to `allergen_rules.PEAL_CATEGORIES`.
7. THE Allergen_Extractor SHALL produce display tags by invoking `allergen_rules.to_display_tags` with the confirmed allergen set, and SHALL produce diet tags by invoking `allergen_rules.derive_diet_tags` with the confirmed allergen set, using the unmodified outputs of those functions.

### Requirement 2: Asynchronous upload acceptance

**User Story:** As a restaurant staff member, I want to upload a menu file and receive an immediate acknowledgement, so that my request is not blocked by long-running OCR and AI processing.

#### Acceptance Criteria

1. WHEN a multipart menu file is received at `POST /menus/upload`, THE Upload_Handler SHALL store the raw file via `s3_service.upload_raw_file`.
2. WHEN a multipart menu file is received at `POST /menus/upload`, THE Upload_Handler SHALL create a DynamoDB item via `dynamo_service` with the Status_Field set to `processing`.
3. WHEN a multipart menu file is received at `POST /menus/upload`, THE Upload_Handler SHALL start an asynchronous Textract document analysis job.
4. WHEN the raw file is stored, the DynamoDB item is created, and the asynchronous Textract job is started, THE Upload_Handler SHALL respond with HTTP status 202 and an Upload_Id.
5. THE Upload_Handler SHALL respond within the API Gateway integration timeout of 30 seconds without waiting for OCR or Bedrock processing to complete.
6. IF the multipart request does not contain a file field, THEN THE Upload_Handler SHALL respond with HTTP status 400 and an error description indicating a required file field is missing, and SHALL NOT create a DynamoDB item.
7. IF the uploaded file exceeds the maximum accepted size of 10 MB, THEN THE Upload_Handler SHALL respond with HTTP status 400 and an error description indicating the file exceeds the size limit, and SHALL NOT store the file in S3 nor create a DynamoDB item.
8. IF storing the raw file, creating the DynamoDB item, or starting the Textract job fails, THEN THE Upload_Handler SHALL respond with HTTP status 500 and an error description, and SHALL NOT return an Upload_Id.

### Requirement 3: OCR completion processing

**User Story:** As a system operator, I want OCR results to be processed automatically when the Textract job completes, so that the pipeline advances without manual intervention.

#### Acceptance Criteria

1. WHEN the asynchronous Textract job completes, THE OCR_Processor SHALL be triggered by the Textract completion notification.
2. WHEN triggered, THE OCR_Processor SHALL retrieve the extracted text for the completed Textract job.
3. WHEN the extracted text is retrieved, THE OCR_Processor SHALL update the Status_Field of the corresponding DynamoDB item to `ocr_done`.
4. WHEN the extracted text is retrieved, THE OCR_Processor SHALL invoke the Allergen_Extractor with the extracted text and the associated Upload_Id.
5. IF the Textract completion notification reports a failed job, THEN THE OCR_Processor SHALL update the Status_Field to `ocr_failed` and record an error description identifying the failure cause on the DynamoDB item, retaining the existing Upload_Id.
6. IF retrieval of the extracted text fails, THEN THE OCR_Processor SHALL update the Status_Field to `ocr_failed` and record an error description identifying the retrieval failure on the DynamoDB item.
7. IF the DynamoDB item corresponding to the completed Textract job cannot be located from the job association, THEN THE OCR_Processor SHALL record an error indicating the association could not be resolved and SHALL NOT invoke the Allergen_Extractor.
8. IF a completion notification is received for a Textract job whose DynamoDB item Status_Field is already `ocr_done`, THEN THE OCR_Processor SHALL discard the duplicate notification without re-invoking the Allergen_Extractor and without modifying the Status_Field.

### Requirement 4: Allergen extraction and reconciliation

**User Story:** As a diner, I want dish allergen tags to reflect both the AI extraction and the deterministic rules engine, so that allergens are not under-declared.

#### Acceptance Criteria

1. WHEN invoked by the OCR_Processor, THE Allergen_Extractor SHALL update the Status_Field of the corresponding DynamoDB item to `analyzing`.
2. WHEN processing a dish, THE Allergen_Extractor SHALL compute the confirmed category set as the union of the categories returned by the Bedrock LLM extraction and the categories returned by the deterministic rules engine, discarding any value that is not a member of PEAL_Categories.
3. WHEN reconciliation completes for a dish, THE Allergen_Extractor SHALL invoke the Translator exactly once with the reconciled dish data and the associated Upload_Id before returning from the pipeline step.
4. WHEN reconciliation completes for a dish, THE Allergen_Extractor SHALL record on the dish data the LLM-only categories (confirmed by the LLM but not by the rules engine) and the rules-only categories (confirmed by the rules engine but not by the LLM), so a human reviewer can inspect the disagreements.
5. IF Bedrock allergen extraction is unavailable, THEN THE Allergen_Extractor SHALL obtain allergen categories from the offline fallback provided by `bedrock_service`, continue reconciliation and Translator invocation as in the nominal path, and record on the dish data a source indicator distinguishing the fallback from a live Bedrock result.

### Requirement 5: Translation and finalization

**User Story:** As an international diner, I want each dish presented with translations in all supported languages, so that I can understand the menu in my language.

#### Acceptance Criteria

1. WHEN invoked by the Allergen_Extractor, THE Translator SHALL update the Status_Field of the corresponding DynamoDB item to `translating`.
2. WHEN processing a dish, THE Translator SHALL produce translations for every code in Configured_Languages (exactly the four codes `es`, `de`, `ja`, `zh`) via `bedrock_service.translate_dish`, such that the resulting Translations_Map contains exactly one entry per configured language code.
3. WHEN translations are produced, THE Translator SHALL embed the translations directly in the dish item as a Translations_Map keyed by language code.
4. WHEN the dish item is written with its allergen data and Translations_Map, THE Translator SHALL update the Status_Field to `ready`.
5. THE Translator SHALL persist the final dish item via `dynamo_service`.
6. IF Bedrock translation is unavailable for a language code, THEN THE Translator SHALL populate that code's Translations_Map entry using the fallback provided by `bedrock_service` and SHALL continue processing the remaining language codes without terminating the pipeline.
7. IF persisting the final dish item via `dynamo_service` fails, THEN THE Translator SHALL set the Status_Field to a value indicating failure and SHALL NOT report the dish as `ready`.

### Requirement 6: Menu read and status polling

**User Story:** As a diner and as a dashboard user, I want to read a restaurant's menu and check upload processing status, so that I can view dishes and monitor progress.

#### Acceptance Criteria

1. WHEN a request is received at `GET /menus/{restaurantId}` for an existing restaurant that has one or more dishes, THE Menu_Read_API SHALL return all dish items for the specified restaurant within 2 seconds.
2. WHEN returning dish items for a restaurant, THE Menu_Read_API SHALL include each dish's embedded Translations_Map in a single read, retrieving all translations for a dish within the same item without issuing a separate query per translation.
3. WHEN a request is received at `GET /menus/{restaurantId}` for an existing restaurant that has no dishes, THE Menu_Read_API SHALL respond with HTTP status 200 and an empty dish-item collection.
4. WHEN a request is received at `GET /menus/{uploadId}/status` for an existing Upload_Id, THE Menu_Read_API SHALL return the current Status_Field value for the specified Upload_Id within 2 seconds.
5. THE Menu_Read_API SHALL perform only read operations against DynamoDB.
6. IF a requested restaurantId or Upload_Id does not exist, THEN THE Menu_Read_API SHALL respond with HTTP status 404 and an error description indicating the resource was not found, and SHALL make no change to stored data.
7. IF a request is received with a missing or malformed restaurantId or Upload_Id path parameter, THEN THE Menu_Read_API SHALL reject the request with HTTP status 400 and an error description indicating the invalid parameter, and SHALL make no change to stored data.

### Requirement 7: Human-in-the-loop allergen override

**User Story:** As a kitchen manager, I want to correct a dish's allergen tags or translations after AI processing, so that the published menu reflects verified information.

#### Acceptance Criteria

1. IF a request is received at `PATCH /menus/{menuId}/items/{itemId}` that includes corrected confirmed allergens, THEN THE Menu_Edit_API SHALL retain only the provided allergens that are members of PEAL_Categories, silently discarding any value that is not a member of PEAL_Categories.
2. IF a request includes corrected confirmed allergens, THEN THE Menu_Edit_API SHALL recompute the display tags via `allergen_rules.to_display_tags` and the diet tags via `allergen_rules.derive_diet_tags` from the retained confirmed allergen set.
3. IF a request includes corrected translations, THEN THE Menu_Edit_API SHALL merge the provided translations into the dish's Translations_Map, preserving existing entries for language codes not present in the request.
4. IF a request includes a corrected name or description, THEN THE Menu_Edit_API SHALL update the corresponding field on the dish item.
5. WHEN an override request is applied to an existing dish item, THE Menu_Edit_API SHALL set the dish status to `human_verified`.
6. WHEN an override request is applied, THE Menu_Edit_API SHALL persist the updated dish item via `dynamo_service`.
7. IF the referenced dish item does not exist, THEN THE Menu_Edit_API SHALL respond with HTTP status 404 and an error description indicating the dish item was not found, and SHALL make no change to stored data.

### Requirement 8: Status-field asynchronous coordination

**User Story:** As a dashboard user, I want to poll a single status value that advances as processing proceeds, so that I can track progress without a workflow orchestration service.

#### Acceptance Criteria

1. THE pipeline SHALL coordinate progress using the Status_Field on the DynamoDB item rather than a workflow orchestration service.
2. WHEN a pipeline stage completes its work, THE responsible Lambda function SHALL update the Status_Field to the value corresponding to that completed stage within 5 seconds of stage completion.
3. THE Status_Field SHALL only advance forward through the ordered values `processing`, `ocr_done`, `analyzing`, `translating`, and `ready`, and SHALL NOT transition to any value earlier in this sequence than its current value.
4. IF a Lambda function attempts to update the Status_Field to a value that is not the immediate successor of the current value in the ordered sequence, THEN THE responsible Lambda function SHALL reject the update and leave the Status_Field unchanged.
5. WHEN the dashboard polls `GET /menus/{uploadId}/status`, THE Menu_Read_API SHALL return the most recently persisted Status_Field value within 2 seconds.
6. IF the dashboard polls `GET /menus/{uploadId}/status` with an uploadId that has no corresponding DynamoDB item, THEN THE Menu_Read_API SHALL return an error response indicating the uploadId was not found and SHALL NOT return a Status_Field value.
7. IF a pipeline stage fails before persisting its Status_Field update, THEN THE responsible Lambda function SHALL set the Status_Field to a value indicating failure and SHALL preserve the previously persisted stage value in a separate attribute for diagnostic reference.

### Requirement 9: Scoped IAM execution roles

**User Story:** As a security reviewer, I want each Lambda function to have only the permissions it needs, so that the blast radius of any single function is minimized.

#### Acceptance Criteria

1. THE Infrastructure_Definition SHALL define exactly one dedicated Execution_Role per Lambda function, with no Execution_Role attached to more than one function.
2. THE Upload_Handler Execution_Role SHALL grant exactly `s3:PutObject` and `textract:StartDocumentAnalysis` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
3. THE OCR_Processor Execution_Role SHALL grant exactly `textract:GetDocumentAnalysis` and `lambda:InvokeFunction` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
4. THE Allergen_Extractor Execution_Role SHALL grant exactly `bedrock:InvokeModel` and `lambda:InvokeFunction` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
5. THE Translator Execution_Role SHALL grant exactly `bedrock:InvokeModel`, `translate:TranslateText`, `dynamodb:PutItem`, and `dynamodb:UpdateItem` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
6. THE Menu_Read_API Execution_Role SHALL grant exactly `dynamodb:GetItem` and `dynamodb:Query` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
7. THE Menu_Edit_API Execution_Role SHALL grant exactly `dynamodb:UpdateItem` and no other service or resource permissions beyond the baseline CloudWatch Logs permissions.
8. THE baseline CloudWatch Logs permissions SHALL comprise `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` scoped to the function's own log group.
9. WHERE an Execution_Role grants a DynamoDB, S3, Textract, Bedrock, or Translate permission, THE Infrastructure_Definition SHALL scope that permission to the specific target resource ARNs where the action supports resource-level permissions, rather than using a wildcard resource.
10. IF an Execution_Role is defined with any action beyond those enumerated for its function plus the baseline CloudWatch Logs permissions, THEN THE Infrastructure_Definition SHALL be considered non-conforming to this requirement.

### Requirement 10: Terraform infrastructure migration

**User Story:** As an infrastructure engineer, I want the Terraform configuration to provision the serverless architecture, so that a single apply deploys the six functions and their routing.

#### Acceptance Criteria

1. THE Infrastructure_Definition SHALL define all six Lambda functions (Upload_Handler, OCR_Processor, Allergen_Extractor, Translator, Menu_Read_API, Menu_Edit_API) in `terraform/lambda.tf`.
2. THE Infrastructure_Definition SHALL define the API Gateway HTTP API in `terraform/api_gateway.tf` and SHALL route each HTTP-exposed path (`POST /menus/upload`, `GET /menus/{restaurantId}`, `GET /menus/{uploadId}/status`, and `PATCH /menus/{menuId}/items/{itemId}`) to its corresponding Lambda function.
3. THE Infrastructure_Definition SHALL NOT expose the OCR_Processor, Allergen_Extractor, or Translator through the API Gateway HTTP API, as these are invoked by event notification or direct Lambda invocation.
4. THE Infrastructure_Definition SHALL NOT contain the Elastic Beanstalk resources previously defined in `terraform/beanstalk.tf`.
5. THE Infrastructure_Definition SHALL define the six scoped Execution_Roles in `terraform/iam.tf` and SHALL NOT retain the single EC2 instance role or the Elastic Beanstalk service role.
6. THE Infrastructure_Definition SHALL leave `terraform/dynamodb.tf`, `terraform/s3.tf`, and `terraform/cognito.tf` byte-for-byte unchanged from their pre-migration content.
7. WHERE HTTP routing is provisioned, THE Infrastructure_Definition SHALL use an API Gateway HTTP API (v2) rather than a REST API.
8. WHEN `terraform apply` is run once against the migrated Infrastructure_Definition, THE apply SHALL provision all six Lambda functions and their HTTP routes without requiring a separate manual deployment step.

### Requirement 11: DynamoDB embedded translations schema

**User Story:** As a menu reader, I want each dish item to embed its translations, so that serving a full menu does not require additional queries per dish.

#### Acceptance Criteria

1. THE dish item SHALL store all translations for the codes in Configured_Languages as a single Translations_Map attribute, keyed by language code, embedded within the dish item.
2. THE dish item SHALL NOT store translations as separate DynamoDB items.
3. WHEN a menu containing one or more dishes is served, THE Menu_Read_API SHALL retrieve each dish's Translations_Map from that dish item using exactly one read per dish and zero additional per-translation queries.
4. WHEN the Translator finalizes a dish, THE Translator SHALL write the Translations_Map containing one entry for every language code in Configured_Languages.
5. IF the Translator cannot produce a translation for a language code in Configured_Languages at finalization, THEN THE Translator SHALL write a placeholder entry for that code marked as untranslated and SHALL indicate the finalization as incomplete.
6. WHILE a dish's Translations_Map is missing one or more codes in Configured_Languages, THE Menu_Read_API SHALL return the available translation entries and indicate, per missing code, that the translation is unavailable.

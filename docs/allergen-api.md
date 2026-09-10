# Allergen & Compliance Service API

This document is for consumers of the **Allergen Extraction + Compliance
Verification** workstream — the deliverable that stays independent of OCR,
Translation and UI so the rest of the team can consume a clean API.

## Which parts are LLM / RAG / Deterministic

| Concern | Engine | Where |
|---|---|---|
| Allergen extraction (primary signal) | **LLM via Bedrock Tool Use** | `services/bedrock_service.py` — `extract_allergens_tool_use` |
| Allergen extraction (cross-check / fallback) | **Deterministic** keyword rules | `services/allergen_rules.py` — `scan_text_for_allergens` |
| Regulatory retrieval | **RAG** — Bedrock Knowledge Base (AWS) or bundled `docs/` (local) | `services/allergen_service.py` — `retrieve_context` / `_retrieve_kb_aws` / `_search_kb_local` |
| Compliance verdict & declarations | **Deterministic** rules engine | `services/allergen_service.py` — `verify` / `evaluate_compliance` |
| Public orchestration / reconciliation | Deterministic merge (Bedrock + rules + RAG) | `services/allergen_service.py` — `extract` / `verify_pipeline` / `_run_pipeline` |

The final regulatory decision is **never made by the LLM alone** — the
deterministic rules engine reconciles and issues declaration / warning /
advisory statements.

## Contracts (defined in `services/allergen_service.py`)

- `ExtractRequest` → `{dish_name, description}`
- `Allergen` → `{name, evidence, status, confidence}`
  - `status` ∈ `CONFIRMED | POSSIBLE | UNKNOWN`. `POSSIBLE`/`UNKNOWN` are never
    silently reported as compliant.
- `ExtractionResult` → `{dish_name, allergens[], engine, llm_reasoning?}`
- `ComplianceResult` → `{dish_name, allergens[], compliance, sources}`
  - `compliance.status` ∈ `COMPLIANT | ACTION_REQUIRED | UNVERIFIED`
  - `compliance` distinguishes `allergen_declarations`,
    `warning_statements`, `advisory_statements` (never a single `warning`).

## HTTP API

Routes live in `app/application.py` and delegate to `services/allergen_service.py`.
Both accept either `dish_name` or the legacy `name` field.

```
POST /api/allergens/extract
  { "dish_name": "...", "description": "..." }
  -> { "dish_name": "...", "allergens": [
        {"name": "Cashew", "evidence": "cashew nuts", "status": "CONFIRMED", "confidence": 0.99}, ...] }

POST /api/compliance/verify
  { "dish_name": "...", "description": "...", "allergens": [ ...optional in-advance extraction... ] }
  -> { "dish_name": "...", "compliance": {
        "status": "COMPLIANT",
        "allergen_declarations": ["Contains Cashews", "Contains Milk"],
        "warning_statements": [],
        "advisory_statements": [] }, "sources": [ ... ] }
```

## Function Calling / Tool Use

`services/bedrock_service.py::extract_allergens_tool_use` drives extraction
through a real Claude tool call. The JSON Schema in `_EXTRACT_ALLERGEN_TOOL`
forces a structured `toolUse.input` (per-allergen name/evidence/status/
confidence) instead of free-form prose. If Bedrock is unavailable (broken
credentials, no model access, offline) it degrades to the deterministic engine,
so the pipeline never hard-fails.

## Running & testing

Local (deterministic path, no AWS):

```powershell
$env:LOCAL_MODE = "true"
cd app
.\.venv\Scripts\python.exe application.py
# then: Invoke-WebRequest http://localhost:8000/api/allergens/extract -Method POST -Body '{"dish_name":"...","description":"..."}' -ContentType 'application/json'
```

Real AWS path: set `LOCAL_MODE=false` (module default), set the per-service
region variables (see `setup_aws_env.ps1`), and use valid IAM credentials in
`~/.aws/credentials`. `BEDROCK_MODEL_ID` must be an inference-profile id such
as `au.anthropic.claude-opus-4-6-v1` (a bare foundation-model id is rejected).

## Regulatory sources

The canonical allergen taxonomy follows the NZ MPI page
[Allergen declarations, warnings and advisory statements on food labels](
https://www.mpi.govt.nz/food-business/labelling-composition-food-drinks/allergen-declarations-warnings-and-advisory-statements-on-food-labels)
and FSANZ Standard 1.2.3. Bundled reference copies used for local retrieval are
in `docs/` (e.g. `nz_peal_allergens.md` and the MPI/FSANZ PDFs).

## Known limitations

- The deterministic keyword engine is a starting point and must be reviewed by a
  food-safety qualified person before commercial use; it is not exhaustive.
- Tree nuts are declared individually (per NZ MPI); the engine intentionally does
  not collapse them into a generic "Nuts".
- Uncertain/sulphite cases are surfaced as `POSSIBLE` / `ACTION_REQUIRED`,
  never auto-confirmed, pending human review.

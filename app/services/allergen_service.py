"""
allergen_service.py — Allergen Extraction & Compliance Verification (combined module)

This module consolidates the two core workstreams, "Allergen Extraction" and
"Compliance Verification", into a single file to avoid excessive file splitting
and keep collaboration simple. It contains:

  1. Data contracts   Allergen / ExtractionResult / ComplianceResult
  2. Deterministic     extract_allergens_deterministic()  -- pure Python, no AWS
  3. Orchestration     extract() / verify()               -- called by API endpoints
  4. Rules engine      evaluate_compliance()              -- deterministic NZ PEAL verdict
  5. RAG retrieval     retrieve_context()                 -- Bedrock KB / local fallback
  6. Pipeline merge    verify_pipeline()                  -- LLM + rules + RAG union

Design principle: Claude handles "understanding and extraction", the rules engine
handles the "decision". The LLM never decides compliance on its own.
"""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from .allergen_rules import PEAL_CATEGORIES, scan_text_for_allergens

logger = logging.getLogger(__name__)

# ==========================================================================
# Constants
# ==========================================================================
# Extraction certainty tiers
CONFIRMED = "CONFIRMED"
POSSIBLE = "POSSIBLE"
UNKNOWN = "UNKNOWN"
_STATUS_TIERS = [CONFIRMED, POSSIBLE, UNKNOWN]

# Compliance verdicts
COMPLIANT = "COMPLIANT"
ACTION_REQUIRED = "ACTION_REQUIRED"
UNVERIFIED = "UNVERIFIED"

# Default region for the Bedrock allergen extraction + Knowledge Base glue in
# this module is ap-southeast-2 (where the KB and inference profile live).
AWS_REGION = os.environ.get(
    "BEDROCK_REGION", os.environ.get("AWS_REGION", "ap-southeast-2")
)
# Knowledge Base client region: separate override so KB can be in a different
# region than the app's other resources (DynamoDB/S3 default to us-east-1).
KB_REGION = os.environ.get("KB_REGION", AWS_REGION)
LOCAL_MODE = os.environ.get("LOCAL_MODE", "false").lower() == "true"
# Local regulatory docs directory: reuse the repo-root docs/ (each ## heading is a PEAL category)
KB_DOCS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs")
)


# ==========================================================================
# 1. Data contracts
# ==========================================================================
@dataclass
class ExtractRequest:
    dish_name: str
    description: str = ""

    @property
    def text(self) -> str:
        return f"{self.dish_name} {self.description}".strip()


@dataclass
class Allergen:
    """A single detected allergen."""
    name: str
    status: str = CONFIRMED
    evidence: str = ""
    confidence: float = 0.0
    reason: str = ""

    def to_json(self) -> Dict[str, Any]:
        d = {"name": self.name, "status": self.status, "evidence": self.evidence}
        if self.confidence:
            d["confidence"] = round(self.confidence, 4)
        if self.reason:
            d["reason"] = self.reason
        return d


@dataclass
class ExtractionResult:
    """Allergen extraction result."""
    dish_name: str
    allergens: List[Allergen] = field(default_factory=list)
    engine: str = "rules"
    llm_reasoning: str = ""

    def to_json(self) -> Dict[str, Any]:
        d = {
            "dish_name": self.dish_name,
            "allergens": [a.to_json() for a in self.allergens],
            "engine": self.engine,
        }
        if self.llm_reasoning:
            d["llm_reasoning"] = self.llm_reasoning
        return d

    @property
    def confirmed_names(self) -> List[str]:
        return [a.name for a in self.allergens if a.status == CONFIRMED]


@dataclass
class ComplianceResult:
    """Compliance verification result."""
    dish_name: str
    status: str = UNVERIFIED
    allergens: List[Allergen] = field(default_factory=list)
    allergen_declarations: List[str] = field(default_factory=list)
    warning_statements: List[str] = field(default_factory=list)
    advisory_statements: List[str] = field(default_factory=list)
    sources: List[Dict[str, str]] = field(default_factory=list)
    reasoning: str = ""

    def to_json(self) -> Dict[str, Any]:
        return {
            "dish_name": self.dish_name,
            "allergens": [a.to_json() for a in self.allergens],
            "compliance": {
                "status": self.status,
                "allergen_declarations": list(self.allergen_declarations),
                "warning_statements": list(self.warning_statements),
                "advisory_statements": list(self.advisory_statements),
            },
            "sources": [dict(s) for s in self.sources],
            **({"reasoning": self.reasoning} if self.reasoning else {}),
        }


# ==========================================================================
# 2. Deterministic extraction (keyword matching, no LLM)
# ==========================================================================
# Ingredient -> allergen mapping (with evidence / tier / confidence)
_TERM_RULES: Dict[str, dict] = {
    # Gluten
    "wheat": {"name": "Gluten (Wheat)", "evidence": "wheat", "tier": CONFIRMED, "confidence": 1.0},
    "flour": {"name": "Gluten (Wheat)", "evidence": "flour", "tier": CONFIRMED, "confidence": 1.0},
    "bread": {"name": "Gluten (Wheat)", "evidence": "bread", "tier": CONFIRMED, "confidence": 1.0},
    "pasta": {"name": "Gluten (Wheat)", "evidence": "pasta", "tier": CONFIRMED, "confidence": 1.0},
    "noodle": {"name": "Gluten (Wheat)", "evidence": "noodle", "tier": CONFIRMED, "confidence": 1.0},
    "batter": {"name": "Gluten (Wheat)", "evidence": "batter", "tier": CONFIRMED, "confidence": 1.0},
    "breadcrumb": {"name": "Gluten (Wheat)", "evidence": "breadcrumb", "tier": CONFIRMED, "confidence": 1.0},
    "couscous": {"name": "Gluten (Wheat)", "evidence": "couscous", "tier": CONFIRMED, "confidence": 1.0},
    "barley": {"name": "Gluten (Barley)", "evidence": "barley", "tier": CONFIRMED, "confidence": 1.0},
    "rye": {"name": "Gluten (Rye)", "evidence": "rye", "tier": CONFIRMED, "confidence": 1.0},
    "oat": {"name": "Gluten (Oats)", "evidence": "oat", "tier": CONFIRMED, "confidence": 1.0},
    "oats": {"name": "Gluten (Oats)", "evidence": "oats", "tier": CONFIRMED, "confidence": 1.0},
    "malt": {"name": "Gluten (Barley)", "evidence": "malt", "tier": CONFIRMED, "confidence": 1.0},
    "soy sauce": {"name": "Gluten (Wheat)", "evidence": "soy sauce (contains wheat)", "tier": CONFIRMED, "confidence": 0.8},
    # Crustacea
    "shrimp": {"name": "Crustacea", "evidence": "shrimp", "tier": CONFIRMED, "confidence": 1.0},
    "prawn": {"name": "Crustacea", "evidence": "prawn", "tier": CONFIRMED, "confidence": 1.0},
    "crab": {"name": "Crustacea", "evidence": "crab", "tier": CONFIRMED, "confidence": 1.0},
    "lobster": {"name": "Crustacea", "evidence": "lobster", "tier": CONFIRMED, "confidence": 1.0},
    "crayfish": {"name": "Crustacea", "evidence": "crayfish", "tier": CONFIRMED, "confidence": 1.0},
    # Molluscs
    "clam": {"name": "Molluscs", "evidence": "clam", "tier": CONFIRMED, "confidence": 1.0},
    "mussel": {"name": "Molluscs", "evidence": "mussel", "tier": CONFIRMED, "confidence": 1.0},
    "oyster": {"name": "Molluscs", "evidence": "oyster", "tier": CONFIRMED, "confidence": 1.0},
    "scallop": {"name": "Molluscs", "evidence": "scallop", "tier": CONFIRMED, "confidence": 1.0},
    "squid": {"name": "Molluscs", "evidence": "squid", "tier": CONFIRMED, "confidence": 1.0},
    "calamari": {"name": "Molluscs", "evidence": "calamari", "tier": CONFIRMED, "confidence": 1.0},
    # Egg
    "egg": {"name": "Egg", "evidence": "egg", "tier": CONFIRMED, "confidence": 1.0},
    "mayonnaise": {"name": "Egg", "evidence": "mayonnaise (contains egg)", "tier": CONFIRMED, "confidence": 0.9},
    "aioli": {"name": "Egg", "evidence": "aioli (contains egg)", "tier": CONFIRMED, "confidence": 0.9},
    # Fish
    "fish": {"name": "Fish", "evidence": "fish", "tier": CONFIRMED, "confidence": 1.0},
    "salmon": {"name": "Fish", "evidence": "salmon", "tier": CONFIRMED, "confidence": 1.0},
    "tuna": {"name": "Fish", "evidence": "tuna", "tier": CONFIRMED, "confidence": 1.0},
    "anchov": {"name": "Fish", "evidence": "anchovy", "tier": CONFIRMED, "confidence": 1.0},
    "cod": {"name": "Fish", "evidence": "cod", "tier": CONFIRMED, "confidence": 1.0},
    "snapper": {"name": "Fish", "evidence": "snapper", "tier": CONFIRMED, "confidence": 1.0},
    "fish sauce": {"name": "Fish", "evidence": "fish sauce", "tier": CONFIRMED, "confidence": 1.0},
    "chowder": {"name": "Fish", "evidence": "chowder (typically contains fish)", "tier": POSSIBLE, "confidence": 0.7},
    # Milk
    "milk": {"name": "Milk", "evidence": "milk", "tier": CONFIRMED, "confidence": 1.0},
    "cream": {"name": "Milk", "evidence": "cream", "tier": CONFIRMED, "confidence": 1.0},
    "butter": {"name": "Milk", "evidence": "butter", "tier": CONFIRMED, "confidence": 1.0},
    "cheese": {"name": "Milk", "evidence": "cheese", "tier": CONFIRMED, "confidence": 1.0},
    "yoghurt": {"name": "Milk", "evidence": "yoghurt", "tier": CONFIRMED, "confidence": 1.0},
    "yogurt": {"name": "Milk", "evidence": "yogurt", "tier": CONFIRMED, "confidence": 1.0},
    "parmesan": {"name": "Milk", "evidence": "parmesan", "tier": CONFIRMED, "confidence": 1.0},
    "custard": {"name": "Milk", "evidence": "custard (contains milk)", "tier": CONFIRMED, "confidence": 0.9},
    "ghee": {"name": "Milk", "evidence": "ghee (clarified butter)", "tier": CONFIRMED, "confidence": 1.0},
    # Peanuts
    "peanut": {"name": "Peanuts", "evidence": "peanut", "tier": CONFIRMED, "confidence": 1.0},
    "groundnut": {"name": "Peanuts", "evidence": "groundnut (peanut)", "tier": CONFIRMED, "confidence": 1.0},
    "satay": {"name": "Peanuts", "evidence": "satay sauce (usually peanut)", "tier": CONFIRMED, "confidence": 0.9},
    # Soy
    "soy": {"name": "Soybeans", "evidence": "soy", "tier": CONFIRMED, "confidence": 1.0},
    "soya": {"name": "Soybeans", "evidence": "soya", "tier": CONFIRMED, "confidence": 1.0},
    "tofu": {"name": "Soybeans", "evidence": "tofu (soy)", "tier": CONFIRMED, "confidence": 1.0},
    "edamame": {"name": "Soybeans", "evidence": "edamame (soy)", "tier": CONFIRMED, "confidence": 1.0},
    "miso": {"name": "Soybeans", "evidence": "miso (soy)", "tier": CONFIRMED, "confidence": 1.0},
    "tempeh": {"name": "Soybeans", "evidence": "tempeh (soy)", "tier": CONFIRMED, "confidence": 1.0},
    # Tree nuts (named individually; mapped back to the Tree Nuts category for the union)
    "almond": {"name": "Almond", "evidence": "almond", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "cashew": {"name": "Cashew", "evidence": "cashew", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "walnut": {"name": "Walnut", "evidence": "walnut", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "hazelnut": {"name": "Hazelnut", "evidence": "hazelnut", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "pistachio": {"name": "Pistachio", "evidence": "pistachio", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "pecan": {"name": "Pecan", "evidence": "pecan", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "macadamia": {"name": "Macadamia", "evidence": "macadamia", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "pine nut": {"name": "Pine Nut", "evidence": "pine nut", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    "brazil nut": {"name": "Brazil Nut", "evidence": "brazil nut", "tier": CONFIRMED, "confidence": 1.0, "category": "Tree Nuts"},
    # Sesame
    "sesame": {"name": "Sesame", "evidence": "sesame", "tier": CONFIRMED, "confidence": 1.0},
    "tahini": {"name": "Sesame", "evidence": "tahini (sesame)", "tier": CONFIRMED, "confidence": 1.0},
    # Lupin
    "lupin": {"name": "Lupin", "evidence": "lupin", "tier": CONFIRMED, "confidence": 1.0},
    # Sulphites (POSSIBLE when concentration is unknown)
    "sulphite": {"name": "Added Sulphites", "evidence": "sulphite (concentration unknown)", "tier": POSSIBLE, "confidence": 0.6},
    "sulfite": {"name": "Added Sulphites", "evidence": "sulfite (concentration unknown)", "tier": POSSIBLE, "confidence": 0.6},
    "dried fruit": {"name": "Added Sulphites", "evidence": "dried fruit (may contain sulphites)", "tier": POSSIBLE, "confidence": 0.6},
}

# Sort by length desc: multi-word phrases ("soy sauce") match before their prefix words
_SORTED_TERMS = sorted(_TERM_RULES.items(), key=lambda x: -len(x[0]))
# "eggplant" must not trigger egg
_FALSE_POSITIVES = {"egg": "plant"}


def extract_allergens_deterministic(dish_name: str, description: str = "") -> ExtractionResult:
    """Deterministic keyword extraction (no LLM). Also reused by bedrock_service's offline fallback."""
    text = f"{dish_name} {description}".strip()
    lowered = text.lower()
    found: Dict[str, Allergen] = {}

    for term, rule in _SORTED_TERMS:
        if " " in term:  # multi-word phrase: substring match
            if term in lowered:
                _merge(found, rule)
            continue
        guard = _FALSE_POSITIVES.get(term)  # single word: word-boundary match
        m = re.search(rf"\b{re.escape(term)}", lowered)
        if not m:
            continue
        if guard and lowered[m.end():].startswith(guard):
            continue
        _merge(found, rule)

    allergens = sorted(found.values(), key=lambda a: (-a.confidence, a.name))
    return ExtractionResult(
        dish_name=(dish_name or "").strip(),
        allergens=allergens,
        engine="rules",
        llm_reasoning="deterministic keyword rules",
    )


def _merge(found: Dict[str, Allergen], rule: dict):
    """Merge same-named allergens, keeping the highest confidence and longest evidence."""
    name = rule["name"]
    if name in found:
        existing = found[name]
        if rule["tier"] == CONFIRMED:
            existing.status = CONFIRMED
        existing.confidence = max(existing.confidence, rule["confidence"])
        if len(rule["evidence"]) > len(existing.evidence):
            existing.evidence = rule["evidence"]
    else:
        found[name] = Allergen(
            name=name, status=rule["tier"],
            evidence=rule["evidence"], confidence=rule["confidence"],
        )


# ==========================================================================
# 3. Orchestration entry points (called by API endpoints)
# ==========================================================================
def _bedrock():
    """Lazy-import bedrock_service so the offline path does not depend on boto3."""
    from . import bedrock_service
    return bedrock_service


def extract(dish_name: str, description: str = "", *, use_bedrock: bool = True) -> ExtractionResult:
    """Extraction entry: prefer Claude Function Calling reconciled with the rules engine; fall back if unavailable.

    Bedrock is the primary signal, but when it is unreachable (bad
    credentials, no model access, model-id misconfiguration, offline) we
    degrade to the deterministic keyword engine so the API returns a useful
    result instead of a 500 - the deterministic engine still runs, and the
    merge that decides declarations is downstream anyway.
    """
    req = ExtractRequest(dish_name=dish_name, description=description)
    if use_bedrock:
        try:
            bs = _bedrock()
            if not bs.LOCAL_MODE:
                return _extract_via_bedrock(req)
        except Exception as exc:
            logger.warning("Bedrock unavailable, using deterministic engine: %s", exc)
    return extract_allergens_deterministic(req.dish_name, req.description)


def verify(dish_name: str, allergens=None, description: str = "") -> ComplianceResult:
    """Compliance entry: extract deterministically when no allergens are supplied, then run the rules engine."""
    if allergens is None:
        extraction = extract_allergens_deterministic(dish_name, description)
    elif not hasattr(allergens, "allergens"):  # list of strings -> ExtractionResult
        extraction = ExtractionResult(
            dish_name=(dish_name or "").strip(),
            allergens=[Allergen(name=n, status=CONFIRMED) for n in allergens],
        )
    else:
        extraction = allergens
    decision = evaluate_compliance(extraction)
    logger.info(
        "compliance: dish=%r status=%s declarations=%d",
        decision.dish_name, decision.status, len(decision.allergen_declarations),
    )
    return decision


def _extract_via_bedrock(req: ExtractRequest) -> ExtractionResult:
    """Bedrock LLM extraction reconciled with the deterministic engine (Bedrock is the primary signal)."""
    # Use the existing extract_allergens function (returns {'categories': [...], 'reasoning': str})
    raw = _bedrock().extract_allergens(req.dish_name, req.description)
    deterministic = extract_allergens_deterministic(req.dish_name, req.description)

    final: List[Allergen] = []
    by_name: Dict[str, int] = {}

    def _add(a: Allergen):
        key = a.name.lower()
        if key in by_name:
            existing = final[by_name[key]]
            if a.status == CONFIRMED:
                existing.status = CONFIRMED
            existing.confidence = max(existing.confidence, a.confidence)
            if len(a.evidence) > len(existing.evidence):
                existing.evidence = a.evidence
        else:
            by_name[key] = len(final)
            final.append(a)

    # Convert categories format to allergens format
    for category_name in (raw.get("categories") or []):  # Bedrock returns categories
        _add(Allergen(
            name=str(category_name).strip(),
            status=CONFIRMED,  # Bedrock categories are confirmed
            evidence=f"Detected by Bedrock LLM: {raw.get('reasoning', '')}",
            confidence=0.8,  # Default confidence for Bedrock detection
            reason=str(raw.get("reasoning", "")).strip(),
        ))
    for a in deterministic.allergens:  # deterministic engine fills gaps
        _add(a)

    final.sort(key=lambda a: (-a.confidence, a.name))
    return ExtractionResult(
        dish_name=req.dish_name, allergens=final,
        engine=raw.get("source", "bedrock-tool-use"),
        llm_reasoning=raw.get("reasoning", ""),
    )


# ==========================================================================
# 4. Compliance rules engine (deterministic NZ PEAL verdict)
# ==========================================================================
_DECLARATION_TEXT: Dict[str, str] = {
    "Gluten (Cereals)": "Contains Gluten (Wheat/Cereals)",
    "Gluten (Wheat)": "Contains Wheat/Gluten",
    "Gluten (Barley)": "Contains Barley (Gluten)",
    "Gluten (Rye)": "Contains Rye (Gluten)",
    "Gluten (Oats)": "Contains Oats (Gluten)",
    "Crustacea": "Contains Crustacea", "Molluscs": "Contains Molluscs",
    "Egg": "Contains Egg", "Fish": "Contains Fish", "Milk": "Contains Milk",
    "Peanuts": "Contains Peanuts", "Soy": "Contains Soy", "Soybeans": "Contains Soy",
    "Almond": "Contains Almonds", "Cashew": "Contains Cashews", "Walnut": "Contains Walnuts",
    "Hazelnut": "Contains Hazelnuts", "Pistachio": "Contains Pistachios",
    "Pecan": "Contains Pecans", "Macadamia": "Contains Macadamias",
    "Brazil Nut": "Contains Brazil Nuts", "Pine Nut": "Contains Pine Nuts",
    "Tree Nuts": "Contains Tree Nuts", "Sesame": "Contains Sesame",
    "Lupin": "Contains Lupin", "Added Sulphites": "Contains Added Sulphites",
}

_SOURCES: List[Dict[str, str]] = [
    {"title": "NZ MPI - Allergen declarations, warnings and advisory statements",
     "url": "https://www.mpi.govt.nz/food-business/labelling-composition-food-drinks/allergen-declarations-warnings-and-advisory-statements-on-food-labels"},
    {"title": "FSANZ Standard 1.2.3 - Warning statements, advisory statements and declarations"},
]


def evaluate_compliance(extraction: ExtractionResult) -> ComplianceResult:
    """Deterministic verdict: CONFIRMED -> declaration; POSSIBLE -> ACTION_REQUIRED; Unknown -> UNVERIFIED."""
    allergens = list(extraction.allergens)
    confirmed = [a for a in allergens if a.status == CONFIRMED]
    possible = [a for a in allergens if a.status == POSSIBLE]
    unknown = [a for a in allergens if a.name == "Unknown"]

    declarations: List[str] = []
    seen: set = set()
    for a in confirmed:
        text = _DECLARATION_TEXT.get(a.name, f"Contains {a.name}")
        if text not in seen:
            seen.add(text)
            declarations.append(text)

    warnings: List[str] = []
    for a in possible:
        if a.name == "Added Sulphites":
            warnings.append(
                "WARNING: Added sulphites may be present; confirm whether "
                "concentration meets the 10 mg/kg declaration threshold."
            )

    advisory: List[str] = []
    if possible and not any(a.name == "Added Sulphites" for a in possible):
        advisory.append(
            "Ingredients of unknown composition may contain a regulated "
            "allergen and should be confirmed before serving."
        )

    if unknown:
        status, reasoning = UNVERIFIED, "Unknown composition present; compliance cannot be confirmed."
    elif possible:
        status, reasoning = ACTION_REQUIRED, "One or more allergens are POSSIBLE; confirm before declaring compliant."
    else:
        status, reasoning = COMPLIANT, "All regulated allergens present are declared."

    return ComplianceResult(
        dish_name=extraction.dish_name, status=status, allergens=allergens,
        allergen_declarations=declarations, warning_statements=warnings,
        advisory_statements=advisory, sources=[dict(s) for s in _SOURCES],
        reasoning=reasoning,
    )


# ==========================================================================
# 5. RAG retrieval (Bedrock Knowledge Base, local fallback)
# ==========================================================================
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError  # noqa: F401
    _HAS_BOTO3 = True
except ImportError:
    _HAS_BOTO3 = False

_kb_client = None


def is_kb_available() -> bool:
    """True when KNOWLEDGE_BASE_ID is set and not in LOCAL_MODE."""
    return (not LOCAL_MODE) and bool(os.environ.get("KNOWLEDGE_BASE_ID"))


def _get_kb_client():
    global _kb_client
    if _kb_client is None:
        if not _HAS_BOTO3:
            raise RuntimeError("boto3 is required for AWS Knowledge Base access")
        _kb_client = boto3.client("bedrock-agent-runtime", region_name=KB_REGION)
    return _kb_client


def _load_kb_sections() -> List[Dict]:
    """Parse docs/*.md, splitting on ## headings (each heading is a PEAL category)."""
    sections: List[Dict] = []
    if not os.path.isdir(KB_DOCS_DIR):
        return sections
    for fname in sorted(os.listdir(KB_DOCS_DIR)):
        if not fname.endswith(".md"):
            continue
        try:
            with open(os.path.join(KB_DOCS_DIR, fname), "r", encoding="utf-8") as f:
                lines = f.read().splitlines()
        except OSError:
            continue
        cur_section, cur_lines = None, []
        for ln in lines:
            if ln.startswith("## "):
                if cur_lines and cur_section:
                    sections.append({"source": fname, "section": cur_section,
                                     "text": "\n".join(cur_lines).strip()})
                cur_section, cur_lines = ln[3:].strip(), []
            elif cur_section:
                cur_lines.append(ln)
        if cur_lines and cur_section:
            sections.append({"source": fname, "section": cur_section,
                             "text": "\n".join(cur_lines).strip()})
    return sections


def _search_kb_local(query: str, top_k: int = 5) -> List[Dict]:
    """Local retrieval: the rules engine picks relevant categories, returning matching regulatory sections."""
    categories = scan_text_for_allergens(query)
    if not categories:
        return []
    sections = {s["section"]: s for s in _load_kb_sections() if s["section"] in PEAL_CATEGORIES}
    results: List[Dict] = []
    for cat in categories:
        sec = sections.get(cat)
        if sec:
            results.append({**sec, "score": 1.0})
        if len(results) >= top_k:
            break
    return results


def _retrieve_kb_aws(query: str, kb_id: str, top_k: int = 5) -> List[Dict]:
    """Retrieve from a Bedrock Knowledge Base.

    Modern "managed" knowledge bases require `managedSearchConfiguration`
    (the older `vectorSearchConfiguration` is rejected with
    ValidationException). Newer boto3/botocore accept the managed key; we
    probe it and fall back to the legacy key only if the SDK rejects it.
    """
    client = _get_kb_client()
    base = {
        "knowledgeBaseId": kb_id,
        "retrievalQuery": {"text": query},
    }
    try:
        resp = client.retrieve(
            **base,
            retrievalConfiguration={"managedSearchConfiguration": {"numberOfResults": top_k}},
        )
    except Exception as first_exc:  # noqa: BLE001
        # Older SDKs do not know "managedSearchConfiguration" and raise
        # ParamValidationError; fall back to the legacy shape so older
        # pinned boto3 versions keep working against vector KBs.
        if "managedSearchConfiguration" in str(first_exc) or "Unknown parameter" in str(first_exc):
            resp = client.retrieve(
                **base,
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
            )
        else:
            raise
    chunks: List[Dict] = []
    for r in resp.get("retrievalResults", []):
        s3_loc = (r.get("location", {}) or {}).get("s3Location", {}) or {}
        chunks.append({
            "text": (r.get("content", {}) or {}).get("text", ""),
            "source": s3_loc.get("uri", "bedrock-kb"),
            "section": "",
            "score": float(r.get("score", 0.0)),
        })
    return chunks


def retrieve_context(query: str, kb_id: str | None = None, top_k: int = 5) -> Dict:
    """Retrieve regulatory context -> {"engine": "aws"|"local"|"none", "chunks": [...]}.

    Uses the AWS Bedrock Knowledge Base when KNOWLEDGE_BASE_ID is configured
    and we are not in LOCAL_MODE; otherwise (or when the AWS call fails)
    degrades to local retrieval over docs/*.md so the pipeline never
    hard-fails - e.g. the default Beanstalk stack (create_knowledge_base=false)
    and the offline demo both run on the local corpus.
    """
    if not query or not query.strip():
        return {"engine": "none", "chunks": []}

    kb_id = kb_id or os.environ.get("KNOWLEDGE_BASE_ID", "")
    if (not LOCAL_MODE) and kb_id:
        try:
            chunks = _retrieve_kb_aws(query.strip(), kb_id, top_k)
            if chunks:
                return {"engine": "aws", "chunks": chunks}
            logger.warning("Bedrock KB '%s' returned no results", kb_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Bedrock KB retrieve failed, falling back to local: %s", exc)
    local = _search_kb_local(query.strip(), top_k)
    return {"engine": "local" if local else "none", "chunks": local}


# ==========================================================================
# 6. Pipeline merge (union of LLM + rules + RAG signals)
# ==========================================================================
def verify_pipeline(name: str, description: str, llm_categories: List[str],
                    rule_categories: List[str], retrieval: Dict) -> Dict:
    """Merge the three signals into confirmed / disagreements / citations (used by _run_pipeline)."""
    llm_set = {c for c in llm_categories if c in PEAL_CATEGORIES}
    rule_set = {c for c in rule_categories if c in PEAL_CATEGORIES}

    rag_categories: List[str] = []
    citations: List[Dict] = []
    for chunk in retrieval.get("chunks", [])[:5]:
        section = chunk.get("section", "")
        if section and section in PEAL_CATEGORIES:
            rag_categories.append(section)
        citations.append({
            "category": section or "general",
            "source": chunk.get("source", ""),
            "section": section,
            "text": chunk.get("text", "")[:200],
        })
    rag_set = {c for c in rag_categories if c in PEAL_CATEGORIES}

    confirmed = sorted(llm_set | rule_set | rag_set)  # union, biased toward not under-declaring
    disagreements = {
        "llm_only": sorted(llm_set - rule_set - rag_set),
        "rule_only": sorted(rule_set - llm_set - rag_set),
        "rag_only": sorted(rag_set - llm_set - rule_set),
    }
    engine = retrieval.get("engine", "none")
    engine_label = {"aws": "bedrock-rag + rules", "local": "local-rag + rules"}.get(engine, "rules-only")

    return {
        "confirmed": confirmed,
        "disagreements": disagreements,
        "citations": citations,
        "engine": engine_label,
        "rag_categories": sorted(rag_set),
        "reasoning": f"Union of {len(llm_set)} LLM, {len(rule_set)} rules, {len(rag_set)} RAG categories.",
    }

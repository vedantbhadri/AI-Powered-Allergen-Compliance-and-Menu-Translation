"""
allergen_rules.py

Deterministic compliance rules engine for NZ/AU Food Standards Code
Standard 1.2.3 (Information requirements - warning statements, advisory
statements and declarations) - the "PEAL" (Plain English Allergen
Labelling) mandatory declarable allergens.

This module is intentionally self-contained (no external knowledge base /
RAG lookup) per project scope - it is used as a second, deterministic
verification pass on top of whatever the Bedrock LLM extracts, so the
final "Contains ..." tags shown to a diner are never based on the LLM's
judgement alone.

The mapping is a starting point for a demo/prototype and should be
reviewed by a food-safety qualified person before being relied on in a
real commercial setting - it is not exhaustive of every product name,
regional ingredient, or brand-specific formulation.
"""
from __future__ import annotations
import re
from typing import Dict, List, Set

# Canonical mandatory declarable allergen categories under FSANZ Standard 1.2.3
# (incl. Molluscs, required for declaration — see docs/nz_peal_allergens.md
# "## Molluscs"; the deterministic extractor already emits it).
PEAL_CATEGORIES: List[str] = [
    "Gluten (Cereals)",
    "Crustacea",
    "Egg",
    "Fish",
    "Milk",
    "Peanuts",
    "Soybeans",
    "Tree Nuts",
    "Sesame",
    "Lupin",
    "Molluscs",
    "Added Sulphites",
]

# Short plain-English tag shown in the UI for each category
DISPLAY_TAG: Dict[str, str] = {
    "Gluten (Cereals)": "Contains Wheat/Gluten",
    "Crustacea": "Contains Crustacea",
    "Egg": "Contains Egg",
    "Fish": "Contains Fish",
    "Milk": "Contains Milk",
    "Peanuts": "Contains Peanuts",
    "Soybeans": "Contains Soy",
    "Tree Nuts": "Contains Tree Nuts",
    "Sesame": "Contains Sesame",
    "Lupin": "Contains Lupin",
    "Molluscs": "Contains Molluscs",
    "Added Sulphites": "Contains Sulphites",
}

# Diet-type tags derived from the *absence* of certain categories.
# These are heuristics for the UI filter chips (Vegan / Gluten-Free /
# Dairy-Free / Keto) shown in the mock-up - not a certification.
DIET_EXCLUSIONS: Dict[str, Set[str]] = {
    "Gluten-Free": {"Gluten (Cereals)"},
    "Dairy-Free": {"Milk"},
}

# Keyword -> allergen category. Keys are matched as whole-word,
# case-insensitive substrings against the ingredient/description text.
INGREDIENT_KEYWORDS: Dict[str, str] = {
    # Gluten / cereals
    "wheat": "Gluten (Cereals)", "flour": "Gluten (Cereals)",
    "bread": "Gluten (Cereals)", "pasta": "Gluten (Cereals)",
    "barley": "Gluten (Cereals)", "rye": "Gluten (Cereals)",
    "oats": "Gluten (Cereals)", "oat": "Gluten (Cereals)",
    "spelt": "Gluten (Cereals)", "malt": "Gluten (Cereals)",
    "breadcrumb": "Gluten (Cereals)", "batter": "Gluten (Cereals)",
    "soy sauce": "Gluten (Cereals)",  # most soy sauce also contains wheat
    "noodle": "Gluten (Cereals)", "couscous": "Gluten (Cereals)",
    # Crustacea
    "shrimp": "Crustacea", "prawn": "Crustacea", "crab": "Crustacea",
    "lobster": "Crustacea", "crayfish": "Crustacea", "langoustine": "Crustacea",
    # Egg
    "egg": "Egg", "mayonnaise": "Egg", "meringue": "Egg", "aioli": "Egg",
    # Fish
    "fish": "Fish", "salmon": "Fish", "tuna": "Fish", "anchov": "Fish",
    "cod": "Fish", "snapper": "Fish", "worcestershire": "Fish",
    "fish sauce": "Fish", "chowder": "Fish",
    # Molluscs
    "clam": "Molluscs", "mussel": "Molluscs", "mussels": "Molluscs",
    "oyster": "Molluscs", "oysters": "Molluscs",
    "scallop": "Molluscs", "scallops": "Molluscs",
    "squid": "Molluscs", "calamari": "Molluscs", "octopus": "Molluscs",
    "snail": "Molluscs", "snails": "Molluscs", "mollusc": "Molluscs",
    # Milk
    "milk": "Milk", "cream": "Milk", "butter": "Milk", "cheese": "Milk",
    "yoghurt": "Milk", "yogurt": "Milk", "parmesan": "Milk",
    "mozzarella": "Milk", "custard": "Milk", "ghee": "Milk",
    "gelato": "Milk", "mascarpone": "Milk",
    # common compounds whose stem is not a standalone word (word-boundary safe)
    "cheesecake": "Milk", "buttermilk": "Milk", "ice cream": "Milk",
    "cream cheese": "Milk", "fishcake": "Fish", "fish cakes": "Fish",
    "peanut butter": "Peanuts", "soybeans": "Soybeans",
    # Peanuts
    "peanut": "Peanuts", "groundnut": "Peanuts", "satay": "Peanuts",
    # Soy
    "soy": "Soybeans", "soya": "Soybeans", "tofu": "Soybeans",
    "soybeans": "Soybeans", "edamame": "Soybeans", "miso": "Soybeans",
    "tempeh": "Soybeans",
    # Tree nuts
    "almond": "Tree Nuts", "cashew": "Tree Nuts", "walnut": "Tree Nuts",
    "hazelnut": "Tree Nuts", "pistachio": "Tree Nuts", "pecan": "Tree Nuts",
    "macadamia": "Tree Nuts", "praline": "Tree Nuts", "nutella": "Tree Nuts",
    # Sesame
    "sesame": "Sesame", "tahini": "Sesame",
    # Lupin
    "lupin": "Lupin", "lupini": "Lupin",
    # Sulphites (common in dried fruit, wine reductions, processed potato)
    "sulphite": "Added Sulphites", "sulfite": "Added Sulphites",
    "dried apricot": "Added Sulphites", "wine reduction": "Added Sulphites",
    "dried fruit": "Added Sulphites",
}


def scan_text_for_allergens(text: str) -> List[str]:
    """Deterministic keyword scan of a dish name/description.

    Returns a sorted list of PEAL category names found. This is the
    rules-engine cross-check that runs *in addition to* the Bedrock LLM
    extraction - the union/agreement of both is what actually gets
    surfaced to the diner as a hard 'Contains X' tag.

    Matching is whole-word (word-boundary) and case-insensitive: substrings
    inside unrelated words do not trigger false positives (e.g. "egg" does
    not match "eggplant", "oat" does not match "goat", "butter" does not
    match "butterfly"). Common plural/derived forms ("prawns", "almonds",
    "mussels", "scallops") are matched by allowing trailing s/es after the
    keyword.
    """
    if not text:
        return []
    lowered = f" {text.lower()} "
    found: Set[str] = set()
    for keyword, category in INGREDIENT_KEYWORDS.items():
        # \b word boundaries for the keyword, then an optional common suffix
        # (s/es) so plurals are caught: "prawn" -> "prawns", "mussel" -> "mussels".
        if re.search(rf"\b{re.escape(keyword)}(?:s|es)?\b", lowered):
            found.add(category)
    return sorted(found)


def reconcile_allergens(llm_categories: List[str], rule_categories: List[str]) -> Dict[str, List[str]]:
    """Combine LLM-extracted and rule-engine-extracted allergens.

    - "confirmed": present in either source (union) - shown to the diner,
      because under-declaring an allergen is the higher-risk failure mode.
    - "llm_only" / "rule_only": returned separately so a human reviewer
      (human-in-the-loop) can see where the two disagreed.
    """
    llm_set = {c for c in llm_categories if c in PEAL_CATEGORIES}
    rule_set = {c for c in rule_categories if c in PEAL_CATEGORIES}
    return {
        "confirmed": sorted(llm_set | rule_set),
        "llm_only": sorted(llm_set - rule_set),
        "rule_only": sorted(rule_set - llm_set),
    }


def derive_diet_tags(confirmed_categories: List[str], text: str = "") -> List[str]:
    """Derive simple diet filter chips from the confirmed allergen set.

    Gluten-Free / Dairy-Free are derived from the absence of the matching
    allergen category. "Vegan" is NOT derivable from the allergen list
    alone (meat/poultry/fish that are not in the PEAL allergen list would
    still slip through) - it is only set here when the dish text
    explicitly says so (matching the sample data's "No animal products
    used" style wording), and should always be confirmed by a human
    before being relied on. "Keto" is intentionally not auto-derived -
    carbohydrate content isn't something the allergen pipeline assesses.
    """
    present = set(confirmed_categories)
    tags = []
    for diet, excluded in DIET_EXCLUSIONS.items():
        if not (present & excluded):
            tags.append(diet)
    lowered = text.lower()
    if "vegan" in lowered or "no animal product" in lowered:
        tags.append("Vegan (self-declared - verify manually)")
    return tags


def to_display_tags(confirmed_categories: List[str]) -> List[str]:
    return [DISPLAY_TAG.get(c, f"Contains {c}") for c in confirmed_categories]

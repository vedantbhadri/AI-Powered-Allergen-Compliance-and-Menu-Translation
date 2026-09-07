"""Turn OCR text lines into structured menu dishes.

Textract returns visual text lines, not menu semantics.  In particular, a
single-column menu commonly consists of headings followed by several
``dish, $price`` lines.  Pairing adjacent OCR lines therefore combines two
different dishes and turns headings or contact details into menu cards.
"""
from __future__ import annotations

import re
from typing import Dict, List


PRICE_AT_END = re.compile(
    r"(?:\s*[,|·-]?\s*)"
    r"(?P<price>(?:NZ\s*)?\$\s*\d+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{2})\s*(?:NZD|USD|AUD))"
    r"\s*$",
    re.IGNORECASE,
)
CONTACT_LINE = re.compile(
    r"(?:https?://|www\.|\b(?:order|phone|call|email|address)\b|"
    r"\b\d{2,4}[- .]\d{3}[- .]\d{3,4}\b)",
    re.IGNORECASE,
)
ITEM_CODE = re.compile(r"^[A-Z]{1,3}\s*\d+[A-Z]?\s*[.)-]\s*", re.IGNORECASE)


def _clean(line: str) -> str:
    return " ".join((line or "").strip().split())


def _clean_item_name(name: str) -> str:
    """Remove ordering codes such as 'A3.' while preserving the dish name."""
    return ITEM_CODE.sub("", name).strip(" .,|·-")


def _is_heading(line: str) -> bool:
    """Identify likely section/title text without treating dish names as headings."""
    letters = [char for char in line if char.isalpha()]
    return bool(letters) and len(line.split()) <= 5 and all(char.isupper() for char in letters)


def _is_section_note(line: str) -> bool:
    """Match short notes attached to headings, such as '(Mild, Medium, Hot)'."""
    return line.startswith("(") and line.endswith(")") and len(line.split()) <= 6


def parse_ocr_lines(lines: List[str]) -> List[Dict[str, str]]:
    """Parse OCR lines into ``name``/``description`` menu items.

    Supports both common Textract sequences:

    - ``dish name + price`` on one OCR line;
    - a left-aligned dish-name line followed by a separate right-aligned
      price line and one or more wrapped description lines.

    Non-menu branding, section headings, notes, and contact lines are ignored.
    If no prices are detected, the adjacent-line fallback is retained for
    simple name/description menus.
    """
    cleaned = [_clean(line) for line in lines]
    cleaned = [line for line in cleaned if line]

    # (name line index, price line index, normalized name, normalized price)
    starts = []
    for index, line in enumerate(cleaned):
        price_match = PRICE_AT_END.search(line)
        if not price_match:
            continue

        price = price_match.group("price").replace(" ", "")
        name = line[: price_match.start()].rstrip(" .,|·-")
        name_index = index

        # Right-aligned prices are emitted as their own Textract LINE directly
        # after the corresponding left-aligned item name.
        if not name and index > 0:
            candidate = cleaned[index - 1]
            if (
                not PRICE_AT_END.fullmatch(candidate)
                and not CONTACT_LINE.search(candidate)
                and not _is_heading(candidate)
                and not _is_section_note(candidate)
            ):
                name = candidate.rstrip(" .,|·-")
                name_index = index - 1

        name = _clean_item_name(name)
        if not name or CONTACT_LINE.search(name):
            continue

        starts.append((name_index, index, name, price))

    if starts:
        priced_items = []
        for item_index, (name_index, price_index, name, price) in enumerate(starts):
            next_name_index = (
                starts[item_index + 1][0]
                if item_index + 1 < len(starts)
                else len(cleaned)
            )
            description_lines = []
            for line in cleaned[price_index + 1:next_name_index]:
                if (
                    PRICE_AT_END.fullmatch(line)
                    or CONTACT_LINE.search(line)
                    or _is_heading(line)
                    or _is_section_note(line)
                ):
                    continue
                description_lines.append(line)

            priced_items.append({
                "name": name,
                "description": " ".join(description_lines),
            })
        return priced_items

    # Price-free menus may use alternating dish-name and description lines.
    usable = [
        line for line in cleaned
        if (
            not CONTACT_LINE.search(line)
            and not _is_heading(line)
            and not _is_section_note(line)
        )
    ]
    return [
        {
            "name": usable[index],
            "description": usable[index + 1] if index + 1 < len(usable) else "",
        }
        for index in range(0, len(usable), 2)
    ]

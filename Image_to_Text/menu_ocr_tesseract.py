import io
import re
import sys
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image, ImageOps, ImageFilter
import pytesseract

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

try:
    from pdf2image import convert_from_bytes
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False


# ========================================================================
# Config
# ========================================================================

class Settings:

    #psm-4 assumes a single column of text of variable size. we have split multiple column into seperate images before OCR so whatever is handed to tesseract is single column.
    tesseract_config = "--oem 3 --psm 4"

    # PDF rasterization DPI — higher = better OCR accuracy, slower + more memory.
    pdf_dpi = 300

    # Below this confidence, lines are flagged for review.
    low_confidence_threshold = 60.0

    #lines below this confidence is not flagged dropped entirely because below this confidence level it's almost always noise ((background photo texture, icons, decorative elements))
    noise_confidence_floor = 35.0

    image_extensions = (".png", ".jpg", ".jpeg", ".tiff", ".bmp")

    #column gap detector: searches for whitespace gutter to avoid false positives. can be widened if menu uses three columns.
    column_search_band = (0.40, 0.62)

    # if the menu image uploaded is below this many pixels, it gets upscaled. upscaling via LANCZOS interpolation
    min_dimension_px = 1500

    # Minimum gap width to treat as a real column divider,
    min_gap_fraction = 0.02


settings = Settings()


# ------------------------------------------------------------------------
# Models
# ------------------------------------------------------------------------

@dataclass
class RawLine:
    text: str
    confidence: float
    page: int = 1


@dataclass
class MenuItem:
    name: str
    description: Optional[str] = None
    price: Optional[str] = None
    currency: Optional[str] = None
    section: Optional[str] = None
    raw_text: str = ""
    confidence: float = 0.0
    page: int = 1


@dataclass
class ExtractionResult:
    document_id: str
    page_count: int = 1
    raw_lines: List[RawLine] = field(default_factory=list)
    menu_items: List[MenuItem] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "page_count": self.page_count,
            "warnings": self.warnings,
            "raw_lines": [
                {"text": l.text, "confidence": l.confidence, "page": l.page}
                for l in self.raw_lines
            ],
            "menu_items": [
                {
                    "name": m.name,
                    "description": m.description,
                    "price": m.price,
                    "currency": m.currency,
                    "section": m.section,
                    "raw_text": m.raw_text,
                    "confidence": m.confidence,
                    "page": m.page,
                }
                for m in self.menu_items
            ],
        }


# ------------------------------------------------------------------------
# OCR engine (Tesseract)
# ------------------------------------------------------------------------

def find_column_split(image: Image.Image) -> Optional[int]:
    """
    Detects a vertical whitespace gutter
    and returns its center x-coordinate, or None if no clear gap is found
    """
    arr = np.array(image.convert("L"))
    h, w = arr.shape

    region = arr[int(h * 0.40): int(h * 0.90), :]
    darkness = (region < 100).sum(axis=0)

    lo = int(w * settings.column_search_band[0])
    hi = int(w * settings.column_search_band[1])
    band = darkness[lo:hi]
    is_gap = band <= 2

    best_start, best_len, cur_start, cur_len = 0, 0, 0, 0
    for i, g in enumerate(is_gap):
        if g:
            if cur_len == 0:
                cur_start = i
            cur_len += 1
            if cur_len > best_len:
                best_len, best_start = cur_len, cur_start
        else:
            cur_len = 0

    if best_len < w * settings.min_gap_fraction:
        return None  # no convincing gutter -> treat as single column

    return lo + best_start + best_len // 2


class TesseractOCR:
    """Wraps pytesseract calls: column splitting, preprocessing, and line-level extraction."""

    def _upscale_if_small(self, image: Image.Image) -> Image.Image:
        shorter_side = min(image.size)
        if shorter_side >= settings.min_dimension_px:
            return image
        scale = settings.min_dimension_px / shorter_side
        new_size = (int(image.width * scale), int(image.height * scale))
        logger.info("Upscaling image from %s to %s",
                    image.size, new_size)
        return image.resize(new_size, Image.LANCZOS)

    def extract_lines_from_page(self, image: Image.Image, page: int = 1) -> List[RawLine]:
        """
        Extracts lines from one page image. If the page has two columns, splits the image and extracts lines from each column separately."""
        image = self._upscale_if_small(image)
        split_x = find_column_split(image)
        if split_x is None:
            return self._extract_single_column(image, page)

        w, h = image.size
        left = image.crop((0, 0, split_x, h))
        right = image.crop((split_x, 0, w, h))
        return self._extract_single_column(left, page) + self._extract_single_column(right, page)

    def _extract_single_column(self, image: Image.Image, page: int) -> List[RawLine]:
        image = self._finish_preprocessing(image)
        data = pytesseract.image_to_data(
            image, config=settings.tesseract_config, output_type=pytesseract.Output.DICT
        )
        lines = self._group_words_into_lines(data, page)
        # Drop near-zero-confidence noise (photo texture, icons, artifacts) before it ever reaches the parser.
        return [l for l in lines if l.confidence >= settings.noise_confidence_floor]

    def _finish_preprocessing(self, image: Image.Image) -> Image.Image:
        """Grayscale + autocontrast + sharpen. This is a simple, fast, and effective preprocessing pipeline for OCR."""
        gray = ImageOps.grayscale(image)
        contrast = ImageOps.autocontrast(gray)
        return contrast.filter(ImageFilter.SHARPEN)

    def _group_words_into_lines(self, data: dict, page: int) -> List[RawLine]:
        """pytesseract's image_to_data returns per-word rows."""
        n = len(data["text"])
        lines_map = {}

        for i in range(n):
            word = data["text"][i].strip()
            if not word:
                continue
            conf = float(data["conf"][i]) if data["conf"][i] not in ("-1", -1) else 0.0
            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines_map.setdefault(key, {"words": [], "confs": []})
            lines_map[key]["words"].append(word)
            lines_map[key]["confs"].append(conf)

        raw_lines = []
        for key in sorted(lines_map.keys()):
            words = lines_map[key]["words"]
            confs = lines_map[key]["confs"]
            text = " ".join(words)
            avg_conf = sum(confs) / len(confs) if confs else 0.0
            raw_lines.append(RawLine(text=text, confidence=avg_conf, page=page))

        return raw_lines


# ------------------------------------------------------------------------
# Menu parser
# ------------------------------------------------------------------------

SECTION_HEADER_RE = re.compile(r"^[A-Z][A-Z\s&/'-]{2,40}$")
PRICE_TOKEN_RE = re.compile(r"[$€£¥]?\s?\d{1,4}(?:[.,]\d{2})?\s?[$€£¥]?")
# Leading bullet/symbol junk OCR sometimes reads a "•" as — strip before checking the header pattern (e.g. "- APPETIZER", "« SOUPS & SALAD", "¢ APPETIZER").
LEADING_SYMBOLS_RE = re.compile(r"^[^A-Za-z0-9]+")
# Menu title words that appear once at the top of the page — not a real section, so items shouldn't get grouped under them.
TITLE_WORDS = {"MENU", "OUR MENU", "MENU CARD", "TODAY'S MENU"}


def _looks_like_price(text: str) -> bool:
    return bool(re.search(r"\d{1,4}([.,]\d{2})?\s*[$€£¥]?\s*$", text.strip())) or \
           bool(re.match(r"^[$€£¥]\s*\d", text.strip()))


def _extract_price(text: str):
    matches = list(PRICE_TOKEN_RE.finditer(text))
    if not matches:
        return None, None, text.strip()
    last = matches[-1]
    price_token = last.group().strip()
    currency = next((s for s in ("$", "€", "£", "¥") if s in price_token), None)
    amount = re.sub(r"[^\d.,]", "", price_token)
    remainder = (text[: last.start()] + text[last.end():]).strip(" -–—.:\t")
    return amount, currency, remainder


def _clean_header_candidate(text: str) -> str:
    """Strips a leading bullet/symbol (OCR noise from '•') and a trailing section-wide price (e.g. 'APPETIZER $3' -> 'APPETIZER'), leaving just the label to test """
    text = LEADING_SYMBOLS_RE.sub("", text).strip()
    if _looks_like_price(text):
        _, _, text = _extract_price(text)
    return text.strip()


def _is_section_header(raw_text: str) -> bool:
    """
    A line counts as a section header if after stripping any leading
    bullet symbol and any trailing section-wide price, it is all-caps, 3-40 characters long, and matches the SECTION_HEADER_RE pattern.
    """
    cleaned = _clean_header_candidate(raw_text)
    if not cleaned or len(cleaned) > 40:
        return False
    return bool(SECTION_HEADER_RE.match(cleaned))


def parse_menu(lines: List[RawLine]) -> List[MenuItem]:
    """
    Groups OCR lines into menu items:
    - Short, all-caps, price-free line -> section header.
    - Line ending in a price -> new item (name + price).
    - Price-free line right after an item -> that item's description.
    - Otherwise -> a standalone unpriced line (e.g. a note to the reader).
    """
    items: List[MenuItem] = []
    current_section: Optional[str] = None
    pending_item: Optional[MenuItem] = None

    for line in lines:
        text = line.text.strip()
        if not text:
            continue

        if _is_section_header(text):
            header_label = _clean_header_candidate(text)
            if header_label.upper() not in TITLE_WORDS:
                current_section = header_label.title()
            pending_item = None
            continue

        if _looks_like_price(text):
            amount, currency, name_part = _extract_price(text)
            item = MenuItem(
                name=name_part or text,
                price=amount,
                currency=currency,
                section=current_section,
                raw_text=text,
                confidence=line.confidence,
                page=line.page,
            )
            items.append(item)
            pending_item = item
        else:
            if pending_item is not None and pending_item.description is None:
                pending_item.description = text
            else:
                items.append(
                    MenuItem(
                        name=text,
                        section=current_section,
                        raw_text=text,
                        confidence=line.confidence,
                        page=line.page,
                    )
                )
                pending_item = None

    return items


# ------------------------------------------------------------------------
# Pipeline
# ------------------------------------------------------------------------

class MenuIngestionPipeline:
    def __init__(self):
        self.ocr = TesseractOCR()

    def process_file(self, path: str) -> ExtractionResult:
        file_path = Path(path)
        file_bytes = file_path.read_bytes()
        return self.process_bytes(file_bytes, filename=file_path.name)

    def process_bytes(self, file_bytes: bytes, filename: str) -> ExtractionResult:
        import uuid
        document_id = f"{uuid.uuid4().hex}_{filename}"
        ext = Path(filename).suffix.lower()

        result = ExtractionResult(document_id=document_id)

        try:
            images = self._load_pages(file_bytes, ext)
            result.page_count = len(images)

            all_lines: List[RawLine] = []
            for page_num, image in enumerate(images, start=1):
                all_lines.extend(self.ocr.extract_lines_from_page(image, page=page_num))

            result.raw_lines = all_lines
            result.menu_items = parse_menu(all_lines)

            if not all_lines:
                result.warnings.append(
                    "No text detected — check image quality, resolution, or orientation."
                )
            low_conf = [l for l in all_lines if l.confidence < settings.low_confidence_threshold]
            if low_conf:
                result.warnings.append(
                    f"{len(low_conf)} line(s) had OCR confidence below "
                    f"{settings.low_confidence_threshold}% — flag for manual review."
                )

        except Exception as e:
            logger.exception("OCR processing failed for %s", filename)
            result.warnings.append(f"OCR error: {e}")

        return result

    def _load_pages(self, file_bytes: bytes, ext: str) -> List[Image.Image]:
        if ext == ".pdf":
            if not PDF_SUPPORT:
                raise RuntimeError(
                    "pdf2image is not installed, or poppler is missing from PATH. "
                    "Run: pip install pdf2image  (and install poppler — see file header)."
                )
            return convert_from_bytes(file_bytes, dpi=settings.pdf_dpi)

        if ext in settings.image_extensions:
            return [Image.open(io.BytesIO(file_bytes)).convert("RGB")]

        # Unknown extension: try opening as an image and let PIL raise if it can't.
        logger.warning("Unrecognized extension '%s' — attempting to open as an image.", ext)
        return [Image.open(io.BytesIO(file_bytes)).convert("RGB")]


# ------------------------------------------------------------------------
# CLI entry point
# ------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python menu_ocr_tesseract.py <path-to-menu-file>")
        sys.exit(1)

    pipeline = MenuIngestionPipeline()
    extraction = pipeline.process_file(sys.argv[1])
    print(json.dumps(extraction.to_dict(), indent=2))

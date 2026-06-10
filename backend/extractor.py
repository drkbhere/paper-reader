"""PDF -> structured text blocks for the Paper Reader.

Pipeline: PyMuPDF block extraction -> running header/footer removal ->
column-aware reading order -> heading-line splitting -> de-hyphenation ->
heading detection -> cross-page paragraph merging.
"""

import re
from collections import Counter, defaultdict

import fitz

MIN_DOC_CHARS = 200        # below this we assume a scanned / image-only PDF
HEADER_BAND = 0.08         # top fraction of the page checked for running headers
FOOTER_BAND = 0.92         # bottom fraction checked for running footers
FULL_WIDTH_RATIO = 0.6     # blocks at least this wide (vs page) span both columns
HEADING_SIZE_RATIO = 1.12  # font size multiplier over body text to call a heading
HEADING_MAX_CHARS = 150
BOLD_FLAG = 16             # PyMuPDF span flag bit for bold

_PAGE_NUM_RE = re.compile(r"^\s*(page\s+)?\d{1,4}(\s*(of|/)\s*\d{1,4})?\s*$", re.I)
_SENTENCE_END_RE = re.compile(r'[.!?:;…]["\')\]’”]*$')

# Standard scholarly section names, accepted as headings even at body size.
_SECTION_NAMES_RE = re.compile(
    r"^(?:\d+(?:\.\d+)*\.?\s+)?("
    r"abstract|key\s?words?|introduction|background|related work|literature review|"
    r"theoretical (?:framework|background)|conceptual (?:framework|background)|"
    r"hypothes[ei]s development|research questions?|"
    r"methods?|methodology|materials and methods|study \d+[a-z]?|experiments? \d*[a-z]?|"
    r"participants|measures|stimuli|procedure|design|pretest|pilot study|"
    r"results?|findings|analysis|analyses|manipulation checks?|"
    r"discussion|general discussion|conclusions?|"
    r"(?:theoretical |managerial |practical )?(?:implications|contributions)|"
    r"limitations(?: and future (?:research|directions))?|future research|"
    r"references|bibliography|acknowledge?ments?|appendix(?:\s+[a-z\d])?|"
    r"supplementary materials?|funding|declarations?|notes?"
    r")\s*[.:]?$",
    re.I,
)

# "3.2 Measures" / "1. Introduction" style lines (need bold or larger font too,
# so plain numbered list items in prose don't split paragraphs).
_NUMBERED_HEADING_RE = re.compile(r"^\d{1,2}(\.\d{1,2})*\.?\s+[A-Z\"“(\[].{0,78}$")


class ExtractionError(ValueError):
    """The PDF could not be parsed at all."""


class EmptyTextError(ExtractionError):
    """The PDF opened fine but contains no extractable text (likely scanned)."""


def extract_pdf(pdf_bytes: bytes) -> dict:
    """Return {"title": str, "blocks": [{"type": "heading"|"paragraph", "text": str}]}."""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise ExtractionError(f"Could not open PDF: {exc}") from None

    try:
        raw = _gather_blocks(doc)
        page_count = doc.page_count
    finally:
        doc.close()

    if sum(len(_block_text(b)) for b in raw) < MIN_DOC_CHARS:
        raise EmptyTextError("No extractable text found in this PDF.")

    raw = _drop_headers_footers(raw, page_count)
    ordered = _reading_order(raw)
    body_size = _body_font_size(ordered)
    ordered = _split_heading_lines(ordered, body_size)
    blocks = _build_blocks(ordered, body_size)
    title = _pick_title(ordered, blocks)
    if blocks and blocks[0]["type"] == "heading" and blocks[0]["text"] == title:
        blocks.pop(0)  # the frontend renders the title itself
    return {"title": title, "blocks": blocks}


def _gather_blocks(doc):
    """Flatten every text block on every page; lines keep their own font
    size/boldness so headings buried inside body blocks can be split out."""
    blocks = []
    for page_no, page in enumerate(doc):
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:  # 0 = text, 1 = image
                continue
            lines, block_sizes, block_bold, block_total = [], [], 0, 0
            for line in blk["lines"]:
                text = "".join(span["text"] for span in line["spans"])
                text = text.replace("­", "").strip()  # soft hyphens
                if not text:
                    continue
                line_sizes, line_bold, line_total = [], 0, 0
                for span in line["spans"]:
                    n = len(span["text"].strip())
                    if not n:
                        continue
                    line_sizes.append((round(span["size"], 1), n))
                    line_total += n
                    if span["flags"] & BOLD_FLAG:
                        line_bold += n
                lines.append({
                    "text": re.sub(r"\s+", " ", text),
                    "size": _weighted_mode(line_sizes),
                    "bold": line_total > 0 and line_bold / line_total > 0.6,
                })
                block_sizes.extend(line_sizes)
                block_bold += line_bold
                block_total += line_total
            if not lines:
                continue
            blocks.append({
                "page": page_no,
                "bbox": blk["bbox"],
                "lines": lines,
                "size": _weighted_mode(block_sizes),
                "bold": block_total > 0 and block_bold / block_total > 0.6,
                "page_height": page.rect.height,
                "page_width": page.rect.width,
            })
    return blocks


def _block_text(block):
    return " ".join(line["text"] for line in block["lines"])


def _weighted_mode(sizes):
    counts = Counter()
    for size, weight in sizes:
        counts[size] += weight
    return counts.most_common(1)[0][0] if counts else 0.0


def _drop_headers_footers(blocks, page_count):
    """Remove running headers/footers (same text in the same band on several pages)
    and standalone page numbers."""
    repeat_threshold = 2 if page_count <= 4 else 3
    band_pages = defaultdict(set)
    for b in blocks:
        key = _band_key(b)
        if key:
            band_pages[key].add(b["page"])

    kept = []
    for b in blocks:
        key = _band_key(b)
        if key:
            if _PAGE_NUM_RE.match(_block_text(b)):
                continue
            if len(band_pages[key]) >= repeat_threshold:
                continue
        kept.append(b)
    return kept


def _band_key(block):
    """Normalized identity for header/footer candidates, or None for body blocks."""
    y0, y1 = block["bbox"][1], block["bbox"][3]
    h = block["page_height"]
    if y1 <= h * HEADER_BAND or y0 >= h * FOOTER_BAND:
        zone = "top" if y1 <= h * HEADER_BAND else "bottom"
        text = re.sub(r"\d+", "#", _block_text(block)).lower().strip()
        return (zone, text)
    return None


def _reading_order(blocks):
    """Order blocks page by page, reading the left column before the right one.
    Full-width blocks (titles, abstracts spanning both columns) act as separators."""
    by_page = defaultdict(list)
    for b in blocks:
        by_page[b["page"]].append(b)

    ordered = []
    for page in sorted(by_page):
        page_blocks = sorted(by_page[page], key=lambda b: (b["bbox"][1], b["bbox"][0]))
        pw = page_blocks[0]["page_width"]
        region = []

        def flush():
            if not region:
                return
            mid = pw / 2
            left = [b for b in region if (b["bbox"][0] + b["bbox"][2]) / 2 < mid]
            right = [b for b in region if (b["bbox"][0] + b["bbox"][2]) / 2 >= mid]
            if left and right:
                ordered.extend(sorted(left, key=lambda b: b["bbox"][1]))
                ordered.extend(sorted(right, key=lambda b: b["bbox"][1]))
            else:
                ordered.extend(sorted(region, key=lambda b: b["bbox"][1]))
            region.clear()

        for b in page_blocks:
            width = b["bbox"][2] - b["bbox"][0]
            if width >= FULL_WIDTH_RATIO * pw:
                flush()
                ordered.append(b)
            else:
                region.append(b)
        flush()
    return ordered


def _body_font_size(blocks):
    sizes = [(b["size"], len(_block_text(b))) for b in blocks]
    return _weighted_mode(sizes)


def _is_heading_line(line, body_size, is_first_in_block, block_bold):
    """Is this single line, inside a larger block, a section heading?"""
    text = line["text"]
    if len(text) > HEADING_MAX_CHARS:
        return False
    if body_size and line["size"] >= max(body_size * HEADING_SIZE_RATIO, body_size + 0.5):
        return True
    if _SECTION_NAMES_RE.match(text) and (is_first_in_block or line["bold"] or line["size"] > body_size):
        return True
    if _NUMBERED_HEADING_RE.match(text) and (line["bold"] or line["size"] >= body_size + 0.5):
        return True
    # a short bold line inside a mostly non-bold block reads as a run-in heading
    return line["bold"] and not block_bold and len(text) <= 60


def _split_heading_lines(blocks, body_size):
    """Split blocks whose first (or interior) lines are really section headings —
    e.g. 'Abstract' sharing a text block with the paragraph that follows it."""
    out = []
    for b in blocks:
        if len(b["lines"]) < 2:
            out.append(b)
            continue
        groups, body_run = [], []
        for i, line in enumerate(b["lines"]):
            if _is_heading_line(line, body_size, i == 0, b["bold"]):
                if body_run:
                    groups.append(("body", body_run))
                    body_run = []
                groups.append(("heading", [line]))
            else:
                body_run.append(line)
        if body_run:
            groups.append(("body", body_run))

        if len(groups) == 1 and groups[0][0] == "body":
            out.append(b)
            continue
        for kind, lines in groups:
            piece = dict(b)
            piece["lines"] = lines
            piece["size"] = _weighted_mode([(l["size"], len(l["text"])) for l in lines])
            piece["heading_hint"] = kind == "heading"
            out.append(piece)
    return out


def _join_lines(lines):
    """Join a block's lines into one string, repairing hyphenated line breaks."""
    out = lines[0]["text"]
    for line in lines[1:]:
        text = line["text"]
        if out.endswith("-") and text and text[0].islower():
            out = out[:-1] + text
        else:
            out = out + " " + text
    return out.strip()


def _is_heading(block, text, body_size):
    if block.get("heading_hint"):
        return True
    if len(text) > HEADING_MAX_CHARS:
        return False
    if body_size and block["size"] >= max(body_size * HEADING_SIZE_RATIO, body_size + 0.5):
        return True
    if len(block["lines"]) == 1 and _SECTION_NAMES_RE.match(text):
        return True
    if len(block["lines"]) == 1 and _NUMBERED_HEADING_RE.match(text) and block["bold"]:
        return True
    return block["bold"] and block["size"] >= body_size and len(text) <= 80 and len(block["lines"]) == 1


def _continues(prev_text, cur_text):
    """True when `cur_text` is the continuation of a paragraph split across
    blocks/pages/columns."""
    if _SENTENCE_END_RE.search(prev_text):
        return False
    if not cur_text:
        return False
    return cur_text[0].islower() or prev_text.endswith(("-", ","))


def _build_blocks(ordered, body_size):
    blocks = []
    for b in ordered:
        text = _join_lines(b["lines"])
        if not text:
            continue
        if _is_heading(b, text, body_size):
            blocks.append({"type": "heading", "text": text})
            continue
        if blocks and blocks[-1]["type"] == "paragraph" and _continues(blocks[-1]["text"], text):
            prev = blocks[-1]["text"]
            if prev.endswith("-") and text[0].islower():
                blocks[-1]["text"] = prev[:-1] + text
            else:
                blocks[-1]["text"] = prev + " " + text
        else:
            blocks.append({"type": "paragraph", "text": text})
    return blocks


def _pick_title(ordered, blocks):
    first_page = [b for b in ordered if b["page"] == 0]
    if first_page:
        candidate = max(first_page, key=lambda b: b["size"])
        text = _join_lines(candidate["lines"])
        if 4 <= len(text) <= 300:
            return text
    for blk in blocks:
        if blk["type"] == "heading":
            return blk["text"]
    return "Untitled document"

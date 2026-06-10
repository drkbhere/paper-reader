"""Tests for the PDF extraction pipeline, using PDFs generated on the fly."""

import fitz
import pytest

from backend.extractor import EmptyTextError, extract_pdf

BODY = 11
A4 = fitz.paper_rect("a4")  # 595 x 842 pt

FILLER = (
    "This is ordinary body text used to establish the dominant font size of the "
    "document so that the heading detector has something to compare against. "
    "It needs to be reasonably long to pass the scanned-document check."
)


def wrap(text, width=80):
    """insert_text doesn't wrap lines; text past the page edge is dropped by
    get_text, so wrap manually to keep everything on the page."""
    import textwrap
    return "\n".join(textwrap.wrap(text, width))


def pdf_bytes(build):
    doc = fitz.open()
    build(doc)
    data = doc.tobytes()
    doc.close()
    return data


def all_text(result):
    return " ".join(b["text"] for b in result["blocks"])


def test_simple_paragraph_roundtrip():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    assert result["blocks"]
    assert "ordinary body text" in all_text(result)


def test_dehyphenation_across_lines():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 300), "We ran a controlled experi-\nment that continues here.", fontsize=BODY)

    text = all_text(extract_pdf(pdf_bytes(build)))
    assert "experiment that continues" in text
    assert "experi-" not in text


def test_running_headers_and_page_numbers_dropped():
    def build(doc):
        for i in range(4):
            page = doc.new_page()
            page.insert_text((72, 30), "Journal of Synthetic Tests", fontsize=9)
            page.insert_text((72, 400), wrap(f"Body paragraph on page {i + 1}. " + FILLER), fontsize=BODY)
            page.insert_text((290, 830), str(i + 1), fontsize=9)

    text = all_text(extract_pdf(pdf_bytes(build)))
    assert "Journal of Synthetic Tests" not in text
    assert "Body paragraph on page 3" in text


def test_heading_detected_by_font_size():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 90), "A Paper Title Larger Than Its Headings", fontsize=20)
        page.insert_text((72, 150), "1. Introduction", fontsize=16)
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    headings = [b["text"] for b in result["blocks"] if b["type"] == "heading"]
    assert "1. Introduction" in headings


def test_two_column_reading_order():
    def build(doc):
        page = doc.new_page()  # 595pt wide; columns ~ [60, 280] and [315, 535]
        page.insert_text((60, 200), "Left column first sentence about alpha topics.", fontsize=BODY)
        page.insert_text((60, 400), "Left column second part discussing beta ideas.", fontsize=BODY)
        page.insert_text((315, 200), "Right column begins gamma discussion here.", fontsize=BODY)
        page.insert_text((60, 700), wrap(FILLER), fontsize=BODY)

    text = all_text(extract_pdf(pdf_bytes(build)))
    assert text.index("alpha") < text.index("beta") < text.index("gamma")


def test_paragraph_merged_across_pages():
    def build(doc):
        p1 = doc.new_page()
        p1.insert_text((72, 200), wrap(FILLER), fontsize=BODY)
        p1.insert_text((72, 800), "The sentence is split across the page", fontsize=BODY)
        p2 = doc.new_page()
        p2.insert_text((72, 60), "boundary and continues on the next page.", fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    merged = [b for b in result["blocks"]
              if "split across the page boundary and continues" in b["text"]]
    assert merged, [b["text"] for b in result["blocks"]]


def test_scanned_pdf_raises_empty_text_error():
    def build(doc):
        doc.new_page()

    with pytest.raises(EmptyTextError):
        extract_pdf(pdf_bytes(build))


def test_heading_on_first_line_of_body_block():
    """'Abstract' often lands on the first line of the same text block as the
    paragraph that follows it — it must still come out as a heading."""
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 100), "A Buy Now Pay Later Paper Title", fontsize=20)
        page.insert_text((72, 200), "Abstract", fontsize=BODY)
        page.insert_text((72, 213), wrap('"Buy now, pay later" installment payments allow customers to pay for purchases in four parts.'), fontsize=BODY)
        page.insert_text((72, 300), wrap(FILLER), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    headings = [b["text"] for b in result["blocks"] if b["type"] == "heading"]
    assert "Abstract" in headings
    first_para = next(b["text"] for b in result["blocks"] if b["type"] == "paragraph")
    assert first_para.startswith('"Buy now')


def test_bold_numbered_heading_inside_block():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 400), "3.2 Measures", fontsize=BODY, fontname="hebo")
        page.insert_text((72, 413), wrap("Purchase intention was measured with five items adapted from prior work."), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    headings = [b["text"] for b in result["blocks"] if b["type"] == "heading"]
    assert "3.2 Measures" in headings


def test_plain_section_name_is_heading():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 500), "References", fontsize=BODY)
        page.insert_text((72, 560), wrap("Smith, J. (2020). A study of things. Journal of Stuff, 1(2), 3-4."), fontsize=BODY)

    headings = [b["text"] for b in extract_pdf(pdf_bytes(build))["blocks"] if b["type"] == "heading"]
    assert "References" in headings


def test_title_is_largest_font_on_first_page():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 100), "Price Disparity in Online Reviews", fontsize=20)
        page.insert_text((72, 200), wrap(FILLER), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    assert result["title"] == "Price Disparity in Online Reviews"

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


from backend.extractor import (
    _classify_nonprose, _center_in_any, _is_footnote, _is_equation,
)


def _block(text="", size=11.0, top=300.0, page_height=842.0, font="", in_table=False):
    return {
        "text": text, "size": size, "font": font, "in_table": in_table,
        "bbox": (72.0, top, 500.0, top + 20.0), "page_height": page_height,
    }


def test_center_in_any_inside_and_outside():
    rects = [fitz.Rect(72, 100, 330, 180)]
    assert _center_in_any((80, 110, 200, 130), rects) is True
    assert _center_in_any((80, 400, 200, 420), rects) is False


def test_caption_blocks_detected():
    body = 11.0
    for text in ("Figure 3. The proposed model.", "Table 2: Means and SDs.",
                 "Fig. 1 — Overview of the design.", "Exhibit 4. Summary."):
        assert _classify_nonprose(_block(text=text), text, body) == "caption", text


def test_grammatical_table_mention_is_not_a_caption():
    # "Table 2 shows..." is prose, not a caption — no separator after the number
    text = "Table 2 shows the means for each condition across the studies."
    assert _classify_nonprose(_block(text=text), text, 11.0) is None


def test_figurative_word_is_not_a_caption():
    text = "Figurative language pervades the advertising copy we analyzed."
    assert _classify_nonprose(_block(text=text), text, 11.0) is None


def test_footnote_detected_by_small_font_low_on_page():
    body = 11.0
    fn = _block(text="1 We thank the editor for helpful comments.", size=8.0, top=770.0)
    assert _is_footnote(fn, body) is True
    assert _classify_nonprose(fn, fn["text"], body) == "footnote"


def test_small_font_high_on_page_is_not_a_footnote():
    body = 11.0
    sup = _block(text="superscript-ish header text", size=8.0, top=90.0)
    assert _is_footnote(sup, body) is False


def test_normal_body_block_is_not_nonprose():
    body = 11.0
    text = ("We collected data from 200 participants in 2020 and analyzed three "
            "conditions using a standard mixed model approach.")
    assert _classify_nonprose(_block(text=text, size=11.0), text, body) is None


def test_equation_detected_by_low_letter_ratio_and_symbols():
    text = "(1) y = 2x + 3z - 4"
    assert _is_equation(text, _block(text=text)) is True
    assert _classify_nonprose(_block(text=text), text, 11.0) == "equation"


def test_equation_detected_by_math_font():
    text = "y = f(x)"
    assert _is_equation(text, _block(text=text, font="CMMI10")) is True


def test_table_flag_takes_priority_over_caption():
    text = "Table 1. Descriptive statistics"
    blk = _block(text=text, in_table=True)
    assert _classify_nonprose(blk, text, 11.0) == "table"


def _nonprose_tags(result):
    return [b.get("nonprose") for b in result["blocks"] if b.get("nonprose")]


def test_table_contents_tagged_as_table():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 120), wrap(FILLER), fontsize=BODY)
        # a ruled 2x2 grid further down the page
        shape = page.new_shape()
        for x in (72, 220, 360):
            shape.draw_line((x, 400), (x, 480))
        for y in (400, 440, 480):
            shape.draw_line((72, y), (360, y))
        shape.finish()
        shape.commit()
        page.insert_text((90, 425), "Construct"); page.insert_text((240, 425), "Mean")
        page.insert_text((90, 465), "Trust"); page.insert_text((240, 465), "4.21")

    result = extract_pdf(pdf_bytes(build))
    assert "table" in _nonprose_tags(result)
    # the body filler is NOT tagged
    assert any(b["type"] == "paragraph" and "nonprose" not in b
               and "ordinary body text" in b["text"] for b in result["blocks"])


def test_caption_block_tagged_as_caption():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 120), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 400), wrap("Figure 1. The proposed mediation model linking trust to loyalty."), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    cap = [b for b in result["blocks"] if b.get("nonprose") == "caption"]
    assert cap and cap[0]["text"].startswith("Figure 1.")


def test_footnote_block_tagged_as_footnote():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 120), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 790), wrap("1 We thank the editor and reviewers for their guidance on this work."), fontsize=8)

    result = extract_pdf(pdf_bytes(build))
    assert "footnote" in _nonprose_tags(result)


def test_nonprose_block_is_not_merged_into_following_prose():
    def build(doc):
        page = doc.new_page()
        page.insert_text((72, 120), wrap(FILLER), fontsize=BODY)
        page.insert_text((72, 400), wrap("Figure 1. The model."), fontsize=BODY)
        page.insert_text((72, 460), wrap("This following paragraph is ordinary prose that must stay separate."), fontsize=BODY)

    result = extract_pdf(pdf_bytes(build))
    cap = next(b for b in result["blocks"] if b.get("nonprose") == "caption")
    assert "ordinary prose that must stay separate" not in cap["text"]

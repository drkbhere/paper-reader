# Skip Non-Prose Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Detect tables, display equations, footnotes, and figure/table captions during PDF extraction, tag those blocks, and have the reader grey + skip them and the export omit them — behind one "Skip tables & equations" toggle (default on).

**Architecture:** Detection happens at extraction time (it needs font size, position, and PyMuPDF `find_tables()`), tagging each non-prose block with a `nonprose` field that is persisted. The player and export treat tagged blocks like references today: rendered greyed, skipped in playback, dropped from audio. Classification logic is pure functions over block dicts so it is unit-testable without real PDFs; the PyMuPDF table lookup is a thin wrapper.

**Tech Stack:** Python 3 / PyMuPDF (`fitz`) 1.27 / FastAPI / pytest (backend); vanilla JS (frontend).

**Reference spec:** `docs/superpowers/specs/2026-06-17-skip-nonprose-design.md`

**Working directory:** `~/PaperReader`, branch `feature/skip-nonprose`. Run all commands from there; tests with `.venv/bin/pytest`.

**Block schema after this feature:** `{type: "heading"|"paragraph", text: str, nonprose?: "table"|"equation"|"footnote"|"caption"}`. The `nonprose` key is absent on ordinary prose/headings.

---

### Task 1: Pure non-prose classifiers (TDD)

Add classification helpers to `backend/extractor.py`. These are pure functions over block dicts; no PDF or pipeline wiring yet.

**Files:**
- Modify: `backend/extractor.py` (add constants + helper functions near the other module-level regexes/helpers, e.g. after `_NUMBERED_HEADING_RE`)
- Test: `backend/tests/test_extractor.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_extractor.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_extractor.py -q -k "nonprose or caption or footnote or equation or center_in_any or figurative or grammatical"`
Expected: FAIL — `ImportError: cannot import name '_classify_nonprose'`

- [ ] **Step 3: Implement the classifiers**

In `backend/extractor.py`, add after the `_NUMBERED_HEADING_RE` definition:

```python
# Figure/table caption: starts with "Figure 3.", "Table 2:", "Fig. 1 —".
# Requires a separator after the number so prose like "Table 2 shows..." is kept.
_CAPTION_RE = re.compile(
    r"^(?:figure|fig|table|tbl|exhibit|panel)s?\.?\s*\d+\s*[.:)—–-]",
    re.I,
)

# Math-symbol density signal for display equations (no '-'/'/' — too common in prose).
_MATH_SYMBOL_RE = re.compile(
    r"[=+×÷±∑∏∫√∞≈≤≥≠∂∇∈∉⊂⊃∀∃<>^]"
    r"|[αβγδεζηθικλμνξοπρστυφχψω]",
    re.I,
)
_MATH_FONTS = ("cmmi", "cmsy", "cmex", "msam", "msbm", "symbol", "mathjax")
EQUATION_MAX_CHARS = 200


def _center_in_any(bbox, rects):
    """True if the centre of bbox falls inside any of the given fitz.Rects."""
    cx = (bbox[0] + bbox[2]) / 2
    cy = (bbox[1] + bbox[3]) / 2
    return any(r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1 for r in rects)


def _is_footnote(block, body_size):
    """A clearly-smaller-than-body block sitting in the lower part of the page."""
    if not body_size:
        return False
    top = block["bbox"][1]
    return block["size"] <= body_size - 1.0 and top >= block["page_height"] * 0.70


def _is_equation(text, block):
    """Short, symbol-dense (or math-font) block that reads as a display equation."""
    if len(text) > EQUATION_MAX_CHARS:
        return False
    nonspace = sum(1 for c in text if not c.isspace())
    if nonspace < 3:
        return False
    letter_ratio = sum(1 for c in text if c.isalpha()) / nonspace
    font = (block.get("font") or "").lower()
    if any(m in font for m in _MATH_FONTS) and letter_ratio < 0.85:
        return True
    return letter_ratio < 0.5 and bool(_MATH_SYMBOL_RE.search(text))


def _classify_nonprose(block, text, body_size):
    """Return 'table'|'caption'|'footnote'|'equation' for non-prose, else None.
    Priority: table > caption > footnote > equation (most reliable wins)."""
    if block.get("in_table"):
        return "table"
    if _CAPTION_RE.match(text):
        return "caption"
    if _is_footnote(block, body_size):
        return "footnote"
    if _is_equation(text, block):
        return "equation"
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest backend/tests/test_extractor.py -q`
Expected: PASS (existing extractor tests + the new ones)

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/extractor.py backend/tests/test_extractor.py
git -C ~/PaperReader commit -m "feat: pure non-prose classifiers for the extractor

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire detection into the extractor pipeline (TDD)

Capture font name + table membership in `_gather_blocks`, and tag blocks in `_build_blocks` (standalone, never merged into prose, never heading-detected).

**Files:**
- Modify: `backend/extractor.py` (`_gather_blocks`, new `_find_table_rects`, `_build_blocks`)
- Test: `backend/tests/test_extractor.py` (append integration tests)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_extractor.py`:

```python
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
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_extractor.py -q -k "tagged or not_merged"`
Expected: FAIL — blocks have no `nonprose` key (KeyError/`None` in `_nonprose_tags`, assertions fail)

- [ ] **Step 3: Implement `_find_table_rects` + gather changes**

In `backend/extractor.py`, add this helper (near `_classify_nonprose`):

```python
def _find_table_rects(page):
    """Bounding boxes of real (>=2x2) tables on the page; [] if none/unsupported."""
    try:
        finder = page.find_tables()
    except Exception:
        return []
    return [fitz.Rect(t.bbox) for t in finder.tables
            if t.row_count >= 2 and t.col_count >= 2]
```

Now modify `_gather_blocks` to (a) compute table rects once per page, (b) record each block's dominant font, and (c) set `in_table`. Replace the current `_gather_blocks` body with:

```python
def _gather_blocks(doc):
    """Flatten every text block on every page; lines keep their own font
    size/boldness so headings buried inside body blocks can be split out."""
    blocks = []
    for page_no, page in enumerate(doc):
        table_rects = _find_table_rects(page)
        for blk in page.get_text("dict")["blocks"]:
            if blk.get("type") != 0:  # 0 = text, 1 = image
                continue
            lines, block_sizes, block_bold, block_total = [], [], 0, 0
            block_fonts = Counter()
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
                    block_fonts[span.get("font", "")] += n
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
                "font": block_fonts.most_common(1)[0][0] if block_fonts else "",
                "in_table": _center_in_any(blk["bbox"], table_rects),
            })
    return blocks
```

(Note: the soft-hyphen replacement uses `­`, the same soft-hyphen character as the original `"­"` literal — keep whichever the file already uses; behavior is identical.)

- [ ] **Step 4: Implement `_build_blocks` tagging**

Replace `_build_blocks` in `backend/extractor.py` with:

```python
def _build_blocks(ordered, body_size):
    blocks = []
    for b in ordered:
        text = _join_lines(b["lines"])
        if not text:
            continue
        tag = _classify_nonprose(b, text, body_size)
        if tag:
            blocks.append({"type": "paragraph", "text": text, "nonprose": tag})
            continue
        if _is_heading(b, text, body_size):
            blocks.append({"type": "heading", "text": text})
            continue
        if (blocks and blocks[-1]["type"] == "paragraph"
                and "nonprose" not in blocks[-1]
                and _continues(blocks[-1]["text"], text)):
            prev = blocks[-1]["text"]
            if prev.endswith("-") and text[0].islower():
                blocks[-1]["text"] = prev[:-1] + text
            else:
                blocks[-1]["text"] = prev + " " + text
        else:
            blocks.append({"type": "paragraph", "text": text})
    return blocks
```

- [ ] **Step 5: Run the full extractor suite**

Run: `.venv/bin/pytest backend/tests/test_extractor.py -q`
Expected: PASS — new tagging tests AND all pre-existing extractor tests (dehyphenation, cross-page merge, headings, two-column, title) still green.

Then the whole suite: `.venv/bin/pytest backend/tests -q` — expect all green.

- [ ] **Step 6: Commit**

```bash
git -C ~/PaperReader add backend/extractor.py backend/tests/test_extractor.py
git -C ~/PaperReader commit -m "feat: tag table/caption/footnote/equation blocks during extraction

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Drop non-prose from audio export (TDD)

**Files:**
- Modify: `backend/export.py` (`drop_nonprose`, `start_export`, render)
- Modify: `backend/main.py` (export endpoint — new body param)
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_api.py`:

```python
from backend.export import drop_nonprose  # noqa: E402


def test_drop_nonprose_removes_tagged_blocks():
    blocks = [
        {"type": "paragraph", "text": "Real prose."},
        {"type": "paragraph", "text": "Trust 4.21 Loyalty 3.98", "nonprose": "table"},
        {"type": "paragraph", "text": "Figure 1. Model.", "nonprose": "caption"},
    ]
    kept = drop_nonprose(blocks)
    assert [b["text"] for b in kept] == ["Real prose."]


def test_export_text_omits_nonprose_when_dropped():
    blocks = [
        {"type": "paragraph", "text": "Real prose here."},
        {"type": "paragraph", "text": "Trust 4.21 Loyalty 3.98", "nonprose": "table"},
    ]
    text = export_text("T", drop_nonprose(blocks))
    assert "Real prose here." in text
    assert "4.21" not in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest backend/tests -q -k "drop_nonprose or omits_nonprose"`
Expected: FAIL — `ImportError: cannot import name 'drop_nonprose'`

- [ ] **Step 3: Implement**

In `backend/export.py`, add next to `drop_references`:

```python
def drop_nonprose(blocks: list[dict]) -> list[dict]:
    """Remove blocks tagged as tables/equations/footnotes/captions."""
    return [b for b in blocks if "nonprose" not in b]
```

Update `start_export`'s signature to add a `skip_nonprose` parameter (place it after `simplify_citations`):

```python
def start_export(pid: str, title: str, blocks: list[dict], out_path: Path,
                 voice: str | None = None, skip_references: bool = True,
                 simplify_citations: bool = True, skip_nonprose: bool = True) -> bool:
```

Inside the nested `render()`, where `content` is assembled, apply the new drop. The current code is:

```python
            content = blocks
            if skip_references:
                content = drop_references(blocks)
            text = export_text(title, content, simplify=simplify_citations)
```

Replace it with (note: chain off `content`, not `blocks`, so both filters compose):

```python
            content = blocks
            if skip_references:
                content = drop_references(content)
            if skip_nonprose:
                content = drop_nonprose(content)
            text = export_text(title, content, simplify=simplify_citations)
```

In `backend/main.py`, extend the export endpoint. It currently is:

```python
@app.post("/papers/{pid}/export")
def start_export(pid: str, voice: str | None = Body(None, embed=True),
                 skip_references: bool = Body(True, embed=True),
                 simplify_citations: bool = Body(True, embed=True)):
    rec = store.get(pid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    if voice is not None and not re.match(r"^[\w .()'-]{1,60}$", voice):
        raise HTTPException(status_code=400, detail="Unknown voice.")
    started = export.start_export(pid, rec["title"], rec["blocks"],
                                  store.export_path(pid), voice, skip_references,
                                  simplify_citations)
    return {"status": "running" if started else "already-running"}
```

Change it to add and forward `skip_nonprose`:

```python
@app.post("/papers/{pid}/export")
def start_export(pid: str, voice: str | None = Body(None, embed=True),
                 skip_references: bool = Body(True, embed=True),
                 simplify_citations: bool = Body(True, embed=True),
                 skip_nonprose: bool = Body(True, embed=True)):
    rec = store.get(pid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    if voice is not None and not re.match(r"^[\w .()'-]{1,60}$", voice):
        raise HTTPException(status_code=400, detail="Unknown voice.")
    started = export.start_export(pid, rec["title"], rec["blocks"],
                                  store.export_path(pid), voice, skip_references,
                                  simplify_citations, skip_nonprose)
    return {"status": "running" if started else "already-running"}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest backend/tests -q`
Expected: PASS (full suite). The real-`say` `test_export_renders_audio` may take tens of seconds — expected.

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/export.py backend/main.py backend/tests/test_api.py
git -C ~/PaperReader commit -m "feat: drop non-prose blocks from audio export behind a flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend toggle + grey/skip rendering

No JS test framework exists; make the edits then verify with Playwright. The frontend already serves blocks with a `nonprose` field from the API (Task 2). This mirrors the existing "Skip references" toggle: tagged segments get a flag, a CSS class greys them, and `nextPlayable` skips them — **no re-render on toggle**.

**Files:**
- Modify: `frontend/index.html` (reader sidebar + export modal)
- Modify: `frontend/app.js` (refs, state, `buildSegment`, `renderDocument`, `nextPlayable`, a CSS-apply fn, toggle wiring, export body)
- Modify: `frontend/style.css` (grey style for skipped non-prose)

Read `frontend/app.js` and `frontend/index.html` fully first. Key existing facts:
- `skipRefsToggle` sits in `#tocPanel`; its wiring (~line 301) sets `.checked` and on change updates `state.skipRefs`, persists `pr-skiprefs`, calls `applySkipRefs()`.
- `applySkipRefs()` does `docBody.classList.toggle("skip-refs", state.skipRefs)`.
- `buildSegment(text, isRef, isHeading)` builds a `<span class="seg">` (adds `ref` class when `isRef`) and pushes `{text, el, words, isRef}` to `state.segments`.
- `renderDocument()` loops `state.blocks`, tracks `inRefs`, computes `source`, calls `buildSegment(sent, inRefs, isHeading)`.
- `nextPlayable(i)` skips `state.skipRefs && state.segments[i].isRef`.
- Export modal: button click sets `exportSkipRefs.checked = state.skipRefs`; `exportStartBtn` POSTs a JSON body with `voice`, `skip_references`, `simplify_citations`.

- [ ] **Step 1: Add the two checkboxes (index.html)**

In `frontend/index.html`, in `#tocPanel`, after the "Simplify citations" `toc-toggle` label, add:

```html
    <label class="toc-toggle">
      <input type="checkbox" id="skipNonproseToggle" checked>
      <span>Skip tables &amp; equations</span>
    </label>
```

In the export modal, after the "Simplify citations" `modal-check` label, add:

```html
    <label class="modal-check">
      <input type="checkbox" id="exportSkipNonprose" checked>
      <span>Skip tables &amp; equations</span>
    </label>
```

- [ ] **Step 2: Add refs + state (app.js)**

Next to the `skipRefsToggle` / `simplifyToggle` refs, add:

```javascript
const skipNonproseToggle = $("skipNonproseToggle");
```

Next to the `exportSkipRefs` / `exportSimplify` refs, add:

```javascript
const exportSkipNonprose = $("exportSkipNonprose");
```

In `state`, next to `skipRefs` / `simplifyCites`, add:

```javascript
  skipNonprose: localStorage.getItem("pr-skipnonprose") !== "0",
```

- [ ] **Step 3: Flag non-prose segments in `buildSegment` + `renderDocument`**

Change `buildSegment`'s signature and stored object to carry an `isSkip` flag. Replace the function header and the `state.segments.push(...)` line. The current signature is `function buildSegment(text, isRef, isHeading) {` and it pushes `state.segments.push({ text, el, words, isRef });`. Change to:

```javascript
function buildSegment(text, isRef, isHeading, isSkip) {
```

add the CSS class when building the span — find the line `segEl.className = "seg" + (isRef ? " ref" : "");` and change it to:

```javascript
  segEl.className = "seg" + (isRef ? " ref" : "") + (isSkip ? " nonprose" : "");
```

and change the push to:

```javascript
  state.segments.push({ text, el: segEl, words, isRef, isSkip });
```

In `renderDocument`, the per-block loop calls `buildSegment(sent, inRefs, block.type === "heading")`. Change that call to pass the non-prose flag:

```javascript
      const segEl = buildSegment(sent, inRefs, block.type === "heading", !!block.nonprose);
```

- [ ] **Step 4: Skip non-prose during auto-advance + apply the CSS class**

Replace `nextPlayable` with one that also skips non-prose when the toggle is on:

```javascript
function nextPlayable(i) {
  while (i < state.segments.length &&
         ((state.skipRefs && state.segments[i].isRef) ||
          (state.skipNonprose && state.segments[i].isSkip))) i++;
  return i;
}
```

Add an apply function next to `applySkipRefs`:

```javascript
function applySkipNonprose() {
  docBody.classList.toggle("skip-nonprose", state.skipNonprose);
}
```

In `renderDocument`, where it currently calls `applySkipRefs();`, also call the new one:

```javascript
  buildToc();
  applySkipRefs();
  applySkipNonprose();
```

- [ ] **Step 5: Wire the toggle + export modal**

Next to the `simplifyToggle` wiring, add (no re-render needed — mirror `skipRefsToggle`):

```javascript
skipNonproseToggle.checked = state.skipNonprose;
skipNonproseToggle.addEventListener("change", () => {
  state.skipNonprose = skipNonproseToggle.checked;
  localStorage.setItem("pr-skipnonprose", state.skipNonprose ? "1" : "0");
  applySkipNonprose();
});
```

In the export-button click handler, next to `exportSimplify.checked = state.simplifyCites;` add:

```javascript
  exportSkipNonprose.checked = state.skipNonprose;
```

In the `exportStartBtn` handler, add `skip_nonprose` to the POST body so it becomes:

```javascript
      body: JSON.stringify({
        voice,
        skip_references: exportSkipRefs.checked,
        simplify_citations: exportSimplify.checked,
        skip_nonprose: exportSkipNonprose.checked,
      }),
```

- [ ] **Step 6: Grey style (style.css)**

In `frontend/style.css`, the references greying rules are at lines 477-478:

```css
.skip-refs .seg.ref { opacity: 0.4; }
.skip-refs .seg.ref:hover { opacity: 0.75; }
```

Immediately after them, add the matching non-prose rules so both behave identically:

```css
.skip-nonprose .seg.nonprose { opacity: 0.4; }
.skip-nonprose .seg.nonprose:hover { opacity: 0.75; }
```

- [ ] **Step 7: Verify with Playwright**

Start a clean server:
```bash
cd ~/PaperReader && PAPER_READER_DATA_DIR=/tmp/pr-verify-nonprose .venv/bin/uvicorn backend.main:app --port 8012 &
```
Wait ~2s. Generate a PDF with a real table + caption + body:
```bash
cd ~/PaperReader && .venv/bin/python -c "
import fitz, textwrap
doc = fitz.open(); page = doc.new_page()
page.insert_text((72,90), 'A Paper With A Table', fontsize=20)
body=('This is ordinary body prose establishing the dominant font size so the reader has plenty to read aloud across the page. '*3)
page.insert_text((72,140), '\n'.join(textwrap.wrap(body,80)), fontsize=11)
sh=page.new_shape()
for x in (72,220,360): sh.draw_line((x,520),(x,600))
for y in (520,560,600): sh.draw_line((72,y),(360,y))
sh.finish(); sh.commit()
page.insert_text((90,545),'Construct'); page.insert_text((240,545),'Mean')
page.insert_text((90,585),'Trust'); page.insert_text((240,585),'4.21')
page.insert_text((72,630), 'Figure 1. The proposed model.', fontsize=11)
doc.save('/tmp/nonprose.pdf'); doc.close(); print('ok')
"
```
Drive `http://127.0.0.1:8012` with the webapp-testing skill (Playwright):
1. Upload `/tmp/nonprose.pdf`; wait for the reader (entrance animations ~1s).
2. With the toggle ON (default): confirm the table cells ("4.21") and the "Figure 1." caption render with the muted/greyed class (`.seg.nonprose` present; `docBody` has `skip-nonprose`), and the body prose is normal.
3. Confirm a block carries `nonprose` by checking the page DOM has `.seg.nonprose` elements.
4. Uncheck `#skipNonproseToggle`; confirm `docBody` loses the `skip-nonprose` class (greying removed).
5. Reload + reopen from library; confirm `#skipNonproseToggle` persists unchecked, then re-check it.
6. Open Export audio; confirm `#exportSkipNonprose` mirrors the reader toggle.
Capture DOM-text/class assertions or a screenshot as evidence. Then kill the background uvicorn and remove `/tmp/pr-verify-nonprose` and `/tmp/nonprose.pdf`.

If Playwright cannot run, report DONE_WITH_CONCERNS — code complete, UI unverified. Do not fake verification.

- [ ] **Step 8: Commit**

```bash
git -C ~/PaperReader add frontend/index.html frontend/app.js frontend/style.css
git -C ~/PaperReader commit -m "feat: Skip tables & equations toggle in reader and export

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Update docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the feature**

In `CLAUDE.md`, under "Implementation notes", add a bullet:

```markdown
- **Skip non-prose** (tables/equations/footnotes/captions): detected at
  extraction time in `extractor.py` (`_classify_nonprose` + `_find_table_rects`,
  using font size, page position, math-symbol density, and PyMuPDF
  `find_tables()`). Tagged blocks carry a `nonprose` field, persisted in the
  saved JSON (re-upload to re-extract after detector changes). Conservative:
  prefers reading junk over dropping prose; equation detection deliberately
  under-detects. Frontend greys + skips tagged segments via the `pr-skipnonprose`
  toggle (default on); `nextPlayable` skips them, like references — no re-render.
  Export drops them via `drop_nonprose` behind the `skip_nonprose` flag.
```

- [ ] **Step 2: Commit**

```bash
git -C ~/PaperReader add CLAUDE.md
git -C ~/PaperReader commit -m "docs: note skip-non-prose design

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Run the **full** suite (`.venv/bin/pytest backend/tests -q`) after Tasks 2 and 3 — they touch shared pipeline/handlers.
- After backend edits, restart the live server on port 8000 if one is running (no `--reload` is in use).
- Detection is intentionally conservative. If a real paper shows prose being greyed, tighten the matching rule (add a guard) rather than loosen it.
- `nonprose` blocks still receive `text_simplified` from the citation feature's `annotate_blocks` — harmless, since they're skipped.

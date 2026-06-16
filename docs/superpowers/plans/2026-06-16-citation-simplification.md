# Citation & Figure-Reference Simplification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make playback and exported audio read academic prose cleanly by condensing in-text author-year citations to a first-author narrative form and stripping parenthetical figure/table asides, behind a global "Simplify citations" toggle (default on).

**Architecture:** A single pure Python function, `simplify_citations`, in a new `backend/textclean.py` is the only place citation logic lives. The backend attaches a computed `text_simplified` to each paragraph block on read (`/upload` response and `GET /papers/{id}`) — never stored, so the algorithm is always current. The frontend simply chooses `text_simplified` vs `text` based on the toggle. Export calls the same function.

**Tech Stack:** Python 3 / FastAPI / pytest (backend); vanilla JS (frontend); macOS `say` (export).

**Reference spec:** `docs/superpowers/specs/2026-06-16-citation-simplification-design.md`

**Working directory:** `~/PaperReader`. Run all commands from there. Run tests with `.venv/bin/pytest`.

---

### Task 1: `simplify_citations` core transform (TDD)

**Files:**
- Create: `backend/textclean.py`
- Test: `backend/tests/test_textclean.py`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_textclean.py`:

```python
from backend.textclean import simplify_citations


def test_condenses_multi_author_group():
    src = "Brand trust mediates loyalty (Smith, Jones & Lee, 2020; Brown et al., 2019)."
    assert simplify_citations(src) == (
        "Brand trust mediates loyalty (Smith and colleagues; Brown and colleagues)."
    )


def test_single_author_keeps_surname_only():
    assert simplify_citations("Loyalty rose (Kumar, 2021).") == "Loyalty rose (Kumar)."


def test_two_authors_with_and_is_multi():
    assert simplify_citations("(Smith and Jones, 2018)") == "(Smith and colleagues)"


def test_drops_leadin_words():
    assert simplify_citations("as shown (see Smith, 2020)") == "as shown (Smith)"
    assert simplify_citations("e.g. (e.g., Kumar, 2021)") == "e.g. (Kumar)"


def test_removes_bare_year_parenthetical():
    assert simplify_citations("Smith (2020) found that trust matters.") == (
        "Smith found that trust matters."
    )


def test_removes_bare_year_with_page_and_year_list():
    assert simplify_citations("As argued (2020, p. 14), trust matters.") == (
        "As argued, trust matters."
    )
    assert simplify_citations("Earlier work (2019, 2021) agrees.") == "Earlier work agrees."


def test_strips_parenthetical_figure_table_asides():
    assert simplify_citations("Loyalty increased (see Table 2).") == "Loyalty increased."
    assert simplify_citations("The effect held (Figure 3).") == "The effect held."
    assert simplify_citations("Means differ (Fig. 3a).") == "Means differ."


def test_keeps_grammatical_figure_mention():
    assert simplify_citations("Figure 3 shows the interaction.") == (
        "Figure 3 shows the interaction."
    )


def test_leaves_statistics_untouched():
    for stat in ("(p < .05)", "(M = 3.42, SD = 0.81)", "(N = 200)", "(95% CI [.12, .34])"):
        assert simplify_citations(f"The result was significant {stat}.") == (
            f"The result was significant {stat}."
        )


def test_leaves_plain_parenthetical_untouched():
    assert simplify_citations("This was true (in most cases).") == (
        "This was true (in most cases)."
    )


def test_mixed_content_group_left_untouched():
    # one part is not a citation -> be conservative, change nothing
    src = "Trust matters (Smith, 2020; in this specific case)."
    assert simplify_citations(src) == src


def test_cleans_double_spaces_and_space_before_punctuation():
    assert simplify_citations("Trust mattered (see Table 2) ; loyalty rose.") == (
        "Trust mattered; loyalty rose."
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest backend/tests/test_textclean.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'backend.textclean'`

- [ ] **Step 3: Write the implementation**

Create `backend/textclean.py`:

```python
"""Read-aloud text cleanup: condense in-text author-year citations and strip
parenthetical figure/table asides so academic prose listens cleanly.

Conservative by design: a parenthetical that does not clearly match a rule is
left untouched (prefer under-cleaning over mangling a sentence).
"""

import re

_YEAR = r"(?:1[89]\d\d|20\d\d)[a-z]?"
_YEAR_RE = re.compile(_YEAR)

# Lead-in words that precede a citation and should be dropped from the surname.
_LEADIN_RE = re.compile(
    r"^(?:see(?:\s+also)?|e\.g\.?,?|cf\.?,?|for\s+(?:a\s+)?review|viz\.?,?)\s+",
    re.I,
)

# Parenthetical whose content is ONLY years plus page refs / commas / connectors.
_BARE_YEAR_CONTENT_RE = re.compile(
    r"^(?:" + _YEAR + r"|pp?\.|n\.d\.|in\s+press|forthcoming|and|&|[,;]|\s|\d+)+$",
    re.I,
)

# Parenthetical figure/table aside: optional lead-ins, then Fig/Figure/Table/Tbl + number.
_FIG_TABLE_CONTENT_RE = re.compile(
    r"^(?:see|cf\.?|e\.g\.?,?|also|and|[,;]|\s)*"
    r"(?:figs?\.?|figures?|t(?:able|bl)s?\.?)\s*\d.*$",
    re.I,
)

_PAREN_RE = re.compile(r"\(([^()]*)\)")
_ETAL_RE = re.compile(r"\bet\s+al\.?.*$", re.I)


def simplify_citations(text: str) -> str:
    """Condense author-year citations and remove parenthetical fig/table asides."""
    def repl(match):
        inner = match.group(1).strip()
        if not inner:
            return match.group(0)
        if _FIG_TABLE_CONTENT_RE.match(inner):
            return ""
        if _YEAR_RE.search(inner) and _BARE_YEAR_CONTENT_RE.match(inner):
            return ""
        condensed = _condense_group(inner)
        if condensed is not None:
            return "(" + condensed + ")"
        return match.group(0)

    return _cleanup(_PAREN_RE.sub(repl, text))


def _condense_group(inner: str) -> str | None:
    """Condense a ';'-separated citation group, or None if any part isn't a citation."""
    parts = []
    for raw in inner.split(";"):
        one = _condense_one(raw.strip())
        if one is None:
            return None
        parts.append(one)
    return "; ".join(parts) if parts else None


def _condense_one(part: str) -> str | None:
    """'Smith, Jones & Lee, 2020' -> 'Smith and colleagues'; or None if not a citation."""
    year = _YEAR_RE.search(part)
    if not year:
        return None
    authors = _LEADIN_RE.sub("", part[: year.start()]).strip().rstrip(",").strip()
    if not authors:
        return None
    multi = bool(
        re.search(r"\bet\s+al", authors, re.I)
        or "&" in authors
        or re.search(r"\band\b", authors, re.I)
        or "," in authors
    )
    first = _ETAL_RE.sub("", re.split(r",|&|\band\b", authors)[0]).strip()
    if not re.search(r"[A-ZÀ-Þ]", first):  # needs a capitalised surname
        return None
    return f"{first} and colleagues" if multi else first


def _cleanup(text: str) -> str:
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest backend/tests/test_textclean.py -q`
Expected: PASS (12 passed)

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/textclean.py backend/tests/test_textclean.py
git -C ~/PaperReader commit -m "feat: simplify_citations text transform

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `annotate_blocks` helper (TDD)

**Files:**
- Modify: `backend/textclean.py` (append `annotate_blocks`)
- Test: `backend/tests/test_textclean.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_textclean.py`:

```python
from backend.textclean import annotate_blocks


def test_annotate_blocks_adds_simplified_text_for_paragraphs_only():
    blocks = [
        {"type": "heading", "text": "Results"},
        {"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."},
    ]
    out = annotate_blocks(blocks)
    assert out[0]["text_simplified"] == "Results"          # heading unchanged
    assert out[1]["text_simplified"] == "Loyalty rose (Kumar)."
    assert out[1]["text"] == "Loyalty rose (Kumar, 2021)."  # original preserved
    assert blocks[1] == {"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."}  # input not mutated
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest backend/tests/test_textclean.py::test_annotate_blocks_adds_simplified_text_for_paragraphs_only -q`
Expected: FAIL — `ImportError: cannot import name 'annotate_blocks'`

- [ ] **Step 3: Implement**

Append to `backend/textclean.py`:

```python
def annotate_blocks(blocks: list[dict]) -> list[dict]:
    """Return copies of blocks with a `text_simplified` field (paragraphs cleaned,
    headings passed through). Input blocks are not mutated."""
    out = []
    for b in blocks:
        nb = dict(b)
        nb["text_simplified"] = (
            simplify_citations(b["text"]) if b["type"] == "paragraph" else b["text"]
        )
        out.append(nb)
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest backend/tests/test_textclean.py -q`
Expected: PASS (13 passed)

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/textclean.py backend/tests/test_textclean.py
git -C ~/PaperReader commit -m "feat: annotate_blocks adds text_simplified per paragraph

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Serve `text_simplified` from the API (TDD)

**Files:**
- Modify: `backend/main.py:13` (import) and the `upload` + `get_paper` handlers
- Test: `backend/tests/test_api.py` (append)

- [ ] **Step 1: Write the failing test**

`backend/tests/test_api.py` already has a module-level `client = TestClient(app)` and a `small_pdf(seed)` helper built with PyMuPDF + `textwrap.wrap` (lines must be wrapped or PyMuPDF drops off-page text). Append a citation-bearing PDF helper and two tests that follow the same style:

```python
def citation_pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Paper With Citations", fontsize=20)
    body = ("Loyalty rose strongly over the studied period (Kumar, 2021) "
            "and the effect held across the whole sample (see Table 2). " * 4)
    page.insert_text((72, 200), "\n".join(textwrap.wrap(body, 80)), fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_get_paper_includes_simplified_text():
    pid = client.post("/upload",
                      files={"file": ("cite.pdf", citation_pdf(), "application/pdf")}).json()["id"]
    rec = client.get(f"/papers/{pid}").json()
    paras = [b for b in rec["blocks"] if b["type"] == "paragraph"]
    joined = " ".join(b["text_simplified"] for b in paras)
    assert "(Kumar)" in joined
    assert "(Kumar, 2021)" not in joined
    assert "Table 2" not in joined
    assert any("(Kumar, 2021)" in b["text"] for b in paras)  # original preserved


def test_upload_response_includes_simplified_text():
    doc = client.post("/upload",
                      files={"file": ("cite2.pdf", citation_pdf(), "application/pdf")}).json()
    paras = [b for b in doc["blocks"] if b["type"] == "paragraph"]
    assert any("(Kumar)" in b["text_simplified"] for b in paras)
```

Note: the existing `test_upload_saves_to_library_and_roundtrips` asserts
`fetched["blocks"] == doc["blocks"]`. That stays green because BOTH `/upload`
and `GET /papers/{id}` annotate identically, so the field is present on both
sides.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest backend/tests/test_api.py -q -k simplified`
Expected: FAIL — `KeyError: 'text_simplified'`

- [ ] **Step 3: Implement**

In `backend/main.py`, add the import next to the other backend imports (currently around line 12-14):

```python
from . import export, textclean
```

(Remove the now-redundant standalone `from . import export` line so `export` isn't imported twice.)

Change the `upload` handler's return so the response carries simplified text (keep `store.save` receiving the original blocks — save happens before annotation):

```python
    doc["id"] = store.save(data, doc)
    doc["blocks"] = textclean.annotate_blocks(doc["blocks"])
    return doc
```

Change `get_paper` to annotate on read:

```python
@app.get("/papers/{pid}")
def get_paper(pid: str):
    rec = store.get(pid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    rec["blocks"] = textclean.annotate_blocks(rec["blocks"])
    return rec
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest backend/tests/test_api.py -q`
Expected: PASS (all existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/main.py backend/tests/test_api.py
git -C ~/PaperReader commit -m "feat: API serves text_simplified on upload and paper fetch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Apply simplification in audio export (TDD)

**Files:**
- Modify: `backend/export.py` (import, `export_text`, `start_export`)
- Modify: `backend/main.py` (`start_export` endpoint — new body param)
- Test: `backend/tests/test_api.py` or `test_export`-style test — see below

- [ ] **Step 1: Write the failing test**

Add a unit test for `export_text` (pure, no `say` needed). `backend/tests/test_api.py` already imports `export_text` at the top (line 11) — append these two tests there, no new import needed:

```python
def test_export_text_simplifies_citations_when_enabled():
    blocks = [{"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."}]
    assert "(Kumar)" in export_text("T", blocks, simplify=True)
    assert "(Kumar, 2021)" not in export_text("T", blocks, simplify=True)


def test_export_text_keeps_citations_when_disabled():
    blocks = [{"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."}]
    assert "(Kumar, 2021)" in export_text("T", blocks, simplify=False)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest backend/tests -q -k export_text`
Expected: FAIL — `TypeError: export_text() got an unexpected keyword argument 'simplify'`

- [ ] **Step 3: Implement**

In `backend/export.py`, add the import near the top:

```python
from .textclean import simplify_citations
```

Replace `export_text` with a version that takes a `simplify` flag:

```python
def export_text(title: str, blocks: list[dict], simplify: bool = True) -> str:
    parts = [title, PARAGRAPH_PAUSE]
    for b in blocks:
        if b["type"] == "heading":
            parts.append(f"{HEADING_PAUSE} {b['text']} {PARAGRAPH_PAUSE}")
        else:
            text = simplify_citations(b["text"]) if simplify else b["text"]
            parts.append(f"{text} {PARAGRAPH_PAUSE}")
    return "\n".join(parts)
```

Thread the flag through `start_export`. Change its signature and the `render` closure's `export_text` call:

```python
def start_export(pid: str, title: str, blocks: list[dict], out_path: Path,
                 voice: str | None = None, skip_references: bool = True,
                 simplify_citations: bool = True) -> bool:
```

Inside `render`, the existing line `text = export_text(title, content)` becomes:

```python
            text = export_text(title, content, simplify=simplify_citations)
```

Note: `simplify_citations` is now both an imported function name and a parameter name in this function's scope. That shadowing is fine because `export_text` (not `start_export`) is what calls the imported function. Do NOT call the imported `simplify_citations` inside `start_export`.

In `backend/main.py`, extend the export endpoint to accept and forward the flag:

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

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest backend/tests -q`
Expected: PASS (full suite green)

- [ ] **Step 5: Commit**

```bash
git -C ~/PaperReader add backend/export.py backend/main.py backend/tests
git -C ~/PaperReader commit -m "feat: simplify citations in audio export behind a flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Frontend toggle + field selection

No JS test harness exists; the change is a field pick plus a re-render refactor. Verify with Playwright per CLAUDE.md practice. Make the edits, then do the manual verification step.

**Files:**
- Modify: `frontend/index.html` (reader toolbar + export modal)
- Modify: `frontend/app.js` (state, `renderDocument` extraction, toggle, export body)

- [ ] **Step 1: Add the reader toolbar toggle**

In `frontend/index.html`, inside `<aside id="tocPanel">`, after the existing "Skip references" label (lines 55-58), add:

```html
    <label class="toc-toggle">
      <input type="checkbox" id="simplifyToggle" checked>
      <span>Simplify citations</span>
    </label>
```

- [ ] **Step 2: Add the export-modal checkbox**

In `frontend/index.html`, after the `exportSkipRefs` label (lines 106-109), add:

```html
    <label class="modal-check">
      <input type="checkbox" id="exportSimplify" checked>
      <span>Simplify citations</span>
    </label>
```

- [ ] **Step 3: Add state + DOM refs in `app.js`**

In the DOM refs block (around line 18), add `simplifyToggle` to the `tocList`/`skipRefsToggle` line:

```javascript
const tocList = $("tocList"), skipRefsToggle = $("skipRefsToggle");
const simplifyToggle = $("simplifyToggle");
```

In the export-modal refs (around line 25), add `exportSimplify`:

```javascript
const exportSkipRefs = $("exportSkipRefs"), exportStatus = $("exportStatus");
const exportSimplify = $("exportSimplify");
```

In the `state` object (around line 37, next to `skipRefs`), add a field and a place to hold loaded blocks:

```javascript
  skipRefs: localStorage.getItem("pr-skiprefs") !== "0",
  simplifyCites: localStorage.getItem("pr-simplify") !== "0",
  blocks: [],       // raw blocks from the API, for re-render on toggle
```

- [ ] **Step 4: Extract `renderDocument` and use the chosen text field**

In `app.js`, `enterReader` currently switches views then builds the document inline (lines 206-239). Refactor so the document build lives in a reusable `renderDocument()`. Replace the body of `enterReader` from the `let inRefs = false;` loop through `updateProgress();` with a call to render, and move the building logic into a new function. Concretely:

Replace lines 216-239 (the `let inRefs = false;` block through `updateProgress();`) with:

```javascript
  state.blocks = doc.blocks;
  renderDocument();
}

// Build (or rebuild) the document body from state.blocks using the current
// simplify-citations preference. Safe to call repeatedly (toggle re-render).
function renderDocument() {
  docBody.innerHTML = "";
  state.segments = [];
  state.toc = [];
  state.lit = [];
  state.liveSeg = null;

  let inRefs = false;
  for (const block of state.blocks) {
    const el = document.createElement(block.type === "heading" ? "h2" : "p");
    if (block.type === "heading") inRefs = REFS_RE.test(block.text);
    const source = state.simplifyCites && block.text_simplified != null
      ? block.text_simplified
      : block.text;
    const sentences = block.type === "heading" ? [source] : splitSentences(source);
    sentences.forEach((sent, i) => {
      const segEl = buildSegment(sent, inRefs, block.type === "heading");
      el.appendChild(segEl);
      if (i < sentences.length - 1) el.appendChild(document.createTextNode(" "));
    });
    docBody.appendChild(el);
  }
  buildToc();
  applySkipRefs();

  localStorage.setItem(totalKey(state.paperId), String(state.segments.length));
  const saved = Number(localStorage.getItem(posKey(state.paperId))) || 0;
  if (saved > 0 && saved < state.segments.length) {
    state.segIdx = saved;
    setLiveSegment(state.segments[saved]);
    setTimeout(() => state.segments[saved].el.scrollIntoView({ block: "center" }), 80);
  }
  updateProgress();
}
```

Leave the lines above (view switching, `document.title`, `docTitle.textContent`, `state.paperId = doc.id;`, lines 206-215) intact inside `enterReader`.

- [ ] **Step 5: Wire the reader toggle (re-render, preserve position)**

In `app.js`, next to the existing `skipRefsToggle` wiring (around lines 301-306), add:

```javascript
simplifyToggle.checked = state.simplifyCites;
simplifyToggle.addEventListener("change", () => {
  state.simplifyCites = simplifyToggle.checked;
  localStorage.setItem("pr-simplify", state.simplifyCites ? "1" : "0");
  const keep = state.segIdx;
  state.gen++;
  speechSynthesis.cancel();
  if (state.status === "playing") { state.status = "paused"; updatePlayBtn(); }
  renderDocument();
  state.segIdx = Math.min(keep, state.segments.length - 1);
  if (state.segIdx > 0) setLiveSegment(state.segments[state.segIdx]);
  updateProgress();
});
```

- [ ] **Step 6: Send the flag on export and sync the modal checkbox**

In `app.js`, in the export-button click handler (around line 543) set the checkbox to the current preference, next to the existing `exportSkipRefs.checked = state.skipRefs;` line:

```javascript
  exportSkipRefs.checked = state.skipRefs;
  exportSimplify.checked = state.simplifyCites;
```

In the `exportStartBtn` click handler (around line 581), add `simplify_citations` to the POST body:

```javascript
      body: JSON.stringify({
        voice,
        skip_references: exportSkipRefs.checked,
        simplify_citations: exportSimplify.checked,
      }),
```

- [ ] **Step 7: Manual verification with Playwright**

Start a clean server and verify both states. Run:

```bash
cd ~/PaperReader && PAPER_READER_DATA_DIR=/tmp/pr-verify .venv/bin/uvicorn backend.main:app --port 8000
```

Then, using the webapp-testing skill / Playwright against `http://127.0.0.1:8000`:
1. Upload a PDF containing a paragraph with `(Kumar, 2021)` and `(see Table 2)`.
2. Confirm the reader shows `(Kumar)` and no `(see Table 2)` with the toggle ON.
3. Uncheck "Simplify citations"; confirm the document re-renders showing `(Kumar, 2021)` and `(see Table 2)` again, reading position retained.
4. Reload; confirm the toggle stays OFF (persisted) and re-check it.
5. Open Export audio; confirm the "Simplify citations" checkbox mirrors the toggle.

Entrance animations take ~1s — wait before screenshots (per CLAUDE.md).

- [ ] **Step 8: Commit**

```bash
git -C ~/PaperReader add frontend/index.html frontend/app.js
git -C ~/PaperReader commit -m "feat: Simplify citations toggle in reader and export

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Update docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Document the new behavior**

In `CLAUDE.md`, under "Implementation notes", add a bullet:

```markdown
- **Citation simplification** lives in ONE place: `backend/textclean.py`
  (`simplify_citations`). The backend attaches `text_simplified` to each
  paragraph block on read (`/upload`, `GET /papers/{id}`) — not stored, always
  recomputed. Frontend picks `text_simplified` vs `text` from the
  `pr-simplify` localStorage toggle (default on) and re-renders via
  `renderDocument()`. Export passes `simplify_citations` through to
  `export_text`. Author-year (APA) only; conservative — unmatched parentheticals
  are left untouched.
```

- [ ] **Step 2: Commit**

```bash
git -C ~/PaperReader add CLAUDE.md
git -C ~/PaperReader commit -m "docs: note citation simplification design

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Notes for the implementer

- Run the **full** suite (`.venv/bin/pytest backend/tests -q`) after Tasks 3 and 4 — they touch shared handlers.
- After backend edits, restart the live server on port 8000 if one is running (no `--reload` is in use).
- Keep `simplify_citations` conservative: if a real paper shows a parenthetical being mangled, prefer tightening the match (add a guard) over broadening it.

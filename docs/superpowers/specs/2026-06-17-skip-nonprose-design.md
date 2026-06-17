# Skip Non-Prose (Tables, Equations, Footnotes, Captions) — Design

**Date:** 2026-06-17
**Status:** Approved, pending implementation plan
**Builds on:** the citation-simplification feature (shared toggles / export params).

## Problem

When a paper is read aloud, the extractor feeds *every* text block to speech —
including table cells, display equations, footnotes, and figure/table captions.
These read as gibberish ("Construct M SD 1 2 3 Trust 4.21 0.83…"), and
footnotes can interrupt a sentence mid-flow. The goal is to keep these off the
audio while still letting the reader glance at them on screen.

## Decisions (from brainstorming)

- **Filter all four kinds:** tables, display equations, footnotes, figure/table
  captions.
- **Keep visible, don't speak.** Detected non-prose stays on screen (greyed,
  like skipped references already are), is skipped during playback, and is
  omitted from the exported audio. This mirrors the existing "Skip references"
  behavior.
- **One global toggle**, "Skip tables & equations", **default ON**, in both the
  reader sidebar and the export modal (alongside "Skip references" and
  "Simplify citations").
- **Conservative detection:** prefer reading a little junk over dropping real
  prose. Equation detection is the riskiest and is kept the most conservative
  (it will under-detect rather than clip prose).

## Architecture

Detection happens at **extraction time** — identifying a table/footnote/equation
needs font size, position, and PyMuPDF `find_tables()`, all discarded once blocks
are stored as `{type, text}`. The extractor therefore **tags** non-prose blocks
and the tag is persisted with the paper.

- Trade-off: improving the detector later requires re-uploading the paper to
  re-extract. The content-hash store re-extracts on re-upload, so this is a
  non-issue in practice.
- Rejected alternatives: detecting at read-time (impossible — positional/font
  data is gone), and a layout-ML model (overkill, heavy dependency, against the
  app's lightweight ethos).

### Block schema

Each block gains an optional field:

```
nonprose: "table" | "equation" | "footnote" | "caption"
```

Absent on normal prose/heading blocks. A non-prose block keeps
`type: "paragraph"` so existing consumers still work; it is never merged into
surrounding prose and never treated as a heading.

### Detection rules

Conservative throughout. To isolate the fiddly PyMuPDF part from the testable
logic, table-rectangle lookup is a thin wrapper (`_find_table_rects(page)`),
and classification is **pure functions over block dicts + injected table
rectangles**, unit-tested without real PDFs.

PyMuPDF 1.27 is installed (`find_tables()` available).

1. **Tables** (most reliable). `page.find_tables()`; keep only grids with
   ≥2 rows × ≥2 cols. Tag a text block as `table` when the **center** of its
   bounding box falls inside a kept table's box (deterministic and robust to
   slightly loose table boundaries).
2. **Captions** (safe). A block whose text starts with
   `Figure | Fig. | Table | Tbl. | Exhibit | Panel` followed by a number
   (optionally `n.`/`n:`) → `caption`. Matches the block lead only.
3. **Footnotes** (decent). A block whose dominant font size is clearly smaller
   than body size (e.g. `size <= body_size - 1.0`, tuned during implementation)
   **and** whose top is in the lower portion of the page (e.g. below ~70% of
   page height) → `footnote`. Not applied to headings or captions.
4. **Equations** (riskiest — most conservative; under-detects). A short,
   isolated block (few words, ≤ ~200 chars) with a low alphabetic-character
   ratio and/or math-font names (e.g. `CMMI`, `CMSY`, `MSAM`, `Symbol`) and
   math-symbol density → `equation`. To support this, `_gather_blocks` also
   captures the dominant font name per block.

Detection priority when a block matches more than one rule: table > caption >
footnote > equation (most reliable wins).

### Pipeline placement

The extractor pipeline today is: gather → drop headers/footers → reading order
→ body font → split heading lines → build blocks. A new classification step
runs after body font is known and produces the `nonprose` tag carried into the
final blocks:

- `_gather_blocks` additionally records, per page, the kept table rectangles
  (via `_find_table_rects`) and marks each raw block `in_table` if inside one;
  it also records each block's dominant font name.
- A pure `_classify_nonprose(block, body_size, page_height)` (plus the
  `in_table` flag) returns the tag or `None`.
- `_build_blocks` emits a tagged block standalone (no merge, no heading
  detection) with its `nonprose` field set.

### Data flow

- **Store/API**: the `nonprose` tag is part of the saved block JSON.
  `annotate_blocks` (citation feature) still adds `text_simplified` to
  paragraph blocks, including non-prose ones — harmless, since they're skipped.
- **Player** (`frontend/app.js`): a `state.skipNonprose` preference
  (`localStorage` key `pr-skipnonprose`, default on — absent ⇒ on). In
  `renderDocument`, a block with `block.nonprose` marks its segments
  `isSkip = true`. A CSS class on `docBody` greys skipped segments, and
  `nextPlayable` skips `isSkip` segments when `skipNonprose` is on. This needs
  **no re-render** on toggle (unlike citations) — exactly like the references
  toggle: flip a CSS class + the playback-skip check.
- **Export** (`backend/export.py`): `drop_nonprose(blocks)` removes blocks with
  a `nonprose` tag; a `skip_nonprose` body param (default `True`) on
  `POST /papers/{pid}/export` controls it, applied alongside `drop_references`.
- **UI**: a "Skip tables & equations" checkbox in the reader sidebar
  (`#skipNonproseToggle`) and the export modal (`#exportSkipNonprose`).

## Testing

- **Pure unit tests** for each classifier with synthetic block dicts:
  - caption lead patterns (`Figure 3.`, `Table 2:`, `Fig. 1`) and non-matches
    (`Figurative language…` must NOT match);
  - footnote (small font + low position) vs body (normal font) vs small-font
    text high on the page (must NOT tag);
  - equation (low letter ratio / math symbols) vs normal prose with a few
    numbers (must NOT tag);
  - table-overlap: a block inside an injected table rect tagged `table`, a block
    outside not.
  - priority: a block matching two rules gets the higher-priority tag.
- **One light integration test**: generate a PDF with a real ruled table
  (drawn lines + cell text) and assert `_find_table_rects` finds it and a block
  inside is tagged `table`. If `find_tables` proves flaky on synthetic input,
  fall back to asserting the overlap logic with injected rects (the pure path).
- **API test**: a paper with a non-prose block exposes the `nonprose` field via
  `GET /papers/{id}`; `export_text` with `skip_nonprose` omits it (and includes
  it when disabled).
- **Frontend (Playwright)**: a non-prose block renders greyed and is skipped
  during playback; toggling "Skip tables & equations" off reads it.

## Implementation order

Within one plan, build detectors in precision order so value lands early and the
risky one is last: **tables + captions → footnotes → equations**, then the
player toggle, then export, then docs.

## Out of scope

- Per-type toggles (single combined toggle only).
- Reconstructing/reading tables in a sensible spoken form (we skip, not
  summarize).
- Inline math inside prose sentences (only block-level display equations).
- OCR / scanned PDFs.

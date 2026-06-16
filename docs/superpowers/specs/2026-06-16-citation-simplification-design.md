# Citation & Figure-Reference Simplification — Design

**Date:** 2026-06-16
**Status:** Approved, pending implementation plan

## Problem

When listening to marketing/management research papers, in-text citations and
parenthetical figure/table asides read aloud as noise. A sentence like

> Brand trust mediates loyalty (Smith, Jones & Lee, 2020; Brown et al., 2019),
> and effects increased (see Table 2).

is tiring to hear with every author, ampersand, and year spoken in full. The
goal is to make playback (and the exported audio) read like prose while
preserving who said what.

## Decisions (from brainstorming)

- **Citations are condensed, not stripped**, to a first-author narrative form:
  - Multi-author / "et al." / "&" → `Surname and colleagues`
  - Single author → `Surname`
  - Multiple `;`-separated citations stay joined: `(Smith and colleagues; Brown and colleagues)`
- **Figure/table references**: strip only *parenthetical asides*
  (`(see Table 2)`, `(Figure 3)`); leave grammatical mentions
  (`Figure 3 shows…`) untouched.
- **Formats**: author-year (APA) only. Numeric/bracketed styles (`[12]`) are
  out of scope.
- **Control**: a single global "Simplify citations" toggle, **default ON**,
  applied to both live playback and audio export, remembered across papers
  (mirrors the existing "Skip references" toggle).

## Architecture

Single Python source of truth. Citation parsing is regex-heavy; duplicating it
in JS and Python (as "Skip references" does today) would create a sync burden
that CLAUDE.md already warns about. Instead:

- New module **`backend/textclean.py`** exposing one pure function:
  `simplify_citations(text: str) -> str`.
- The backend computes a **`text_simplified`** value for each paragraph block
  **on read** — in the `POST /upload` response and `GET /papers/{id}` — and
  attaches it to each block in the JSON. It is **not stored** on disk, so the
  algorithm is always current and no migration of existing papers is needed.
  Headings are passed through unchanged (`text_simplified == text`).
- The **frontend** picks a field: `block.text_simplified` when the toggle is
  on, else `block.text`. No JavaScript citation regex exists.
- **Export** (`backend/export.py`) calls the same `simplify_citations` when its
  flag is set, before building the `say` text.

### Rejected alternatives

- **Dual JS + Python regex** (mirroring Skip references): instant client-side
  toggle, but two implementations to keep in sync — the existing pain point.
- **Transform at extraction time**: would require re-extraction/migration of
  stored papers and storing both original and simplified copies.

## Transform rules (`simplify_citations`)

Conservative by design: when a parenthetical does not clearly match a rule,
leave it untouched. Prefer under-cleaning over mangling a sentence.

1. **Author-year citation groups.** A parenthetical `(...)` whose
   `;`-separated parts look like author-year citations (each part contains a
   year token `\b(1[89]\d\d|20\d\d)[a-z]?\b` preceded by author-like text).
   Condense each part:
   - First author surname = the leading name token(s), after dropping a lead-in
     signal word (`see`, `e.g.,`, `cf.`, `for a review`, `for review`).
   - Multi-author if the part contains `et al`, `&`, ` and `, or a second
     comma-separated name before the year → `Surname and colleagues`.
   - Otherwise → `Surname`.
   - Rejoin condensed parts with `; ` inside one set of parentheses.
   - If not all parts qualify (mixed content), leave the whole parenthetical
     untouched.
   - Example: `(Smith, Jones & Lee, 2020; Brown et al., 2019)`
     → `(Smith and colleagues; Brown and colleagues)`;
     `(Kumar, 2021)` → `(Kumar)`.

2. **Bare year parentheticals** (narrative "Smith (2020) found…"). A
   parenthetical whose content is only years plus optional page refs/commas
   (`(2020)`, `(2020a)`, `(2019, 2021)`, `(2020, p. 14)`) → removed entirely,
   leaving `Smith found…`.

3. **Parenthetical figure/table asides.** A parenthetical whose content is an
   optional lead-in word followed by `Fig`/`Fig.`/`Figure`/`Table`/`Tbl` and a
   number (`(see Table 2)`, `(Figure 3)`, `(Fig. 3a)`, `(cf. Figure 4)`,
   `(Tables 1–2)`) → removed. Grammatical mentions (`Figure 3 shows…`,
   `as Table 2 reports`) are untouched because they are not parenthetical.

4. **Cleanup.** After removals, collapse leftover double spaces, space-before-
   punctuation (` .`, ` ,`, ` ;`), and trim spaces just inside any remaining
   parentheses.

5. **Never touched.** Parentheticals containing statistics or anything without
   a 4-digit year: `(p < .05)`, `(M = 3.42, SD = 0.81)`, `(N = 200)`,
   `(95% CI [.12, .34])`, `(in this case)`. The year requirement in rules 1–2
   protects these automatically.

## UI

- A "Simplify citations" checkbox beside "Skip references" in **both** the
  reader toolbar and the export modal.
- `localStorage` key `pr-simplify`, default on (absent ⇒ on).
- Toggling in the reader re-renders the document from the already-loaded blocks.
  The document-building portion of `enterReader` is extracted into a
  `renderDocument()` function so it can be re-run on toggle. Reading position
  (`state.segIdx`, clamped) is preserved across the re-render; playback stops on
  toggle.

## Testing

- **`pytest`** for `simplify_citations`, covering each rule and the
  must-not-touch statistics cases:
  - multi-author condensation, single author, `et al.`, multiple `;`-joined
    citations, lead-in words (`see`, `e.g.,`).
  - bare year parentheticals incl. page refs and year lists.
  - figure/table asides incl. `Fig.`, plurals, lead-ins; and a grammatical
    "Figure 3 shows…" that must remain unchanged.
  - statistics/non-citation parentheticals left verbatim.
  - whitespace/punctuation cleanup.
  - mixed-content parenthetical left untouched (conservative path).
- **Frontend**: the field pick is a one-liner with no regex risk; verify the
  toggle and re-render with Playwright against a server started with a temp
  `PAPER_READER_DATA_DIR`, per existing practice.

## Out of scope

- Numeric/bracketed citation styles (`[12]`).
- Equation/table-body/footnote detection in the extractor (separate future
  work).
- Per-paper (vs global) toggle granularity.

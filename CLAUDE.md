# CLAUDE.md

## Project

Paper Reader — a macOS app (and local web app) that reads academic PDFs aloud with
Coursera-style synchronized highlighting: the spoken word plus a ±2-word chunk is
highlighted in real time. Owner uses it to listen to marketing research papers.

## Commands

```bash
.venv/bin/uvicorn backend.main:app --port 8000   # dev server → http://127.0.0.1:8000
.venv/bin/python desktop.py                      # native window (pywebview/WKWebView)
.venv/bin/pytest backend/tests -q                # test suite
./build.sh                                       # tests + PyInstaller .app + PaperReader.dmg
```

## Architecture

```
desktop.py            app shell: embedded uvicorn on a free port + WKWebView window
backend/
  main.py             FastAPI: /upload, /papers CRUD, /voices, export endpoints, statics
  extractor.py        PDF → {title, blocks:[{type: heading|paragraph, text}]}
  store.py            paper library on disk (content-hash ids, dedupes re-uploads)
  export.py           M4A render via macOS `say` (background thread jobs)
  tests/              pytest; PDF fixtures generated on the fly with PyMuPDF
frontend/             vanilla JS single page: index.html / style.css / app.js
build.sh              packaging; assets/icon.icns is the app icon
```

Data lives in `~/Library/Application Support/Paper Reader/` (override with
`PAPER_READER_DATA_DIR`, which the test conftest sets to a temp dir).

## Implementation notes (hard-won, don't regress)

- **Extractor pipeline order matters**: gather (per-LINE font size/bold) → drop
  running headers/footers → column-aware reading order → compute body font size →
  split heading lines out of body blocks → build blocks (merge cross-page
  paragraphs, de-hyphenate). Headings are detected by font size, boldness,
  numbered patterns ("3.2 Measures"), and standard section names ("Abstract",
  "References", …) — section names catch headings that share a text block with
  the following paragraph.
- **TTS model**: one `SpeechSynthesisUtterance` per sentence segment
  (≤280 chars; engines silently die on long utterances). `onboundary` charIndex →
  pre-built word `<span>` offsets. Pause = cancel + remembered segment index
  (native `pause()` hangs with some voices). A `state.gen` token invalidates
  stale utterance callbacks after cancel/seek.
- **WKWebView quirks**: word boundaries DO fire (verified); `getVoices()` is
  lazily populated and `voiceschanged` is unreliable → the frontend retry-polls.
  WebKit exposes only a curated legacy voice list to web content (premium/
  enhanced voices hidden, anti-fingerprinting) — that's why desktop.py prefers
  a Chrome `--app=` window when Chrome is installed: Chrome sees all system
  voices. Lifetime in Chrome mode = heartbeat (`POST /ping` every 3s from
  app.js; server exits after 45s of silence). WKWebView is the fallback.
- **Skip references** exists in BOTH layers: frontend (segment `isRef`, skipped
  during auto-advance only) and backend export (`drop_references`). Keep the
  regexes in sync (`^(references|bibliography)\b`, case-insensitive).
- **Citation simplification** lives in ONE place: `backend/textclean.py`
  (`simplify_citations`). The backend attaches `text_simplified` to each
  paragraph block on read (`/upload`, `GET /papers/{id}`) — not stored, always
  recomputed, so rule tweaks apply retroactively. Frontend picks
  `text_simplified` vs `text` from the `pr-simplify` localStorage toggle
  (default on) and re-renders via `renderDocument()`. Export passes
  `simplify_citations` through to `export_text`. Author-year (APA) only;
  conservative — unmatched parentheticals are left untouched.
- Reading position / rate / voice prefs are in `localStorage`, not the backend.
- Paper ids are 12-hex content hashes; `store.py` validates with `_PID_RE`
  before touching paths (path-traversal guard).

## Testing & verification

- `pytest backend/tests` covers extractor heuristics, library API, and a real
  (small) `say` render. Test PDFs must wrap lines manually (`wrap()` helper) —
  PyMuPDF drops text inserted past the page edge.
- UI changes: verify with Playwright against a server started with
  `PAPER_READER_DATA_DIR=/tmp/...` so the real library isn't polluted.
  Entrance animations take ~1s — wait before screenshots.

## Distribution

- `./build.sh` produces an ad-hoc-signed `Paper Reader.app` in a `.dmg`;
  unsigned → friends must right-click → Open on first launch (documented in the
  DMG's READ ME and README.md). Proper fix = Apple Developer ID + notarization.
- GitHub: `gh` CLI, account `drkbhere`; .dmg ships as a Release asset, not in git.

## Owner context

Marketing professor (IIM Sirmaur); non-engineer but technical. Prefers working
software over ceremony — verify changes end-to-end and restart the live server
on port 8000 after backend edits (no --reload in use).

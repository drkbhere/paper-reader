# Paper Reader — Design

**Date:** 2026-06-10
**Status:** Approved

## Goal

Local web app MVP: upload an academic PDF, extract clean structured text, and listen to it
with Coursera-style synchronized highlighting — the spoken word plus a small surrounding
chunk is highlighted in real time.

## Decisions

- **Persistence:** single document, in-memory. No storage layer; the `/upload` response *is* the document.
- **Parsing:** smart cleanup — de-hyphenation, repeating header/footer removal, heading
  detection by font size, two-column reading order, paragraph merging across pages.
- **Controls:** play/pause, click-to-seek (click a sentence to start there), speed (0.75–2x),
  system voice picker, auto-scroll that follows the highlight.
- **TTS sync architecture:** sentence-level utterance queue (Approach A). One
  `SpeechSynthesisUtterance` per sentence, queued sequentially. `onboundary` `charIndex`
  is relative to the sentence and maps to pre-built word `<span>` offsets. Chosen over
  one-utterance-per-document/paragraph because Chrome's engine silently dies on long
  utterances, and sentence granularity makes seek/speed/voice changes clean.

## Architecture

```
backend/
  main.py        FastAPI app: POST /upload → {title, blocks}; serves frontend statics
  extractor.py   PyMuPDF pipeline: PDF bytes → [{type: heading|paragraph, text}]
  tests/test_extractor.py
frontend/
  index.html / style.css / app.js   Upload zone → reading view + sticky player bar
```

### Data flow

PDF → `POST /upload` → `{title, blocks: [{type, text}]}` → frontend splits paragraphs
into sentences, wraps every word in `<span data-w>` with char offsets → playback queue
speaks sentence-by-sentence → `onboundary` maps charIndex → word span → highlight
active word (`.active-word`) + ±2-word halo (`.active-chunk`); light highlight on the
whole current sentence.

### Error handling

- Non-PDF or corrupt upload → 400 with friendly banner.
- Scanned/image-only PDF (near-zero text) → 422, message that OCR is out of scope.
- Safari (no word-level `onboundary`) → sentence-level highlight still works as fallback.
- Pause implemented as cancel + remembered sentence index (Chrome's native pause/resume
  is unreliable with remote voices); resume restarts the current sentence.

### Testing

pytest for the extraction pipeline using PDFs generated on the fly with PyMuPDF
(de-hyphenation, header/footer stripping, heading detection). Speech sync verified
manually in the browser.

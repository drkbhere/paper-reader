# Paper Reader

A Mac app that turns academic PDFs into a listenable, Coursera-style reading
experience: open a paper, press play, and read along as the spoken word (plus a
small surrounding phrase) is highlighted in real time.

## Install (for friends)

1. Download `PaperReader.dmg` from the latest
   [GitHub Release](../../releases/latest) and open it.
2. Drag **Paper Reader** into **Applications**.
3. **First launch only:** right-click the app → **Open** → **Open**
   (the app isn't registered with Apple, so a plain double-click is blocked
   the first time).

For nicer narration, download a Premium system voice: System Settings →
Accessibility → Spoken Content → System Voice → Manage Voices… (Ava Premium
is a good pick), then choose it in the app's Voice menu.

## Build the app yourself

```bash
./build.sh   # runs tests, builds dist/Paper Reader.app, packages PaperReader.dmg
```

## Run from source (development)

```bash
cd ~/PaperReader

# one-time setup
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# browser mode
.venv/bin/uvicorn backend.main:app --port 8000   # then open http://127.0.0.1:8000

# or desktop-window mode
.venv/bin/python desktop.py
```

## Features

- **Smart PDF extraction** (PyMuPDF): two-column reading order, hyphenated
  line-break repair, running header/footer and page-number removal, heading
  detection (font size, boldness, and standard section names like "Abstract"),
  paragraph merging across page breaks.
- **Synchronized highlighting**: the Web Speech API's `onboundary` events map the
  spoken word onto pre-built `<span>`s — the current word plus two words on each
  side are highlighted as audio plays.
- **Click-to-seek**: click any sentence to start reading from there; arrow keys
  skip a sentence back/forward, space toggles play.
- **Library with resume**: uploaded papers persist (in
  `~/Library/Application Support/Paper Reader`) and reopen at the sentence
  where you stopped.
- **Contents sidebar** built from detected headings, with current-section
  tracking and click-to-jump.
- **Skip references**: the references section is dimmed and skipped during
  playback (toggleable).
- **Audio export**: render any paper to an M4A file via macOS `say`, with a
  voice picker — listen on your phone or in a podcast app.
- **Speed (0.75–2×) and voice** selection from your system's installed voices,
  remembered between sessions.
- **Auto-scroll** follows the highlight, and politely backs off for 4 seconds
  whenever you scroll manually.

## Architecture

```
desktop.py       app shell: embedded uvicorn + native WKWebView window
backend/
  main.py        FastAPI: POST /upload → {title, blocks}; serves the frontend
  extractor.py   PDF → [{type: heading|paragraph, text}] pipeline
  tests/         pytest suite (PDF fixtures generated on the fly)
frontend/
  index.html, style.css, app.js   vanilla JS single page
build.sh         PyInstaller .app + .dmg packaging
```

Playback uses one `SpeechSynthesisUtterance` per sentence, queued sequentially —
short utterances avoid Chrome's silent cutoff on long speech and make seeking,
speed, and voice changes clean. Pause is implemented as cancel + remembered
position because native `speechSynthesis.pause()` is unreliable in Chrome;
resuming restarts the current sentence.

## Notes & limitations

- Scanned (image-only) PDFs are rejected with a clear message — no OCR yet.
- The packaged app is ad-hoc signed, not notarized — hence the right-click → Open
  on first launch. An Apple Developer ID would remove that step.
- Voice quality depends on the system voices installed on the Mac.

## Tests

```bash
.venv/bin/pytest backend/tests
```

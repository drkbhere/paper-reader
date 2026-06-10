/* Paper Reader — upload, render, and speech-synchronized highlighting.
 *
 * Playback model: the document is split into "segments" (sentences, with very
 * long ones chunked at clause boundaries). One SpeechSynthesisUtterance per
 * segment, spoken sequentially. utterance.onboundary gives a charIndex relative
 * to the segment's text, which maps onto pre-built word <span>s.
 */

"use strict";

// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const uploadView = $("uploadView"), readerView = $("readerView");
const dropZone = $("dropZone"), fileInput = $("fileInput");
const uploadStatus = $("uploadStatus");
const docTitle = $("docTitle"), docBody = $("docBody");
const playerBar = $("playerBar"), playBtn = $("playBtn");
const iconPlay = $("iconPlay"), iconPause = $("iconPause");
const progressFill = $("progressFill"), progressLabel = $("progressLabel");
const rateSelect = $("rateSelect"), voiceSelect = $("voiceSelect");
const newDocBtn = $("newDocBtn");

// ---------- state ----------
const state = {
  segments: [],     // {text, el, words: [{start, end, el}]}
  segIdx: 0,
  status: "idle",   // idle | playing | paused
  rate: 1,
  voice: null,
  gen: 0,           // generation token: invalidates stale utterance callbacks
  lit: [],          // word spans currently highlighted
  liveSeg: null,
};

const CHUNK_RADIUS = 2;        // words on each side of the spoken word
const MAX_SEGMENT_CHARS = 280; // Chrome's engine dies on long utterances

// ---------- upload ----------

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (e) => {
  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); }
});
fileInput.addEventListener("change", () => {
  if (fileInput.files[0]) handleFile(fileInput.files[0]);
});

["dragenter", "dragover"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.add("dragover"); }));
["dragleave", "drop"].forEach((ev) =>
  dropZone.addEventListener(ev, (e) => { e.preventDefault(); dropZone.classList.remove("dragover"); }));
dropZone.addEventListener("drop", (e) => {
  const file = e.dataTransfer.files[0];
  if (file) handleFile(file);
});

async function handleFile(file) {
  if (!/\.pdf$/i.test(file.name) && file.type !== "application/pdf") {
    showUploadError("Please choose a PDF file.");
    return;
  }
  uploadStatus.innerHTML = `<span class="busy"><span class="spinner"></span>Reading “${escapeHtml(file.name)}”…</span>`;

  const form = new FormData();
  form.append("file", file);
  let res;
  try {
    res = await fetch("/upload", { method: "POST", body: form });
  } catch {
    showUploadError("Could not reach the server. Is it still running?");
    return;
  }
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    showUploadError(body?.detail || `Upload failed (HTTP ${res.status}).`);
    return;
  }
  enterReader(await res.json());
}

function showUploadError(msg) {
  uploadStatus.innerHTML = `<span class="error">${escapeHtml(msg)}</span>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- sentence segmentation ----------

const ABBREV = /(?:e\.g|i\.e|etc|et al|cf|vs|viz|ca|fig|figs|eq|eqs|sec|ref|refs|no|nos|vol|pp|p|ch|approx|dr|mr|mrs|ms|prof|st|jr|sr|inc|ltd|dept|univ|ed|eds|trans|repr|resp)\.$/i;

function splitSentences(text) {
  const out = [];
  let start = 0;
  const re = /[.!?]+["')\]’”]*\s+/g;
  let m;
  while ((m = re.exec(text)) !== null) {
    const end = m.index + m[0].length;
    const next = text[end];
    const prevWord = text.slice(start, m.index + 1).split(/\s+/).pop() || "";
    const isAbbrev = ABBREV.test(prevWord) || /^[A-Z]\.$/.test(prevWord);
    if (next && /[A-Z0-9"'“‘(\[]/.test(next) && !isAbbrev) {
      out.push(text.slice(start, end).trim());
      start = end;
    }
  }
  const rest = text.slice(start).trim();
  if (rest) out.push(rest);
  return out.flatMap(chunkLong);
}

// Split an over-long "sentence" at clause boundaries so no utterance exceeds
// MAX_SEGMENT_CHARS (Chrome silently stops speaking very long utterances).
function chunkLong(s) {
  if (s.length <= MAX_SEGMENT_CHARS) return [s];
  const window = s.slice(0, MAX_SEGMENT_CHARS - 40);
  let cut = Math.max(window.lastIndexOf("; "), window.lastIndexOf(", "));
  if (cut < 40) cut = window.lastIndexOf(" ");
  if (cut < 1) cut = MAX_SEGMENT_CHARS - 40;
  const head = s.slice(0, cut + 1).trim();
  const tail = s.slice(cut + 1).trim();
  return [head, ...chunkLong(tail)];
}

// ---------- rendering ----------

function enterReader(doc) {
  uploadView.classList.add("hidden");
  readerView.classList.remove("hidden");
  playerBar.classList.remove("hidden");
  newDocBtn.classList.remove("hidden");
  document.title = `${doc.title} — Paper Reader`;
  docTitle.textContent = doc.title;

  for (const block of doc.blocks) {
    const el = document.createElement(block.type === "heading" ? "h2" : "p");
    const sentences = block.type === "heading" ? [block.text] : splitSentences(block.text);
    sentences.forEach((sent, i) => {
      el.appendChild(buildSegment(sent));
      if (i < sentences.length - 1) el.appendChild(document.createTextNode(" "));
    });
    docBody.appendChild(el);
  }
  updateProgress();
}

// Build one segment: a <span class="seg"> whose words are individually wrapped
// so onboundary charIndexes can be mapped to DOM nodes.
function buildSegment(text) {
  const segEl = document.createElement("span");
  segEl.className = "seg";
  segEl.dataset.seg = state.segments.length;

  const words = [];
  const re = /\S+/g;
  let m, last = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segEl.appendChild(document.createTextNode(text.slice(last, m.index)));
    const w = document.createElement("span");
    w.className = "w";
    w.textContent = m[0];
    segEl.appendChild(w);
    words.push({ start: m.index, end: m.index + m[0].length, el: w });
    last = m.index + m[0].length;
  }
  if (last < text.length) segEl.appendChild(document.createTextNode(text.slice(last)));

  state.segments.push({ text, el: segEl, words });
  return segEl;
}

// Click a sentence to jump there.
docBody.addEventListener("click", (e) => {
  const segEl = e.target.closest(".seg");
  if (!segEl) return;
  seekTo(Number(segEl.dataset.seg));
});

// ---------- speech ----------

function speakFrom(i) {
  if (i >= state.segments.length) { finishPlayback(); return; }
  state.segIdx = i;
  const seg = state.segments[i];
  const gen = ++state.gen;

  const u = new SpeechSynthesisUtterance(seg.text);
  u.rate = state.rate;
  if (state.voice) u.voice = state.voice;

  u.onboundary = (e) => {
    if (gen !== state.gen) return;
    if (e.name && e.name !== "word") return; // skip sentence boundaries
    highlightWordAt(seg, e.charIndex);
  };
  u.onend = () => {
    if (gen !== state.gen || state.status !== "playing") return;
    speakFrom(i + 1);
  };
  u.onerror = (e) => {
    if (gen !== state.gen) return;
    if (e.error === "canceled" || e.error === "interrupted") return;
    if (state.status === "playing") speakFrom(i + 1); // skip a segment the engine rejects
  };

  setLiveSegment(seg);
  updateProgress();
  speechSynthesis.speak(u);
}

function play() {
  if (!state.segments.length) return;
  state.status = "playing";
  updatePlayBtn();
  speechSynthesis.cancel();
  // Chrome needs a beat after cancel() before speak() registers reliably.
  setTimeout(() => { if (state.status === "playing") speakFrom(state.segIdx); }, 60);
}

// Native speechSynthesis.pause() hangs with some Chrome voices, so pause is
// cancel + remembered position; resume restarts the current sentence.
function pause() {
  state.status = "paused";
  state.gen++;
  speechSynthesis.cancel();
  updatePlayBtn();
}

function finishPlayback() {
  state.status = "idle";
  state.segIdx = 0;
  clearWordHighlights();
  setLiveSegment(null);
  updatePlayBtn();
  updateProgress();
}

function seekTo(i) {
  state.gen++;
  speechSynthesis.cancel();
  state.segIdx = i;
  clearWordHighlights();
  setLiveSegment(state.segments[i]);
  updateProgress();
  if (state.status === "playing") {
    setTimeout(() => { if (state.status === "playing") speakFrom(i); }, 60);
  }
}

function restartCurrentSegment() {
  if (state.status !== "playing") return;
  state.gen++;
  speechSynthesis.cancel();
  setTimeout(() => { if (state.status === "playing") speakFrom(state.segIdx); }, 60);
}

playBtn.addEventListener("click", () => (state.status === "playing" ? pause() : play()));

document.addEventListener("keydown", (e) => {
  if (e.code !== "Space" || readerView.classList.contains("hidden")) return;
  if (e.target !== document.body) return;
  e.preventDefault();
  state.status === "playing" ? pause() : play();
});

function updatePlayBtn() {
  const playing = state.status === "playing";
  iconPlay.classList.toggle("hidden", playing);
  iconPause.classList.toggle("hidden", !playing);
  playBtn.setAttribute("aria-label", playing ? "Pause" : "Play");
}

// ---------- highlighting ----------

function setLiveSegment(seg) {
  if (state.liveSeg) state.liveSeg.el.classList.remove("live");
  state.liveSeg = seg;
  if (seg) {
    seg.el.classList.add("live");
    maybeAutoScroll(seg.el);
  }
}

function highlightWordAt(seg, charIndex) {
  const words = seg.words;
  if (!words.length) return;
  // last word starting at or before charIndex; if the boundary landed in the
  // whitespace after it, advance to the next word.
  let lo = 0, hi = words.length - 1, idx = 0;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (words[mid].start <= charIndex) { idx = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (charIndex >= words[idx].end && idx + 1 < words.length) idx += 1;

  clearWordHighlights();
  const from = Math.max(0, idx - CHUNK_RADIUS);
  const to = Math.min(words.length - 1, idx + CHUNK_RADIUS);
  for (let k = from; k <= to; k++) {
    words[k].el.classList.add(k === idx ? "now" : "chunk");
    state.lit.push(words[k].el);
  }
}

function clearWordHighlights() {
  for (const el of state.lit) el.classList.remove("now", "chunk");
  state.lit = [];
}

// ---------- auto-scroll ----------

let manualScrollAt = 0;
["wheel", "touchmove"].forEach((ev) =>
  window.addEventListener(ev, () => { manualScrollAt = Date.now(); }, { passive: true }));

function maybeAutoScroll(el) {
  if (Date.now() - manualScrollAt < 4000) return; // the reader is browsing; don't fight them
  const r = el.getBoundingClientRect();
  const vh = window.innerHeight;
  if (r.top < vh * 0.12 || r.bottom > vh * 0.78) {
    el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

// ---------- progress ----------

function updateProgress() {
  const total = state.segments.length;
  const done = state.status === "idle" && state.segIdx === 0 ? 0 : state.segIdx;
  progressFill.style.width = total ? `${(done / total) * 100}%` : "0%";
  progressLabel.textContent = total ? `sentence ${Math.min(done + 1, total)} of ${total}` : "";
}

// ---------- rate & voice ----------

rateSelect.addEventListener("change", () => {
  state.rate = Number(rateSelect.value);
  restartCurrentSegment();
});

let voiceRetries = 0;
function populateVoices() {
  const voices = speechSynthesis.getVoices();
  if (!voices.length) {
    // WKWebView/Safari populate the voice list lazily and don't always fire
    // voiceschanged, so poll for a few seconds.
    if (voiceRetries++ < 20) setTimeout(populateVoices, 250);
    return;
  }
  const english = voices.filter((v) => v.lang.toLowerCase().startsWith("en"));
  const list = (english.length ? english : voices)
    .sort((a, b) => Number(b.localService) - Number(a.localService));

  const previous = state.voice?.voiceURI;
  voiceSelect.innerHTML = "";
  list.forEach((v, i) => {
    const opt = document.createElement("option");
    opt.value = v.voiceURI;
    opt.textContent = `${v.name}${v.localService ? "" : " (online)"}`;
    voiceSelect.appendChild(opt);
    if (v.voiceURI === previous || (!previous && i === 0)) { state.voice = v; opt.selected = true; }
  });

  voiceSelect.onchange = () => {
    const all = speechSynthesis.getVoices();
    state.voice = all.find((v) => v.voiceURI === voiceSelect.value) || null;
    restartCurrentSegment();
  };
}

populateVoices();
speechSynthesis.addEventListener?.("voiceschanged", populateVoices);

// ---------- misc ----------

newDocBtn.addEventListener("click", () => {
  speechSynthesis.cancel();
  location.reload();
});

window.addEventListener("beforeunload", () => speechSynthesis.cancel());

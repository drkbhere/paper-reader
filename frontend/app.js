/* Paper Reader — library, render, speech-synchronized highlighting, export.
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
const librarySection = $("librarySection"), libraryList = $("libraryList");
const docTitle = $("docTitle"), docBody = $("docBody");
const tocList = $("tocList"), skipRefsToggle = $("skipRefsToggle");
const simplifyToggle = $("simplifyToggle");
const playerBar = $("playerBar"), playBtn = $("playBtn");
const iconPlay = $("iconPlay"), iconPause = $("iconPause");
const progressFill = $("progressFill"), progressLabel = $("progressLabel");
const rateSelect = $("rateSelect"), voiceSelect = $("voiceSelect");
const libraryBtn = $("libraryBtn"), exportBtn = $("exportBtn");
const exportModal = $("exportModal"), exportVoiceSelect = $("exportVoiceSelect");
const exportSkipRefs = $("exportSkipRefs"), exportStatus = $("exportStatus");
const exportSimplify = $("exportSimplify");
const exportStartBtn = $("exportStartBtn"), exportCancelBtn = $("exportCancelBtn");

// ---------- state ----------
const state = {
  paperId: null,
  segments: [],     // {text, el, words: [{start, end, el}], isRef}
  toc: [],          // {text, segIdx, el}
  segIdx: 0,
  status: "idle",   // idle | playing | paused
  rate: Number(localStorage.getItem("pr-rate")) || 1,
  voice: null,
  skipRefs: localStorage.getItem("pr-skiprefs") !== "0",
  simplifyCites: localStorage.getItem("pr-simplify") !== "0",
  blocks: [],       // raw blocks from the API, for re-render on toggle
  gen: 0,           // generation token: invalidates stale utterance callbacks
  lit: [],          // word spans currently highlighted
  liveSeg: null,
};

const CHUNK_RADIUS = 2;        // words on each side of the spoken word
const MAX_SEGMENT_CHARS = 280; // speech engines die on long utterances
const REFS_RE = /^(references|bibliography)\b/i;

const posKey = (id) => `pr-pos-${id}`;
const totalKey = (id) => `pr-total-${id}`;

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
  uploadStatus.innerHTML = "";
  enterReader(await res.json());
}

function showUploadError(msg) {
  uploadStatus.innerHTML = `<span class="error">${escapeHtml(msg)}</span>`;
}

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---------- library ----------

async function loadLibrary() {
  let papers = [];
  try {
    papers = await (await fetch("/papers")).json();
  } catch { return; }
  librarySection.classList.toggle("hidden", papers.length === 0);
  libraryList.innerHTML = "";
  for (const p of papers) {
    const pos = Number(localStorage.getItem(posKey(p.id))) || 0;
    const total = Number(localStorage.getItem(totalKey(p.id))) || 0;
    const pct = total ? Math.round((pos / total) * 100) : 0;

    const li = document.createElement("li");
    li.className = "library-item";

    const main = document.createElement("button");
    main.type = "button";
    main.className = "library-open";
    const t = document.createElement("span");
    t.className = "library-name";
    t.textContent = p.title;
    const sub = document.createElement("span");
    sub.className = "library-meta";
    sub.textContent = `${new Date(p.added * 1000).toLocaleDateString()}${total ? ` · ${pct}% read` : ""}`;
    const bar = document.createElement("span");
    bar.className = "library-bar";
    const fill = document.createElement("span");
    fill.className = "library-bar-fill";
    fill.style.width = `${pct}%`;
    bar.appendChild(fill);
    main.append(t, sub, bar);
    main.addEventListener("click", () => openPaper(p.id));

    const del = document.createElement("button");
    del.type = "button";
    del.className = "library-delete";
    del.setAttribute("aria-label", `Remove ${p.title}`);
    del.textContent = "✕";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/papers/${p.id}`, { method: "DELETE" });
      localStorage.removeItem(posKey(p.id));
      localStorage.removeItem(totalKey(p.id));
      loadLibrary();
    });

    li.append(main, del);
    libraryList.appendChild(li);
  }
}

async function openPaper(id) {
  const res = await fetch(`/papers/${id}`);
  if (!res.ok) { loadLibrary(); return; }
  enterReader(await res.json());
}

libraryBtn.addEventListener("click", () => {
  state.gen++;
  speechSynthesis.cancel();
  location.reload();
});

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
// MAX_SEGMENT_CHARS (engines silently stop on very long utterances).
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
  libraryBtn.classList.remove("hidden");
  exportBtn.classList.remove("hidden");
  document.title = `${doc.title} — Paper Reader`;
  docTitle.textContent = doc.title;
  state.paperId = doc.id;

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

// Build one segment: a <span class="seg"> whose words are individually wrapped
// so onboundary charIndexes can be mapped to DOM nodes.
function buildSegment(text, isRef, isHeading) {
  const segEl = document.createElement("span");
  segEl.className = "seg" + (isRef ? " ref" : "");
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

  if (isHeading) {
    state.toc.push({ text, segIdx: state.segments.length, el: null });
  }
  state.segments.push({ text, el: segEl, words, isRef });
  return segEl;
}

function buildToc() {
  tocList.innerHTML = "";
  for (const entry of state.toc) {
    const a = document.createElement("button");
    a.type = "button";
    a.className = "toc-entry";
    a.textContent = entry.text;
    a.addEventListener("click", () => seekTo(entry.segIdx));
    entry.el = a;
    tocList.appendChild(a);
  }
  document.getElementById("tocPanel").classList.toggle("hidden", state.toc.length === 0);
}

function updateTocCurrent() {
  let current = null;
  for (const entry of state.toc) {
    if (entry.segIdx <= state.segIdx) current = entry;
    entry.el.classList.remove("current");
  }
  current?.el.classList.add("current");
}

// Click a sentence to jump there.
docBody.addEventListener("click", (e) => {
  const segEl = e.target.closest(".seg");
  if (!segEl) return;
  seekTo(Number(segEl.dataset.seg));
});

// ---------- skip references ----------

skipRefsToggle.checked = state.skipRefs;
skipRefsToggle.addEventListener("change", () => {
  state.skipRefs = skipRefsToggle.checked;
  localStorage.setItem("pr-skiprefs", state.skipRefs ? "1" : "0");
  applySkipRefs();
});

simplifyToggle.checked = state.simplifyCites;
simplifyToggle.addEventListener("change", () => {
  state.simplifyCites = simplifyToggle.checked;
  localStorage.setItem("pr-simplify", state.simplifyCites ? "1" : "0");
  const keep = state.segIdx;
  state.gen++;
  speechSynthesis.cancel();
  if (state.status === "playing") { state.status = "paused"; updatePlayBtn(); }
  renderDocument();
  state.segIdx = Math.max(0, Math.min(keep, state.segments.length - 1));
  if (state.segments.length) setLiveSegment(state.segments[state.segIdx]);
  updateProgress();
});

function applySkipRefs() {
  docBody.classList.toggle("skip-refs", state.skipRefs);
}

function nextPlayable(i) {
  while (i < state.segments.length && state.skipRefs && state.segments[i].isRef) i++;
  return i;
}

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
    speakFrom(nextPlayable(i + 1));
  };
  u.onerror = (e) => {
    if (gen !== state.gen) return;
    if (e.error === "canceled" || e.error === "interrupted") return;
    if (state.status === "playing") speakFrom(nextPlayable(i + 1)); // skip a segment the engine rejects
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
  // WebKit/Chrome need a beat after cancel() before speak() registers reliably.
  setTimeout(() => { if (state.status === "playing") speakFrom(state.segIdx); }, 60);
}

// Native speechSynthesis.pause() hangs with some voices, so pause is
// cancel + remembered position; resume restarts the current sentence.
function pause() {
  state.status = "paused";
  state.gen++;
  speechSynthesis.cancel();
  updatePlayBtn();
  savePosition();
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
  if (readerView.classList.contains("hidden") || e.target !== document.body) return;
  if (e.code === "Space") {
    e.preventDefault();
    state.status === "playing" ? pause() : play();
  } else if (e.code === "ArrowRight") {
    e.preventDefault();
    seekTo(Math.min(nextPlayable(state.segIdx + 1), state.segments.length - 1));
  } else if (e.code === "ArrowLeft") {
    e.preventDefault();
    seekTo(Math.max(state.segIdx - 1, 0));
  }
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
  updateTocCurrent();
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

// ---------- progress & resume ----------

function savePosition() {
  if (state.paperId) localStorage.setItem(posKey(state.paperId), String(state.segIdx));
}

function updateProgress() {
  const total = state.segments.length;
  const done = state.status === "idle" && state.segIdx === 0 ? 0 : state.segIdx;
  progressFill.style.width = total ? `${(done / total) * 100}%` : "0%";
  progressLabel.textContent = total ? `sentence ${Math.min(done + 1, total)} of ${total}` : "";
  savePosition();
}

// ---------- rate & voice ----------

rateSelect.value = String(state.rate);
rateSelect.addEventListener("change", () => {
  state.rate = Number(rateSelect.value);
  localStorage.setItem("pr-rate", rateSelect.value);
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

  const previous = state.voice?.voiceURI || localStorage.getItem("pr-voice");
  voiceSelect.innerHTML = "";
  list.forEach((v) => {
    const opt = document.createElement("option");
    opt.value = v.voiceURI;
    opt.textContent = `${v.name}${v.localService ? "" : " (online)"}`;
    voiceSelect.appendChild(opt);
  });
  // Prefer higher-quality defaults: premium/enhanced if exposed, else Samantha.
  const fallback =
    list.find((v) => /premium|enhanced/i.test(v.name)) ||
    list.find((v) => /^samantha/i.test(v.name)) ||
    list.find((v) => v.lang.toLowerCase() === "en-us") ||
    list[0];
  state.voice = list.find((v) => v.voiceURI === previous) || fallback;
  voiceSelect.value = state.voice.voiceURI;

  voiceSelect.onchange = () => {
    const all = speechSynthesis.getVoices();
    state.voice = all.find((v) => v.voiceURI === voiceSelect.value) || null;
    localStorage.setItem("pr-voice", voiceSelect.value);
    restartCurrentSegment();
  };
}

populateVoices();
speechSynthesis.addEventListener?.("voiceschanged", populateVoices);

// ---------- export ----------

let exportVoicesLoaded = false;
let exportPolling = null;

exportBtn.addEventListener("click", async () => {
  exportModal.classList.remove("hidden");
  exportSkipRefs.checked = state.skipRefs;
  exportSimplify.checked = state.simplifyCites;
  exportStatus.textContent = "";
  if (!exportVoicesLoaded) {
    try {
      const voices = await (await fetch("/voices")).json();
      exportVoiceSelect.innerHTML = "";
      const preferred = localStorage.getItem("pr-export-voice");
      for (const v of voices) {
        const opt = document.createElement("option");
        opt.value = v.name;
        opt.textContent = `${v.name} (${v.lang.replace("_", "-")})`;
        if (v.name === preferred) opt.selected = true;
        exportVoiceSelect.appendChild(opt);
      }
      exportVoicesLoaded = voices.length > 0;
    } catch { /* leave the list empty; export uses the system default voice */ }
  }
});

exportCancelBtn.addEventListener("click", closeExportModal);
exportModal.addEventListener("click", (e) => { if (e.target === exportModal) closeExportModal(); });

function closeExportModal() {
  exportModal.classList.add("hidden");
  clearInterval(exportPolling);
  exportPolling = null;
}

exportStartBtn.addEventListener("click", async () => {
  if (!state.paperId) return;
  const voice = exportVoiceSelect.value || null;
  if (voice) localStorage.setItem("pr-export-voice", voice);
  exportStartBtn.disabled = true;
  exportStatus.textContent = "Rendering audio… this can take a few minutes for long papers.";
  try {
    await fetch(`/papers/${state.paperId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voice,
        skip_references: exportSkipRefs.checked,
        simplify_citations: exportSimplify.checked,
      }),
    });
  } catch {
    exportStatus.textContent = "Could not start the export.";
    exportStartBtn.disabled = false;
    return;
  }
  exportPolling = setInterval(async () => {
    let st;
    try {
      st = await (await fetch(`/papers/${state.paperId}/export/status`)).json();
    } catch { return; }
    if (st.status === "done") {
      clearInterval(exportPolling);
      exportPolling = null;
      exportStartBtn.disabled = false;
      exportStatus.innerHTML = "";
      const link = document.createElement("a");
      link.href = `/papers/${state.paperId}/audio`;
      link.className = "download-link";
      link.textContent = "Download M4A";
      exportStatus.append("Done — ", link);
    } else if (st.status === "error") {
      clearInterval(exportPolling);
      exportPolling = null;
      exportStartBtn.disabled = false;
      exportStatus.textContent = `Export failed: ${st.error || "unknown error"}`;
    }
  }, 1500);
});

// ---------- misc ----------

window.addEventListener("beforeunload", () => speechSynthesis.cancel());

// Heartbeat: in desktop (Chrome app window) mode the embedded server exits
// once these stop arriving. Harmless when running against a dev server.
setInterval(() => fetch("/ping", { method: "POST" }).catch(() => {}), 3000);

loadLibrary();

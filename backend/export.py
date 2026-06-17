"""M4A audio export using the macOS `say` command.

Renders run in a background thread; clients poll job status and then download
the finished file. `say` uses the same system voices as the in-app player.
"""

import re
import subprocess
import tempfile
import threading
from pathlib import Path

from .textclean import simplify_citations

REFERENCES_RE = re.compile(r"^(references|bibliography)\b", re.I)
HEADING_PAUSE = "[[slnc 700]]"    # `say` embedded-command: pause before a heading
PARAGRAPH_PAUSE = "[[slnc 300]]"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def list_voices() -> list[dict]:
    """English voices available to `say` (same pool the web player uses)."""
    try:
        raw = subprocess.run(["say", "-v", "?"], capture_output=True, text=True,
                             check=True, timeout=15).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    voices = []
    for line in raw.splitlines():
        m = re.match(r"^(.*?)\s{2,}([a-z]{2}_[A-Z]{2})\s", line)
        if m and m.group(2).startswith("en"):
            voices.append({"name": m.group(1).strip(), "lang": m.group(2)})
    return voices


def drop_references(blocks: list[dict]) -> list[dict]:
    """Remove the references/bibliography section (heading through the next
    heading, or to the end of the document)."""
    out, skipping = [], False
    for b in blocks:
        if b["type"] == "heading":
            skipping = bool(REFERENCES_RE.match(b["text"]))
            if skipping:
                continue
        if not skipping:
            out.append(b)
    return out


def drop_nonprose(blocks: list[dict]) -> list[dict]:
    """Remove blocks tagged as tables/equations/footnotes/captions."""
    return [b for b in blocks if "nonprose" not in b]


def export_text(title: str, blocks: list[dict], simplify: bool = True) -> str:
    parts = [title, PARAGRAPH_PAUSE]
    for b in blocks:
        if b["type"] == "heading":
            parts.append(f"{HEADING_PAUSE} {b['text']} {PARAGRAPH_PAUSE}")
        else:
            text = simplify_citations(b["text"]) if simplify else b["text"]
            parts.append(f"{text} {PARAGRAPH_PAUSE}")
    return "\n".join(parts)


def job_status(pid: str) -> dict:
    with _lock:
        return dict(_jobs.get(pid, {"status": "none"}))


def start_export(pid: str, title: str, blocks: list[dict], out_path: Path,
                 voice: str | None = None, skip_references: bool = True,
                 simplify_citations: bool = True, skip_nonprose: bool = True) -> bool:
    """Kick off a render; returns False if one is already running for this paper."""
    with _lock:
        if _jobs.get(pid, {}).get("status") == "running":
            return False
        _jobs[pid] = {"status": "running"}

    def render():
        try:
            content = blocks
            if skip_references:
                content = drop_references(content)
            if skip_nonprose:
                content = drop_nonprose(content)
            text = export_text(title, content, simplify=simplify_citations)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
                tf.write(text)
                src = tf.name
            cmd = ["say", "-o", str(out_path), "--file-format=m4af", "-f", src]
            if voice:
                cmd[1:1] = ["-v", voice]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            Path(src).unlink(missing_ok=True)
            if proc.returncode != 0 or not out_path.exists():
                raise RuntimeError(proc.stderr.strip() or "say failed")
            with _lock:
                _jobs[pid] = {"status": "done"}
        except Exception as exc:
            out_path.unlink(missing_ok=True)
            with _lock:
                _jobs[pid] = {"status": "error", "error": str(exc)}

    threading.Thread(target=render, daemon=True).start()
    return True

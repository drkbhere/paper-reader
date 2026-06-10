"""Persistent paper library: extracted JSON + original PDF on disk."""

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

_PID_RE = re.compile(r"^[0-9a-f]{12}$")  # ids are content hashes; reject path tricks


def default_data_dir() -> Path:
    override = os.environ.get("PAPER_READER_DATA_DIR")
    if override:
        return Path(override)
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Paper Reader"
    return Path.home() / ".paper-reader"


class PaperStore:
    def __init__(self, root: Path | None = None):
        self.root = Path(root) if root else default_data_dir()
        self.papers_dir = self.root / "papers"
        self.exports_dir = self.root / "exports"
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.exports_dir.mkdir(parents=True, exist_ok=True)

    def save(self, pdf_bytes: bytes, doc: dict) -> str:
        """Persist a paper; the id is a content hash, so re-uploading the same
        PDF replaces (re-extracts) rather than duplicates."""
        pid = hashlib.sha256(pdf_bytes).hexdigest()[:12]
        existing = self._meta_path(pid)
        added = time.time()
        if existing.exists():
            try:
                added = json.loads(existing.read_text())["added"]
            except (ValueError, KeyError):
                pass
        record = {"id": pid, "added": added, "title": doc["title"], "blocks": doc["blocks"]}
        existing.write_text(json.dumps(record, ensure_ascii=False))
        (self.papers_dir / f"{pid}.pdf").write_bytes(pdf_bytes)
        return pid

    def list(self) -> list[dict]:
        out = []
        for path in self.papers_dir.glob("*.json"):
            try:
                rec = json.loads(path.read_text())
                out.append({"id": rec["id"], "title": rec["title"], "added": rec["added"]})
            except (ValueError, KeyError):
                continue
        return sorted(out, key=lambda r: r["added"], reverse=True)

    def get(self, pid: str) -> dict | None:
        if not _PID_RE.match(pid):
            return None
        path = self._meta_path(pid)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def delete(self, pid: str) -> bool:
        if not _PID_RE.match(pid):
            return False
        found = False
        for path in (self._meta_path(pid),
                     self.papers_dir / f"{pid}.pdf",
                     self.export_path(pid)):
            if path.exists():
                path.unlink()
                found = True
        return found

    def export_path(self, pid: str) -> Path:
        return self.exports_dir / f"{pid}.m4a"

    def _meta_path(self, pid: str) -> Path:
        return self.papers_dir / f"{pid}.json"

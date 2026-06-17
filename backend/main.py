"""Paper Reader — FastAPI app: PDF upload, paper library, audio export, frontend."""

import re
import sys
import time
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import export, textclean
from .extractor import EmptyTextError, ExtractionError, extract_pdf
from .store import PaperStore

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="Paper Reader")
store = PaperStore()

# Heartbeat from the frontend; desktop.py watches this to know when the
# Chrome app window has been closed.
last_ping = 0.0


@app.post("/ping")
def ping():
    global last_ping
    last_ping = time.time()
    return {"ok": True}


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 50 MB.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(status_code=400, detail="That file doesn't look like a PDF.")
    try:
        doc = extract_pdf(data)
    except EmptyTextError:
        raise HTTPException(
            status_code=422,
            detail="No selectable text found — this looks like a scanned PDF. "
                   "OCR isn't supported yet; try a born-digital PDF.",
        )
    except ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    doc["id"] = store.save(data, doc)
    # text_simplified is computed on read, never persisted, so rule tweaks apply
    # retroactively to papers already in the library.
    doc["blocks"] = textclean.annotate_blocks(doc["blocks"])
    return doc


@app.get("/papers")
def list_papers():
    return store.list()


@app.get("/papers/{pid}")
def get_paper(pid: str):
    rec = store.get(pid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    rec["blocks"] = textclean.annotate_blocks(rec["blocks"])  # computed on read, not stored
    return rec


@app.delete("/papers/{pid}")
def delete_paper(pid: str):
    if not store.delete(pid):
        raise HTTPException(status_code=404, detail="Paper not found.")
    return {"deleted": pid}


@app.get("/voices")
def voices():
    return export.list_voices()


@app.post("/papers/{pid}/export")
def start_export(pid: str, voice: str | None = Body(None, embed=True),
                 skip_references: bool = Body(True, embed=True),
                 simplify_citations: bool = Body(True, embed=True),
                 skip_nonprose: bool = Body(True, embed=True)):
    rec = store.get(pid)
    if rec is None:
        raise HTTPException(status_code=404, detail="Paper not found.")
    if voice is not None and not re.match(r"^[\w .()'-]{1,60}$", voice):
        raise HTTPException(status_code=400, detail="Unknown voice.")
    started = export.start_export(pid, rec["title"], rec["blocks"],
                                  store.export_path(pid), voice, skip_references,
                                  simplify_citations, skip_nonprose)
    return {"status": "running" if started else "already-running"}


@app.get("/papers/{pid}/export/status")
def export_status(pid: str):
    return export.job_status(pid)


@app.get("/papers/{pid}/audio")
def download_audio(pid: str):
    rec = store.get(pid)
    path = store.export_path(pid)
    if rec is None or not path.exists():
        raise HTTPException(status_code=404, detail="No exported audio for this paper.")
    safe_name = re.sub(r"[^\w \-.]", "", rec["title"])[:80].strip() or "paper"
    return FileResponse(path, media_type="audio/mp4", filename=f"{safe_name}.m4a")


if getattr(sys, "frozen", False):  # running inside a PyInstaller bundle
    _frontend = Path(sys._MEIPASS) / "frontend"
else:
    _frontend = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")

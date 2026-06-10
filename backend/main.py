"""Paper Reader — FastAPI app: PDF upload + static frontend."""

import sys
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.staticfiles import StaticFiles

from .extractor import EmptyTextError, ExtractionError, extract_pdf

MAX_UPLOAD_BYTES = 50 * 1024 * 1024

app = FastAPI(title="Paper Reader")


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 50 MB.")
    if b"%PDF-" not in data[:1024]:
        raise HTTPException(status_code=400, detail="That file doesn't look like a PDF.")
    try:
        return extract_pdf(data)
    except EmptyTextError:
        raise HTTPException(
            status_code=422,
            detail="No selectable text found — this looks like a scanned PDF. "
                   "OCR isn't supported yet; try a born-digital PDF.",
        )
    except ExtractionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


if getattr(sys, "frozen", False):  # running inside a PyInstaller bundle
    _frontend = Path(sys._MEIPASS) / "frontend"
else:
    _frontend = Path(__file__).resolve().parent.parent / "frontend"
app.mount("/", StaticFiles(directory=_frontend, html=True), name="frontend")

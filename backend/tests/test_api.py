"""Library, export, and API endpoint tests."""

import shutil
import textwrap
import time

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.export import drop_references, export_text
from backend.main import app, store

client = TestClient(app)

HAS_SAY = shutil.which("say") is not None


def small_pdf(seed="alpha"):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), f"A Tiny Paper About {seed.title()}", fontsize=20)
    body = (f"This brief {seed} document exists to exercise the API. " * 6)
    page.insert_text((72, 200), "\n".join(textwrap.wrap(body, 80)), fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def upload(seed="alpha"):
    res = client.post("/upload", files={"file": (f"{seed}.pdf", small_pdf(seed), "application/pdf")})
    assert res.status_code == 200, res.text
    return res.json()


def test_upload_saves_to_library_and_roundtrips():
    doc = upload("beta")
    assert doc["id"]
    listing = client.get("/papers").json()
    assert any(p["id"] == doc["id"] for p in listing)
    fetched = client.get(f"/papers/{doc['id']}").json()
    assert fetched["title"] == doc["title"]
    assert fetched["blocks"] == doc["blocks"]


def test_reupload_same_pdf_keeps_one_entry():
    upload("gamma")
    upload("gamma")
    ids = [p["id"] for p in client.get("/papers").json()]
    assert len(ids) == len(set(ids))


def test_delete_paper():
    doc = upload("delta")
    assert client.delete(f"/papers/{doc['id']}").status_code == 200
    assert client.get(f"/papers/{doc['id']}").status_code == 404


def test_bad_ids_are_rejected():
    assert client.get("/papers/nope").status_code == 404
    assert store.get("../../etc/passwd") is None
    assert store.delete("..") is False


def test_drop_references_removes_section():
    blocks = [
        {"type": "paragraph", "text": "Body."},
        {"type": "heading", "text": "References"},
        {"type": "paragraph", "text": "Smith, J. (2020)."},
        {"type": "heading", "text": "Appendix A"},
        {"type": "paragraph", "text": "Extra material."},
    ]
    kept = drop_references(blocks)
    texts = [b["text"] for b in kept]
    assert "Smith, J. (2020)." not in texts
    assert "References" not in texts
    assert "Extra material." in texts


def test_export_text_includes_pauses():
    text = export_text("Title", [{"type": "heading", "text": "Intro"},
                                 {"type": "paragraph", "text": "Hello."}])
    assert "[[slnc 700]]" in text and "Hello." in text


@pytest.mark.skipif(not HAS_SAY, reason="macOS say not available")
def test_voices_listed():
    voices = client.get("/voices").json()
    assert isinstance(voices, list) and len(voices) > 0
    assert all("name" in v for v in voices)


def citation_pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A Paper With Citations", fontsize=20)
    body = ("Loyalty rose strongly over the studied period (Kumar, 2021) "
            "and the effect held across the whole sample (see Table 2). " * 4)
    page.insert_text((72, 200), "\n".join(textwrap.wrap(body, 80)), fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_get_paper_includes_simplified_text():
    pid = client.post("/upload",
                      files={"file": ("cite.pdf", citation_pdf(), "application/pdf")}).json()["id"]
    rec = client.get(f"/papers/{pid}").json()
    paras = [b for b in rec["blocks"] if b["type"] == "paragraph"]
    joined = " ".join(b["text_simplified"] for b in paras)
    assert "(Kumar)" in joined
    assert "(Kumar, 2021)" not in joined
    assert "Table 2" not in joined
    assert any("(Kumar, 2021)" in b["text"] for b in paras)  # original preserved


def test_upload_response_includes_simplified_text():
    doc = client.post("/upload",
                      files={"file": ("cite2.pdf", citation_pdf(), "application/pdf")}).json()
    paras = [b for b in doc["blocks"] if b["type"] == "paragraph"]
    assert any("(Kumar)" in b["text_simplified"] for b in paras)


@pytest.mark.skipif(not HAS_SAY, reason="macOS say not available")
def test_export_renders_audio():
    doc = upload("epsilon")
    res = client.post(f"/papers/{doc['id']}/export", json={"skip_references": True})
    assert res.json()["status"] in ("running", "already-running")

    deadline = time.time() + 90
    status = {}
    while time.time() < deadline:
        status = client.get(f"/papers/{doc['id']}/export/status").json()
        if status["status"] in ("done", "error"):
            break
        time.sleep(1)
    assert status["status"] == "done", status

    audio = client.get(f"/papers/{doc['id']}/audio")
    assert audio.status_code == 200
    assert audio.headers["content-type"] == "audio/mp4"
    assert len(audio.content) > 10_000


def test_export_text_simplifies_citations_when_enabled():
    blocks = [{"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."}]
    assert "(Kumar)" in export_text("T", blocks, simplify=True)
    assert "(Kumar, 2021)" not in export_text("T", blocks, simplify=True)


def test_export_text_keeps_citations_when_disabled():
    blocks = [{"type": "paragraph", "text": "Loyalty rose (Kumar, 2021)."}]
    assert "(Kumar, 2021)" in export_text("T", blocks, simplify=False)

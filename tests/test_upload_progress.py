import io
import json

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import main
from app.storage import Store


def _pdf(pages: int) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(200, 200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def test_upload_streams_page_progress_then_the_result(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    monkeypatch.setattr(main, "extract_text", lambda name, data, on_page=None: [on_page(i, 3) for i in (1, 2, 3)] and "Texte")
    client = TestClient(main.app)
    course = client.post("/api/courses", json={"name": "Endo"}).json()
    response = client.post(f"/api/courses/{course['id']}/files?stream=1", files={"files": ("manuel.pdf", _pdf(3))})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [e["page"] for e in events if e["type"] == "progress"] == [1, 2, 3]
    assert events[-1]["type"] == "done" and events[-1]["result"]["results"] == {"manuel.pdf": "added"}


def test_upload_stream_reports_an_unreadable_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    client = TestClient(main.app)
    course = client.post("/api/courses", json={"name": "Endo"}).json()
    response = client.post(f"/api/courses/{course['id']}/files?stream=1", files={"files": ("vide.pdf", _pdf(2))})
    last = json.loads(response.text.splitlines()[-1])
    assert last["type"] == "error" and "est un scan" in last["message"]


def test_a_scanned_pdf_goes_through_the_computer_ocr(monkeypatch):
    from app import extract, ocr

    seen = []

    def fake_ocr(data, on_page=None):
        on_page(1, 2)
        on_page(2, 2)
        return "[Page 1]\nLa pulpe dentaire\n\n[Page 2]\nLe canal radiculaire"

    monkeypatch.setattr(ocr, "ocr_pdf", fake_ocr)
    text = extract.extract_text("scan.pdf", _pdf(2), lambda *args: seen.append(args))
    assert "pulpe dentaire" in text
    assert seen[-2:] == [(1, 2, "ocr"), (2, 2, "ocr")]


def test_without_ocr_a_scan_has_no_text(monkeypatch):
    from app import extract, ocr

    def unavailable(data, on_page=None):
        raise ocr.OcrUnavailable("pas ici")

    monkeypatch.setattr(ocr, "ocr_pdf", unavailable)
    assert extract.extract_text("scan.pdf", _pdf(1)) == ""

"""
tests/test_parser.py

Run with: pytest tests/

Requires: pytest, httpx, and the sample contracts at the paths below.
Update CONTRACTS dict paths if your files are located elsewhere.
"""

import json
import os
import pytest

# Adjust if running tests from a different working directory
CONTRACT_2 = os.path.join(os.path.dirname(__file__), "..", "sample_input", "example_contract_2.docx")


# ---------------------------------------------------------------------------
# Parser unit tests (no server needed)
# ---------------------------------------------------------------------------

def _get_parser():
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
    from app.parser import parse_docx
    return parse_docx


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_parse_returns_chunks():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    assert doc.chunk_count > 0
    assert doc.total_tokens > 0
    assert doc.title != ""


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_chunks_have_required_fields():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    for chunk in doc.chunks:
        assert chunk.id, "chunk.id must be non-empty"
        assert isinstance(chunk.path, list) and len(chunk.path) > 0
        assert chunk.content.strip(), "chunk.content must be non-empty"
        assert chunk.token_count > 0
        assert all(t in {"paragraph", "list", "table"} for t in chunk.element_types)


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_manifest_has_no_content():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    manifest = doc.manifest()
    for entry in manifest["chunks"]:
        assert "content" not in entry, "Manifest entries must not include content"


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_get_chunk_by_id():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    first_id = doc.chunks[0].id
    chunk = doc.get_chunk(first_id)
    assert chunk is not None
    assert chunk.id == first_id


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_missing_chunk_returns_none():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    assert doc.get_chunk("does-not-exist-999") is None


@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_token_counts_are_positive():
    parse_docx = _get_parser()
    doc = parse_docx(CONTRACT_2)
    assert all(c.token_count > 0 for c in doc.chunks)
    assert doc.total_tokens == sum(c.token_count for c in doc.chunks)


# ---------------------------------------------------------------------------
# API integration tests (server must be running)
# ---------------------------------------------------------------------------

try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

BASE_URL = "http://localhost:8000"


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_upload_returns_manifest():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        with open(CONTRACT_2, "rb") as f:
            resp = client.post("/documents", files={"file": ("example_contract_2.docx", f)})
        assert resp.status_code == 201
        data = resp.json()
        assert "doc_id" in data
        assert "chunks" in data
        assert data["chunk_count"] > 0
        for entry in data["chunks"]:
            assert "content" not in entry


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
@pytest.mark.skipif(not os.path.exists(CONTRACT_2), reason="Sample contract not found")
def test_fetch_chunk_returns_content():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        with open(CONTRACT_2, "rb") as f:
            upload = client.post("/documents", files={"file": ("example_contract_2.docx", f)})
        doc_id = upload.json()["doc_id"]
        chunk_id = upload.json()["chunks"][0]["id"]

        resp = client.get(f"/documents/{doc_id}/chunks/{chunk_id}")
        assert resp.status_code == 200
        chunk = resp.json()
        assert "content" in chunk
        assert chunk["content"].strip()


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
def test_missing_document_returns_404():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        resp = client.get("/documents/nonexistent-id/manifest")
        assert resp.status_code == 404


@pytest.mark.skipif(not HAS_HTTPX, reason="httpx not installed")
def test_invalid_file_type_returns_400():
    with httpx.Client(base_url=BASE_URL, timeout=10) as client:
        resp = client.post(
            "/documents",
            files={"file": ("bad.txt", b"not a docx file", "text/plain")}
        )
        assert resp.status_code == 400

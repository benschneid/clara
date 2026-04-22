"""
main.py — FastAPI service for .docx contract parsing.

Endpoints
---------
POST /documents
    Upload a .docx file. Returns a manifest (lightweight index of all
    chunks — no content) plus doc_id for subsequent fetches.

GET  /documents/{doc_id}/manifest
    Re-fetch the manifest for an already-uploaded document.

GET  /documents/{doc_id}/chunks/{chunk_id}
    Fetch the full content of a single chunk by its ID.

GET  /documents/{doc_id}/chunks
    Fetch ALL chunks with content (use sparingly — for short docs or
    when the agent needs the full text in one shot).

DELETE /documents/{doc_id}
    Remove a document from the in-memory store.

Design note: storage is intentionally in-memory (a plain dict). This
keeps the service dependency-free and easy to run locally. See DESIGN.md
for what a production version would look like.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from app.parser import parse_docx, ParsedDocument

app = FastAPI(
    title="Contract Parser API",
    description="Transforms .docx contracts into agent-ready structured chunks.",
    version="1.0.0",
)

# In-memory store: doc_id → ParsedDocument
_store: dict[str, ParsedDocument] = {}


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

@app.post("/documents", summary="Upload and parse a .docx contract")
async def upload_document(file: UploadFile = File(...)):
    """
    Accept a .docx file, parse it into structured chunks, and return:

    - **doc_id**: UUID to use in subsequent requests
    - **title**: detected document title
    - **chunk_count**: total number of chunks
    - **total_tokens**: approximate token budget for the full document
    - **chunks**: lightweight manifest entries (no content)

    The agent should use this manifest to decide *which* chunks to fetch,
    rather than loading the full document at once.
    """
    if not file.filename or not file.filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Only .docx files are supported.")

    # Write upload to a temp file (UploadFile is a stream)
    tmp = tempfile.NamedTemporaryFile(suffix=".docx", delete=False)
    try:
        shutil.copyfileobj(file.file, tmp)
        tmp.close()
        parsed = parse_docx(tmp.path if hasattr(tmp, "path") else tmp.name)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Failed to parse document: {exc}")
    finally:
        os.unlink(tmp.name)

    _store[parsed.doc_id] = parsed
    return JSONResponse(content=parsed.manifest(), status_code=201)


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

@app.get("/documents/{doc_id}/manifest", summary="Get the chunk manifest for a document")
def get_manifest(doc_id: str):
    """
    Return the lightweight manifest (no chunk content) for a previously
    uploaded document. Useful when the agent needs to re-orient itself
    or a new agent session begins with a known doc_id.
    """
    doc = _get_or_404(doc_id)
    return doc.manifest()


# ---------------------------------------------------------------------------
# Single chunk fetch
# ---------------------------------------------------------------------------

@app.get("/documents/{doc_id}/chunks/{chunk_id}", summary="Fetch a single chunk with full content")
def get_chunk(doc_id: str, chunk_id: str):
    """
    Return the full content of a specific chunk. This is the primary way
    an agent should consume document content — fetch the manifest first,
    identify the relevant section(s), then pull only those chunks.

    Response includes:
    - **id**, **heading**, **path** (breadcrumb)
    - **content** (plain text / Markdown tables)
    - **element_types** (paragraph | list | table)
    - **token_count** (approximate)
    """
    doc = _get_or_404(doc_id)
    chunk = doc.get_chunk(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"Chunk '{chunk_id}' not found.")
    return chunk.to_dict()


# ---------------------------------------------------------------------------
# All chunks (convenience)
# ---------------------------------------------------------------------------

@app.get("/documents/{doc_id}/chunks", summary="Fetch all chunks with full content")
def get_all_chunks(doc_id: str, max_tokens: Optional[int] = None):
    """
    Return all chunks with full content. Optionally pass `max_tokens` to
    stop after a cumulative token budget is exceeded — useful for agents
    that want to stream-read up to their context limit.

    For long contracts this response can be large. Prefer selective chunk
    fetching via the manifest pattern.
    """
    doc = _get_or_404(doc_id)
    result = []
    budget = 0
    for chunk in doc.chunks:
        if max_tokens and budget + chunk.token_count > max_tokens:
            break
        result.append(chunk.to_dict())
        budget += chunk.token_count
    return {"doc_id": doc_id, "returned": len(result), "chunks": result}


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

@app.delete("/documents/{doc_id}", summary="Remove a document from the store")
def delete_document(doc_id: str):
    _get_or_404(doc_id)
    del _store[doc_id]
    return {"deleted": doc_id}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "documents_in_store": len(_store)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_or_404(doc_id: str) -> ParsedDocument:
    doc = _store.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Document '{doc_id}' not found.")
    return doc

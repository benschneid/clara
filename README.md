# Contract Parser API

A small FastAPI service that ingests `.docx` contracts and transforms them into
structured, agent-ready chunks — designed for downstream AI agents that review
business contracts.

## Quick Start

### Requirements

- Python 3.11+
- pip

### Install & Run

```bash
# Clone / unzip the repo, then:
cd contract-parser

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Start the server
uvicorn app.main:app --reload --port 8000
```

The API is now running at `http://localhost:8000`.  
Interactive docs: `http://localhost:8000/docs`

---

## API Usage

### 1. Upload a contract

```bash
curl -X POST http://localhost:8000/documents \
  -F "file=@example_contract_2.docx"
```

**Response** — a lightweight manifest (no content), safe to load regardless of document size:

```json
{
  "doc_id": "3f2a1b...",
  "title": "Bonterms Professional Services Agreement (Version 1.2)",
  "chunk_count": 15,
  "total_tokens": 9952,
  "chunks": [
    {
      "id": "definitions-1",
      "path": ["Bonterms Professional Services Agreement (Version 1.2)", "Definitions."],
      "heading": "Definitions.",
      "token_count": 1586,
      "element_types": ["list"]
    },
    {
      "id": "confidentiality-13",
      "path": ["Bonterms Professional Services Agreement (Version 1.2)", "Confidentiality."],
      "heading": "Confidentiality.",
      "token_count": 817,
      "element_types": ["list"]
    }
    // ...13 more entries
  ]
}
```

### 2. Fetch a specific chunk

```bash
curl http://localhost:8000/documents/3f2a1b.../chunks/confidentiality-13
```

**Response** — full content for that section only:

```json
{
  "id": "confidentiality-13",
  "path": ["Bonterms Professional Services Agreement (Version 1.2)", "Confidentiality."],
  "heading": "Confidentiality.",
  "content": "- Use and Protection. As recipient, each party will...\n\n- Permitted Disclosures...\n\n- Exceptions...",
  "element_types": ["list"],
  "token_count": 817
}
```

### 3. Other endpoints

```bash
# Re-fetch the manifest
GET /documents/{doc_id}/manifest

# All chunks with content (add ?max_tokens=4000 to cap by budget)
GET /documents/{doc_id}/chunks

# Delete from store
DELETE /documents/{doc_id}

# Health check
GET /health
```

---

## Project Structure

```
contract-parser/
├── app/
│   ├── __init__.py
│   ├── main.py        # FastAPI app, endpoints, in-memory store
│   └── parser.py      # Core .docx → chunks logic
├── sample_output/
│   ├── manifest_bonterms.json       # Manifest for example_contract_2.docx
│   ├── manifest_intertrust.json     # Manifest for example_contract_1.docx
│   ├── chunk_fetch_example.json     # Sample GET /chunks/{id} response
│   └── all_chunks_bonterms.json     # Full parsed output (all chunks + content)
├── requirements.txt
├── README.md
└── DESIGN.md
```

---

## Running Tests (optional)

```bash
pip install pytest httpx
pytest tests/
```

See `tests/test_parser.py` for unit tests on the parser logic.

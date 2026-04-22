# Design Note

## Chosen Approach: Hierarchical Section Chunking with Manifest + Fetch

### What the service does

The service accepts a `.docx` file and returns **two things**:

1. **A manifest** — a lightweight JSON index of every section in the document. Each entry contains a chunk ID, a breadcrumb path, a heading, an approximate token count, and the element types present (paragraph, list, table). Crucially, the manifest contains *no content*. It is small and constant-size regardless of how long the document is.

2. **A fetch endpoint** — `GET /documents/{doc_id}/chunks/{chunk_id}` returns the full text of a single section on demand.

The parser walks the document's block-level elements in order — paragraphs and tables — and groups them under headings. Headings are detected via Word paragraph styles (`Heading 1–6`, `Title`) with a numbered-section regex fallback (`1.  Foo`, `1.1 Bar`, `EXHIBIT A`) for legacy contracts that were converted from plain text and have no heading styles. Tables are converted to Markdown so they remain readable as plain text without any special rendering.

---

### Why this is well-suited for a downstream AI agent

**The core problem with naive approaches:**

- *Dump the full text*: Breaks immediately on any contract longer than the model's context window (~100–200K tokens for current models). Even within the window, flooding the context with irrelevant boilerplate degrades reasoning on the specific clauses that matter.

- *Fixed-size chunking*: Splitting every N tokens ignores document structure. A chunk that starts mid-sentence in Section 9.2 has no idea it is about Confidentiality. The agent loses the section context it needs to answer questions like "what are the exceptions to the confidentiality obligation?"

**Why manifest + fetch is better:**

Legal contracts are *hierarchically organised*. Lawyers navigate them by section number. An AI agent reviewing a contract should work the same way:

1. Load the manifest — learn the document's skeleton in a single, bounded call.
2. Identify the relevant sections from headings (e.g. "Confidentiality", "Indemnification", "License Fees").
3. Fetch only those chunks — staying well within the context window.

Each chunk includes a `path` (breadcrumb) field — e.g. `["Master Agreement", "9. Confidentiality", "9.3 Universal Information"]` — so even a chunk read in isolation carries enough context for the agent to understand where it sits in the document. This is essential when an agent's tool call returns a single chunk and the LLM needs to reason about it without seeing the rest.

The `token_count` on each manifest entry lets an agent plan its context budget before fetching — it can, for example, decide to fetch up to 5 sections totalling ≤4,000 tokens for a focused analysis pass.

---

### Key tradeoffs

**In-memory storage**

Documents are stored in a Python dict keyed by `doc_id`. This is fine for a demo and a short-lived review session. It means documents are lost on restart, there is no persistence, and the service cannot scale horizontally. A production version would use a database (see below).

*Why I made this call:* Eliminating an external database dependency keeps the service runnable with `uvicorn app.main:app` and no infrastructure. The scope of the assignment is the parsing and structuring logic; storage is a deliberate simplification.

**No vector embeddings**

Chunks are navigable by ID and heading text only — there is no semantic search. An agent must use the manifest headings to decide what to fetch. For a contract with clear, standard headings (which most are), this is sufficient. For a poorly structured document, the agent may need to fetch more chunks to find what it needs.

*Why I made this call:* Adding embeddings (e.g. via OpenAI's embeddings API or a local model) would meaningfully increase complexity and introduce an external API dependency. The structural signal in legal contracts — section numbering and standard headings — is strong enough that keyword navigation over the manifest gets an agent to the right clause most of the time.

**Heading detection heuristics**

The parser uses a two-pass strategy: Word heading styles first, then a numbered-section regex. This handles both well-formatted modern contracts (example_contract_2.docx) and legacy contracts (example_contract_1.docx, which uses a single `Body` style throughout). The regex heuristic is imperfect: it fires on table-of-contents entries if the document includes one at the top, producing some small spurious chunks before the main body begins.

**Token count approximation**

Token counts use a `len(text) / 4` approximation rather than running `tiktoken`. This avoids an extra dependency and is accurate to within ~10% for English legal prose. The counts are used for budgeting guidance, not hard limits, so this precision is sufficient.

**`element_types` in the manifest**

Each manifest entry includes the element types present in that chunk (paragraph, list, table). This is low cost to include — we track it during parsing anyway — and gives an agent a small amount of extra signal when deciding what to fetch without having to fetch the content first. For example, an agent asked to "summarize the payment terms table" could filter the manifest for chunks containing `"table"` before fetching. In practice, for most contract review questions the heading name alone is sufficient to make that decision, so this field is a nice-to-have rather than load-bearing to the core design.

---

### What the system does not yet handle well

- **Table-of-contents bleed**: Legacy contracts that embed a TOC as body text (contract 1) produce spurious small chunks from the TOC entries before the actual body sections begin. This was observed directly in `example_contract_1.docx` — the heading detector fires on lines like `"9.1 Confidentiality...34"` from the TOC, creating near-empty chunks before the real section body appears. A production parser would filter lines matching the `......  34` page-number pattern and skip the TOC region entirely.

- **Manually formatted lists**: Contracts that use `(a)`, `(b)`, `(c)` typed as plain paragraphs — rather than applying Word's list styles — are classified as `paragraph` in `element_types`, not `list`. The content is still present and readable, but the metadata is inaccurate.

- **No file size limit**: The upload endpoint accepts arbitrarily large files. Production would set a max upload size to prevent the service from being overwhelmed by a large file.

- **Temp file cleanup**: The current code deletes the temp file in a `finally` block which covers most failure cases, but a hard process crash mid-parse would leave the file on disk. Production would use Python's `tempfile` context manager for guaranteed cleanup.

- **Cross-references**: Contracts frequently say "as defined in Section 2.1" or "subject to Section 13.3(b)". The service does not resolve these — the agent must follow up with a targeted fetch. A graph of cross-references between chunks would improve agent reasoning on complex interdependencies.

- **Scanned / image-based PDFs**: The service only handles `.docx`. Many contracts arrive as PDFs (sometimes scanned). This would require OCR (e.g. AWS Textract, Google Document AI) before parsing.

- **Tracked changes / redlines**: `python-docx` surfaces the final accepted text by default. A contract under negotiation may have meaningful redline content that is silently dropped.

- **Style-free heading detection edge cases**: All-caps short lines are treated as potential headings in the fallback heuristic. In some documents this misfires on chapter epigraphs, definitions, or exhibit labels.

- **No authentication / multi-tenancy**: The current store is global. `doc_id` alone is not access control — while UUIDs are hard to guess, they are not a substitute for auth. Production would scope documents to an authenticated user so no cross-tenant document access is possible.

---

### What I would do with 10× the time

1. **Persistent storage with Postgres + pgvector.** Store chunks in a `chunks` table with a `tsvector` column for keyword search and a `vector` column for semantic similarity. This enables both structural navigation (manifest) and semantic retrieval ("find the clause about indemnification caps") in a single service, and survives restarts. The original uploaded `.docx` files would live in S3; the parsed chunks would live in Postgres — S3 for cheap durable storage of raw files, Postgres for everything the application actually queries against.

2. **Idempotent uploads via file hashing.** Hash the raw file bytes (SHA-256) on upload and check for an existing `doc_id` before parsing. Return the existing manifest immediately if the file has already been processed. Parsing is the expensive step; hashing is a few milliseconds regardless of file size.

3. **Authentication and document scoping.** `doc_id` alone is not access control. Production would require API key auth on every endpoint and scope documents to an authenticated user or organization.

4. **Embedding generation on ingest.** Run each chunk through an embedding model at upload time. Expose a `GET /documents/{doc_id}/search?q=...` endpoint that returns the top-k semantically similar chunks. This improves recall for agents working with non-standard or poorly structured contracts where heading names alone are insufficient to navigate.

5. **Cross-reference graph.** Parse section references (regex over chunk content) and build an adjacency list. Expose it on the manifest so an agent can navigate to referenced sections without knowing their IDs in advance.

6. **PDF support via pdfplumber / Textract.** Detect file type at upload, route to the appropriate parser. For scanned PDFs, integrate an OCR pipeline with layout analysis to recover heading structure.

7. **Streaming ingest.** For very large documents (100+ pages), return the manifest as a streaming response so the agent can begin working before parsing completes.

8. **Redline / tracked-change support.** Surface inserted and deleted runs in a structured way so an agent reviewing a negotiated contract can reason about what changed, not just what the final text is.

9. **Richer element metadata.** Tag defined terms (quoted phrases followed by "means" or "shall mean"), parties, dates, and monetary figures at the chunk level. This lets an agent quickly locate key terms without reading the definitions section in full.

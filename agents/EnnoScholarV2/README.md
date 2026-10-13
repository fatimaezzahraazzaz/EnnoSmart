# EnnoScholar V2 (local MVP)

Independent conversational assistant, alongside EnnoScholar V1. Open `/ennoscholar-v2`
or select **EnnoScholar V2** in the existing sidebar. All authenticated users can use V2.

## Configuration

Set `ENNOSCHOLAR_V2_MODEL` and `OPENAI_API_KEY` in the environment loaded by the backend
(`.env` at the repository root or `backend_api/.env`). The model has no automatic default
and no silent fallback. The official identifier for GPT-5.6 Sol is `gpt-5.6-sol`;
account access is separate and is not implied by access through the Codex app.
See this directory's `.env.example`. Missing configuration affects V2 requests only.
Existing production requirements already include the OpenAI SDK, HTTPX, BeautifulSoup,
PyMuPDF, FastAPI and Pydantic; no new Python production dependency is required.

## API

`POST /scholar-v2/chat`, with the existing Bearer authentication:

```json
{"session_id": null, "message": "Trouve des articles sur la génération de maillages 3D", "history": []}
```

Use the returned `session_id` for follow-ups. `history` is optional plain text for a new
session only; existing sessions use server history. Sources and reading evidence always
come from server tools, never from the supplied history. No project or diagnostic ID.
Response: `session_id`, `answer`, `citations`, `articles`, `tool_activity`, `partial`,
`reason`, `tool_calls`, `tool_counts`, `rounds`. A business limit returns HTTP 200,
`partial=true`, and collected articles/citations; hard tool budget reason is
`tool_budget_reached`. Actual upstream errors still return 502.
Unknown/expired sessions return 404; concurrent requests on a session return 409;
missing configuration returns 503. New conversation uses a new session ID.

## Tools and evidence

Native Responses `web_search`; custom `search_scientific`, `read_article`, `get_fulltext`,
`analyze_articles`, `get_articles`, `find_similar_articles`. The model chooses tools and their sequence.
`search_scientific(queries=[...], top_k=8)` accepts up to eight queries, runs three
providers in parallel (50 results per query/provider by default). Complete results
stay in backend RAM. A native-order round robin across query/source buckets returns
at most 8–12 compact candidates; it never grades hundreds of candidates with hidden
LLM calls. The conversational model judges those candidates, can search more precisely,
and chooses which notices/texts to inspect. No automatic global scientific score drives
chat or pagination. Legacy scientific_selection reranking helpers remain off this path.
Identity grouping preserves publication/preprint links without deleting backend records.
arXiv requests are serialized with a three-second interval.
get_articles(article_ids=[...]) returns details for 1–12 stored articles in one call.
`get_fulltext(article_ids=[...], candidate_urls=[])` and
`analyze_articles(article_ids=[...], user_context=null)` batch up to eight articles with
at most three workers. Full text results contain availability/provenance only, never
text or chunks. Analyses retain short, verified evidence. Unit dispatch remains
compatible with older calls; new model schemas encourage batches.
Scientific providers: Semantic Scholar, OpenAlex and arXiv, with normalized results,
DOI/title/arXiv deduplication and partial provider failure reporting. V2 imports no V1 client.
Full text retrieval uses public PDF/HTML, DOI lookups and optional URLs discovered via Web.
Protected, paywalled, encrypted, scanned-only or mismatched documents stay metadata/abstract only.
There is no challenge solving, authentication, Google Scholar scraping or paywall bypass.
Selected passages are bounded (24,000 characters) and retain page/section provenance.
`fulltext` means extracted text is available; `analysis_basis` is set only after analysis.
Structured analysis returns evidence IDs only. Python restores the exact stored
excerpt, page and section, rejects unknown IDs and blanks unsupported factual fields.
This validates provenance, not the correctness of every LLM inference.
Native URL citations preserve original text offsets and full URLs; consulted sources are
separate from actual citations. Opaque Responses reasoning stays inside the tool protocol;
it is never exposed in API responses or debug activity.

## Memory and limits

Backend RAM only, keyed by user and session, up to 64 sessions and 10,000 articles per session.
Idle TTL: one hour; oldest idle session evicted at capacity. Up to 60 recent messages with
80,000 characters of chat history before the model-facing token budget is applied.
The active selection contains at most 12 notices, deduplicated by article ID, DOI,
arXiv ID or normalized title. Searches update this selection. Model-facing tool
outputs retain references instead of repeated notices; the current selection is
rendered once in instructions. Explicit get_articles/get_fulltext/analyze_articles requests are pinned for the
turn before recent search candidates. If more than 12 articles are explicitly inspected,
the latest explicit batch takes priority. Fulltext status and verified analyses also
use this selection; long details are shortened under the existing token budget. Complete metadata/text remains in session.articles.
`ENNOSCHOLAR_V2_MAX_INPUT_TOKENS=48000` is a token-pressure target for compaction,
not a serialized-byte limit and not a reason to disable tools. Token estimates use
o200k when an optional, verified V2-owned vocabulary is available at
`.codex_tmp/ennoscholar-v2-context/o200k_base.tiktoken` and tiktoken is installed.
Otherwise an offline character-based estimate is used; no runtime download occurs.
Opaque encrypted_content is excluded from plaintext estimates. Actual Responses
input-token usage is logged and also triggers compaction when needed.
Native Responses compaction stays enabled at
`ENNOSCHOLAR_V2_COMPACT_THRESHOLD=12000`. Once an automatic compaction item is
received, the old prefix is dropped and its state plus recent protocol items is
retained. At token pressure a standalone native compact request is tried; its
canonical output is kept intact, then tools remain available. If compaction is
unavailable, reasonable continuation still enables server-side compaction.
`ENNOSCHOLAR_V2_MAX_CONTEXT_BYTES=4000000` is an extreme serialized-payload safety
guard only, applied after an attempt to compact. Ciphertext contributes to this
byte guard but is never mistaken for its rendered token cost.
Browser display state is RAM only and survives sidebar remounts, not page reloads.
No PostgreSQL migration, file persistence, Chroma, DOCX, diagnostic integration or fixed pipeline.
Restarting the backend clears sessions. Multiple backend processes do not share memory;
run this local MVP with one worker. Soft/hard budgets default to 16/32 visible tool
invocations, including native web searches. Internal HTTP/analysis requests do not
consume additional tool budget. Configure `ENNOSCHOLAR_V2_SOFT_TOOL_BUDGET`,
`ENNOSCHOLAR_V2_HARD_TOOL_BUDGET`, and `ENNOSCHOLAR_V2_MAX_ROUNDS` (default 10).
The soft budget adds a synthesis instruction while allowing essential tools. A hard,
round or time limit finalizes with existing results, without new tools;
if synthesis is unavailable a factual list of stored notices is returned instead.
Each turn logs per-tool counts, status, rounds and the final reason without user
messages, retrieved texts or credentials. Context logs distinguish estimated
tokens, actual reported input tokens, serialized bytes and active article count.
A final synthesis may add one model round;
upstream requests have timeouts and no automatic SDK retries.

## Native Windows launch (this local workspace)

In two PowerShell terminals in VS Code:

```powershell
# Backend; loads .env.native and starts local PostgreSQL/Redis if needed
cd D:\EnnoSmart
.\.venv\Scripts\python.exe .\.codex_tmp\native-install\run_backend_native.py
```

```powershell
# Frontend; uses the existing local production build
cd D:\EnnoSmart\frontend
npm.cmd run start -- --hostname 127.0.0.1 --port 3000
```

Open `http://127.0.0.1:3000`. Ctrl+C stops each process; backend sessions are RAM only.

## Validation

```powershell
python -m pytest tests/ennoscholar_v2 -q
cd frontend
npm.cmd run typecheck
npm.cmd run build
```

Tests mock OpenAI and scientific HTTP. They verify protocol dispatch, memory, legal retrieval,
evidence levels, citations, API isolation and frontend integration. They do not measure live
model tool selection or scientific ranking quality. No paid API call occurs in automatic tests.

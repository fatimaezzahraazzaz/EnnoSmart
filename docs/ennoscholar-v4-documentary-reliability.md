# EnnoScholar V4 — documentary reliability

Baseline: `8acbad748b3c88e41a36ee0b5ceb68c5b48935ca`.
The sanitized remote backup `727893afc32c288bd1609dafd675cf0418053f86`
was compared by Git blob identity before edits. Application/V4 files match;
only the previously authorized security changes and private inventory exclusions differ.

## 1. Full-text recovery

V4 retains all open document locations already returned by OpenAlex, recognizes
bioRxiv full-text containers and Roman-numbered arXiv sections, and reads JATS
bodies with external XML entities disabled. Downloads remain publicly routed,
DNS-pinned, bounded in bytes/time/attempts, with bounded transient retries and
per-host cooldowns. Genuine challenge pages and abstract-only pages stay rejected;
an incidental CAPTCHA script on an otherwise valid scientific article is not a challenge.

No search-provider implementation, scientific query, relevance strategy, reasoning
prompt, conversational agent, older agent, backend route or frontend was changed.

Validation: 131 V4/V3/V3.1 tests passed, including nine documentary regressions.
Protected scientific functions were compared structurally to the baseline.
Live metadata, download, extraction, durable reconstruction and `analyze_article(read)`
checks succeeded for the following public sources:

| Document | Extracted characters | Chunks | Full-text passages offered |
|---|---:|---:|---:|
| Cellpose bioRxiv v1 | 55,591 | 26 | 8 |
| GAInS arXiv v1 | 27,007 | 12 | 8 |
| Diffusion Copenhagen PDF | 21,044 | 6 | 8 |
| Diffusion Europe PMC JATS | 14,350 | 5 | 8 |

These checks do not label an extraction as a model-authored scientific analysis.
Downloaded documents and local execution state remain outside Git.

## 2. Document-version provenance

Retrieved bytes now identify a document version independently of the publication
notice: bioRxiv `2020.02.02.931238v1`, arXiv `2409.13988v1`, PMC `PMC11631529`,
and the Copenhagen PDF's own front-matter DOI were verified with the real sources.
Each version retains its bytes SHA-256, URL, explicit scientific identifiers and
extracted passages. Current-document data and archived versions survive reloading.

New snapshots bind version identifiers, source type, document hash, section/page
and exact text. Version/identifier/hash/quote/article tampering is rejected.
Snapshots are copied so later extraction cannot mutate already-used evidence.
Legacy snapshots remain readable; model projections explicitly say
`legacy_unverified`, without inferring a previously unverified edition.

Validation: 134 V4/V3/V3.1 tests, including the new version and compatibility checks;
real recovery/storage/reading checks repeated for all four source variants.

## 3. PDF pagination

A page request can obtain a complementary PDF after HTML has been extracted.
The HTML version and its immutable evidence remain separate and unpaginated.
PDF extraction distinguishes file position, declared PDF label and an explicit
printed numeral read in the page margin. Labels/offsets never fabricate printed
publication pagination. PDF extraction retains section transitions within pages.

A live Omnipose run fetched the 7,838,808-byte publisher PDF in 29.562 seconds,
kept both HTML and PDF versions, and recovered the overlap/self-contact passage
in Discussion: PDF position 9, printed page 1446, verified from margin text.
All previously generated HTML evidence remained valid without pagination.
The PDF contains 28 pages, including appendices with their own printed numerals.

The complementary-PDF download budget is explicit and bounded (90 seconds by
default), separate from the unchanged model/search/context budgets.
Validation: 136 V4/V3/V3.1 tests including independent page-coordinate and
HTML-to-PDF non-contamination regressions, plus the real Omnipose tool flow.

## 4. Scientific tables and numerical evidence

HTML/JATS tables preserve captions, explicit headers, literal rows, metrics,
units and experimental conditions. Rectangular unmerged structures permit exact
row evidence; merged/missing/ambiguous layouts retain raw cells and uncertainty.
PDF vector tables are extracted when PyMuPDF establishes their structure. A
conservative fallback handles horizontally ruled tables without vertical grids:
an explicit caption, ruled header, complete aligned baselines and a shared gap
between every pair of columns are required. Missing/clipped cells remain ambiguous.
Absent or ambiguous metadata is not invented. Table rows carry the document version,
section and available page coordinates. `analyze_article(table_number=...)` can
read an exact table without competing prose, with existing passage windows intact.

Real MMR arXiv `2304.02216v2` was downloaded, persisted, reconstructed and read:
Table 2 (AeBAD-S sample AUROC %) gives PatchCore 71.0 and MMR 84.7;
Table 3 (AeBAD-S pixel PRO %) gives PatchCore 87.8 and MMR 89.1.
All four results were offered as exact row evidence and passed source validation.
A document DOI different from the journal notice can be cited only through
verified already-shown document evidence; invented identifiers remain rejected.

Table column labels such as `Methods` no longer replace real PDF section headings;
Roman experiment/result headings and actual extended-data captions are distinguished
from figure references in prose. An initial page request retains accessible HTML
and its document metadata, without asserting a PDF page; a subsequent call may
recover a complementary PDF. A bioRxiv document's DOI stays separate from a related
journal DOI, and lookalike host names cannot establish archive identifiers.

Validation: 146 V4/V3/V3.1 tests; 302 V2 cases validated (one timing-sensitive
case was rerun with its three timeout scenarios after an unusually long run).
Scientific engine/query/selection/conversation/prompt code stays unchanged.

## Real API and model validation, 13–14 October 2026

The unmodified EnnoSmart backend routes and authentication were exercised on a
separate local SQLite database, with actual scientific services, public downloads
and the configured real `gpt-6.1-sol` model. Each resulting assistant answer was
checked in the persisted chat history. No keys, tokens, private database, downloads
or full local evaluation state are tracked by Git.

- Omnipose, identified from its title: accepted scientific answer, coverage marked
  sufficient, actual publisher PDF Discussion, file position 9 / printed page 1446.
- MMR: the first model run exposed a missing structured final row in an ungridded
  PDF table. After the geometric correction, all four requested rows were stored,
  shown to GPT and validated against their immutable sources. The final cached-PDF
  conversation directly cited both Tables 2 and 3, with sufficient coverage and no
  additional search or download: AUROC PatchCore 71.0 / MMR 84.7; PRO 87.8 / 89.1.
  A final new conversation, starting only from the title and without seeded papers,
  also succeeded with all four rows, their actual arXiv v2 identity and PDF position 10;
  all four immutable row proofs were actually shown to GPT and passed exact-source checks.
- Cellpose, GAInS and diffusion: real targeted body reading and a published
  comparison distinguish paper statements, model inferences and unresolved questions.
  That comparison is explicitly partial. GPT used bioRxiv v1 for Cellpose, arXiv v1
  for GAInS and the July 2023 bioRxiv preprint for diffusion. The Copenhagen PDF and
  PMC JATS were separately recovered/stored/read by real tools; this model conversation
  does not claim to have read those two diffusion versions.
- One earlier comparison failed with `APIConnectionError` and produced a partial
  fallback, then succeeded on conversational continuation. Slow PDF transfers and
  bioRxiv 429 responses can consume the unchanged exploration budget; downloaded
  text alone is never marked as a completed model analysis.

The real MMR continuation used 96,442 input / 2,715 output tokens in 71.188 seconds
(estimated USD 0.220034). Omnipose used 91,486 / 3,467 in 169.093 seconds (USD 0.267642).
The three-paper comparison continuation used 150,353 / 6,932 in 282.609 seconds
(USD 0.410026). Costs are application estimates; the interrupted request has
unreported usage, not a verified invoice total.
The final title-only MMR run used 95,236 / 3,324 in 125.953 seconds (USD 0.263712).
Across all nine real model turns (including unsuccessful/incomplete attempts and
continuations), reported usage totals 914,132 input / 28,650 output tokens and an
estimated USD 2.364764. A USD 0.4836 reservation belongs to the disconnected request
and is not reported measured usage or an additional confirmed charge.

A final GAInS continuation confirmed the explicit dataset-dependent parameter
limitation and distinguished the actual section III headings from old extraction
labels. It made no new search/download; archived proofs remain immutable, so an old
label is not silently rewritten. New PDF extraction recognizes the section correctly.

Limits: no OCR for scanned pages, no invented cell association for merged or
ambiguous tables, no inferred printed-page offset, no retroactive verification of
legacy document versions. Exact-source checks do not prove semantic entailment;
scientific interpretation remains the conversation model's responsibility.
PostgreSQL and the user's final manual UI campaign remain unexecuted here.

For the existing Windows worktree's single manual campaign, the local V4 UI uses
`http://127.0.0.1:3000/ennoscholar-v4`, with its backend on port 8002 and the existing
`ennoscholar_v4_test.db`. The temporary port-8003 evaluation database is separate.
Local launch/evaluation helpers and reports are kept under ignored `.codex_tmp`;
the tracked generic launcher remains `scripts/start_ennoscholar_v4.py`.

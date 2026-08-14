# EnnoSmart Research Upgrade V2

Main correction:
- only FULLTEXT_READY counts as usable scientific evidence;
- ABSTRACT_READY remains a discovery/triage status only.

Smart Stop:
- first batch: 10;
- count only full texts;
- if target 10 full texts is not reached, process +5;
- continue until target or max scan 25;
- preserve early coverage across verrous.

Chain:
known URLs -> direct PDF/HTML/XML -> legal MCP -> GROBID fallback -> FULLTEXT_READY

Acceleration:
- existing parallel providers remain;
- OpenCitations can use Redis cache;
- Smart Stop limits expensive extraction;
- extraction is parallel;
- legal MCP concurrency is separately bounded;
- Celery can execute the whole preflight outside FastAPI when Redis is running;
- synchronous fallback is automatic when Redis is unavailable.

CORE:
- existing CORE search remains;
- V2 also uses CORE first for DOI neighbours discovered by OpenCitations,
  before OpenAlex and Crossref.

CORE Recommender is deliberately not hardwired: the official recommender
product requires registration/dashboard/plugin installation, so this patch
does not invent an undocumented backend endpoint.

EnnoAmelioration:
- abstract-only papers remain visible as candidates;
- they are no longer marked scientific_evidence_eligible=true.

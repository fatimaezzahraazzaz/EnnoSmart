"""Authorised, read-only reuse of V4 science through its existing memory reader.

The lookup runs only when Sol explicitly uses a scientific retrieval tool.
Selections, plans, drafts and conversation histories are never imported.
"""
from agents.EnnoScholarV4.service import ScholarV4Service

class V4ReadOnlyMemory(ScholarV4Service):
    def __init__(self, v5_service, destination):
        # No executor/watchdog or V4 writes: reuse only article_memory/owned.
        self.v5_service, self.destination = v5_service, destination
        self.session_factory, self.config = v5_service.session_factory, v5_service.config

    def owned(self, db, owner, sid, *, lock=False):
        if lock:
            raise PermissionError("Cross-version scientific memory is read-only")
        if sid == self.destination:
            return self.v5_service.owned(db, owner, sid)
        return super().owned(db, owner, sid)

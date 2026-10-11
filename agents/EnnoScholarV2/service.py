from __future__ import annotations

import copy
import threading
import time
import uuid
from dataclasses import dataclass, field

from .agent import ScholarChatAgent, StoredArticle, TurnResult
from .config import ScholarConfig
from .models import ArticlePage, ChatRequest, ChatResponse, ScientificMemory
from .canonical import ArticleStore
from .context import compact_article


class SessionNotFound(LookupError):
    pass


class SessionBusy(RuntimeError):
    pass


@dataclass
class ScholarSession:
    owner_id: int
    updated_at: float = field(default_factory=time.monotonic)
    messages: list[dict] = field(default_factory=list)
    articles: dict[str, StoredArticle] = field(default_factory=dict)
    scientific_memory: ScientificMemory = field(default_factory=ScientificMemory)
    lock: threading.Lock = field(default_factory=threading.Lock)


class ScholarChatService:
    """RAM only. No PostgreSQL tables, project context or V1 storage writes."""

    def __init__(self, config: ScholarConfig | None = None, *, agent: ScholarChatAgent | None = None):
        self.config = config or ScholarConfig.from_env()
        self.agent = agent or ScholarChatAgent(self.config)
        self.sessions: dict[str, ScholarSession] = {}
        self._guard = threading.Lock()

    def _acquire_session(self, owner_id: int, request: ChatRequest) -> tuple[str, ScholarSession]:
        with self._guard:
            now = time.monotonic()
            expired = [sid for sid, s in self.sessions.items() if now - s.updated_at > self.config.session_ttl and not s.lock.locked()]
            for sid in expired:
                del self.sessions[sid]
            if request.session_id:
                session = self.sessions.get(request.session_id)
                if session is None or session.owner_id != owner_id:
                    raise SessionNotFound("Conversation expirée ou introuvable. Démarrez une nouvelle conversation.")
                sid = request.session_id
            else:
                if len(self.sessions) >= self.config.max_sessions:
                    idle = [(sid, s) for sid, s in self.sessions.items() if not s.lock.locked()]
                    if not idle:
                        raise SessionBusy("Toutes les conversations sont occupées. Réessayez plus tard.")
                    del self.sessions[min(idle, key=lambda pair: pair[1].updated_at)[0]]
                sid = uuid.uuid4().hex
                session = ScholarSession(owner_id=owner_id)
                # Only plain user/assistant text; never accept client tools or evidence.
                session.messages = [m.model_dump() for m in request.history]
                self.sessions[sid] = session
            if not session.lock.acquire(blocking=False):
                raise SessionBusy("Une recherche est déjà en cours dans cette conversation.")
            return sid, session

    def chat(self, owner_id: int, request: ChatRequest) -> ChatResponse:
        sid, session = self._acquire_session(owner_id, request)
        try:
            registry = copy.deepcopy(session.articles)
            history, used = [], 0
            for item in reversed(session.messages[-60:]):
                if used + len(item["content"]) > 80000:
                    break
                history.append(item)
                used += len(item["content"])
            memory = session.scientific_memory.model_copy(deep=True)
            if isinstance(self.agent, ScholarChatAgent):
                result = self.agent.run(request.message, list(reversed(history)), registry, memory=memory)
            else:
                result = self.agent.run(request.message, list(reversed(history)), registry)
            answer, citations, activity = result
            session.articles = registry
            if isinstance(result, TurnResult) and result.scientific_memory is not None:
                session.scientific_memory = result.scientific_memory
            store = ArticleStore(registry)
            session.messages.extend([{"role": "user", "content": request.message}, {"role": "assistant", "content": answer}])
            session.messages = session.messages[-60:]
            active_ids = result.active_article_ids if isinstance(result, TurnResult) else []
            preview_ids = active_ids or list(registry)[:12]
            return ChatResponse(session_id=sid, answer=answer, citations=citations,
                articles=[compact_article(registry[identifier].article) for identifier in preview_ids[:12] if identifier in registry],
                articles_total=len(store.works), canonical_work_count=len(store.works),
                record_count=store.version_record_count(), version_count=store.version_count(), active_article_ids=active_ids, tool_activity=activity,
                partial=result.partial if isinstance(result, TurnResult) else False,
                reason=result.reason if isinstance(result, TurnResult) else None,
                tool_calls=sum(result.tool_counts.values()) if isinstance(result, TurnResult) else 0,
                tool_counts=result.tool_counts if isinstance(result, TurnResult) else {},
                rounds=result.rounds if isinstance(result, TurnResult) else 0)
        except Exception:
            if not request.session_id:
                with self._guard:
                    self.sessions.pop(sid, None)
            raise
        finally:
            session.updated_at = time.monotonic()
            session.lock.release()

    def list_articles(self, owner_id: int, session_id: str, offset: int = 0, limit: int = 20) -> ArticlePage:
        if offset < 0 or not 1 <= limit <= 50:
            raise ValueError("Pagination invalide.")
        with self._guard:
            session = self.sessions.get(session_id)
            if session is None or session.owner_id != owner_id or (time.monotonic() - session.updated_at > self.config.session_ttl and not session.lock.locked()):
                raise SessionNotFound("Conversation expirée ou introuvable. Démarrez une nouvelle conversation.")
            # The committed registry is replaced atomically; in-flight tools work on
            # their own copy. Pagination reads a coherent snapshot without blocking chat.
            registry = session.articles
            store = ArticleStore(registry)
            records = [work.article for work in store.works]
            session.updated_at = time.monotonic()
        # Preserve registry order; independent ranking batches never drive chat or pagination.
        total = len(records)
        return ArticlePage(session_id=session_id, total=total, offset=offset, limit=limit,
            next_offset=offset + limit if offset + limit < total else None,
            articles=[article.model_copy(deep=True) for article in records[offset:offset + limit]],
            canonical_work_count=total, record_count=store.version_record_count(), version_count=store.version_count())


_service: ScholarChatService | None = None
_service_guard = threading.Lock()


def get_service() -> ScholarChatService:
    global _service
    with _service_guard:
        if _service is None:
            _service = ScholarChatService()
        return _service

"""V5-only durable tables registered on the existing SQLAlchemy metadata."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Boolean
from db.database import Base


class ScholarV4Session(Base):
    __tablename__ = "scholar_v5_sessions"
    id = Column(String(40), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    data = Column(JSON, nullable=False, default=dict)
    active_run_id = Column(String(40), nullable=True)
    lease_until = Column(DateTime, nullable=True)


class ScholarV4Run(Base):
    __tablename__ = "scholar_v5_runs"
    id = Column(String(40), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="queued")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    data = Column(JSON, nullable=False, default=dict)


class ScholarV4Message(Base):
    __tablename__ = "scholar_v5_messages"
    id = Column(String(40), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    role = Column(String(20), nullable=False)
    data = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ScholarV4Article(Base):
    __tablename__ = "scholar_v5_articles"
    # Session-scoped records keep private uploaded documents and interpretations isolated.
    id = Column(String(100), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    article_id = Column(String(60), nullable=False)
    doi = Column(String(500), index=True)
    access_status = Column(String(40), nullable=False)
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ScholarV4SessionArticle(Base):
    __tablename__ = "scholar_v5_session_articles"
    id = Column(String(100), ForeignKey("scholar_v5_articles.id"), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    selected = Column(Boolean, nullable=False, default=False)


class ScholarV4Analysis(Base):
    __tablename__ = "scholar_v5_analyses"
    id = Column(String(100), ForeignKey("scholar_v5_articles.id"), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    analysis_basis = Column(String(30), nullable=False)
    data = Column(JSON, nullable=False)


class ScholarV4Outline(Base):
    __tablename__ = "scholar_v5_outlines"
    id = Column(String(100), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v5_sessions.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    data = Column(JSON, nullable=False)


def sync_records(db, session):
    """Index the durable checkpoint in dedicated V5 tables in the same transaction."""
    data, sid = session.data, session.id
    for message in data.get("messages", []):
        db.merge(ScholarV4Message(id=message["id"], session_id=sid, role=message["role"], data=message))
    for aid, raw in data.get("articles", {}).items():
        key = sid + ":" + aid
        # PDF bytes live in one immutable content-addressed file, never duplicated here.
        db.merge(ScholarV4Article(id=key, session_id=sid, article_id=aid, doi=raw.get("doi") or None,
            access_status=raw.get("access_status", "metadata_only"), data=raw, updated_at=datetime.utcnow()))
        db.flush()
        db.merge(ScholarV4SessionArticle(id=key, session_id=sid, selected=raw.get("selected", False)))
        if raw.get("analysis"):
            db.merge(ScholarV4Analysis(id=key, session_id=sid, analysis_basis=raw.get("analysis_basis") or "metadata", data=raw["analysis"]))
    for outline in [*data.get("outline_versions", []), data.get("outline", {})]:
        if outline:
            version = outline.get("version", 0)
            db.merge(ScholarV4Outline(id=f"{sid}:{version}", session_id=sid, version=version, data=outline))


def lock_rows(db, session_id=None, run_id=None):
    """Serialise a read-modify-write of a session's or a run's JSON across threads and
    processes. A no-op UPDATE takes the row lock (PostgreSQL) or the database write lock
    (SQLite) before the row is read; it is held until commit or rollback. Always the
    session before its run, so two writers never wait for each other in opposite orders.
    Objects already loaded in this database session are expired: the next read is fresh."""
    from sqlalchemy import update
    if session_id is not None:
        db.execute(update(ScholarV4Session).where(ScholarV4Session.id == session_id)
            .values(lease_until=ScholarV4Session.lease_until).execution_options(synchronize_session=False))
    if run_id is not None:
        db.execute(update(ScholarV4Run).where(ScholarV4Run.id == run_id)
            .values(status=ScholarV4Run.status).execution_options(synchronize_session=False))
    db.expire_all()

"""V3-only durable tables registered on the existing SQLAlchemy metadata."""
from datetime import datetime
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Boolean
from db.database import Base


class ScholarV3Session(Base):
    __tablename__ = "scholar_v3_sessions"
    id = Column(String(40), primary_key=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(160), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    data = Column(JSON, nullable=False, default=dict)
    active_run_id = Column(String(40), nullable=True)
    lease_until = Column(DateTime, nullable=True)


class ScholarV3Run(Base):
    __tablename__ = "scholar_v3_runs"
    id = Column(String(40), primary_key=True)
    session_id = Column(String(40), ForeignKey("scholar_v3_sessions.id"), nullable=False, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="queued")
    cancel_requested = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    data = Column(JSON, nullable=False, default=dict)

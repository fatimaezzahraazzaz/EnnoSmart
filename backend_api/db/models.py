# -*- coding: utf-8 -*-
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    JSON,
    LargeBinary,
)
from sqlalchemy.orm import relationship, deferred

from db.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(255), nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(50), default="consultant", nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    projects = relationship(
        "Project",
        back_populates="consultant",
        cascade="all, delete-orphan",
    )
    profile = relationship(
        "UserProfile",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )
    preferences = relationship(
        "UserPreference",
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserProfile(Base):
    """Informations métier séparées du compte pour préserver les utilisateurs existants."""

    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    job_title = Column(String(255), nullable=True)
    company = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    bio = Column(Text, nullable=True)
    avatar_url = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="profile")


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    language = Column(String(10), default="fr", nullable=False)
    timezone = Column(String(100), default="Africa/Casablanca", nullable=False)
    theme = Column(String(20), default="system", nullable=False)
    compact_sidebar = Column(Boolean, default=False, nullable=False)
    email_notifications = Column(Boolean, default=True, nullable=False)
    project_notifications = Column(Boolean, default=True, nullable=False)
    weekly_summary = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="preferences")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    consultant_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    organisme = Column(String(255), nullable=False)
    project_name = Column(String(255), nullable=False)
    year = Column(String(20), nullable=False)
    domain_label = Column(String(255), nullable=True)

    status = Column(String(100), default="Créé", nullable=False)
    ai_folder = Column(String(500), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    consultant = relationship("User", back_populates="projects")
    documents = relationship(
        "Document",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    diagnostic_runs = relationship(
        "DiagnosticRun",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    scholar_runs = relationship(
        "ScholarRun",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    improvement_sessions = relationship(
        "ImprovementSession",
        back_populates="project",
        cascade="all, delete-orphan",
    )
    workflow = relationship(
        "ProjectWorkflow",
        back_populates="project",
        cascade="all, delete-orphan",
        uselist=False,
    )


class ProjectWorkflow(Base):
    __tablename__ = "project_workflows"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), unique=True, nullable=False, index=True)
    stage = Column(String(50), default="collecte", nullable=False)
    progress_percent = Column(Integer, default=10, nullable=False)
    priority = Column(String(20), default="normale", nullable=False)
    due_date = Column(DateTime, nullable=True)
    notes = Column(Text, nullable=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="workflow")


class PlatformSetting(Base):
    __tablename__ = "platform_settings"

    key = Column(String(100), primary_key=True)
    value_json = Column(JSON, nullable=False)
    description = Column(Text, nullable=True)
    updated_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(100), nullable=False, index=True)
    entity_type = Column(String(50), nullable=False)
    entity_id = Column(String(100), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    filename = Column(String(500), nullable=False)
    stored_filename = Column(String(500), nullable=False)

    # Ancien stockage disque : maintenant optionnel.
    # Pour les nouveaux uploads DB, on mettra un identifiant logique :
    # db://documents/<sha256>
    file_path = Column(Text, nullable=True)

    content_type = Column(String(255), nullable=True)
    file_size = Column(Integer, nullable=False)
    document_type = Column(String(100), nullable=True)
    upload_status = Column(String(100), default="importé", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Nouveau stockage complet en PostgreSQL.
    # deferred évite de charger les gros fichiers quand on liste les documents.
    file_data = deferred(Column(LargeBinary, nullable=True))
    file_sha256 = Column(String(64), nullable=True, index=True)
    storage_mode = Column(String(30), default="database", nullable=False)

    project = relationship("Project", back_populates="documents")


class DiagnosticRun(Base):
    __tablename__ = "diagnostic_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    status = Column(String(100), default="created", nullable=False)
    report_path = Column(Text, nullable=True)
    nlp_result_path = Column(Text, nullable=True)
    selected_verrous_path = Column(Text, nullable=True)
    raw_result_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="diagnostic_runs")
    verrous = relationship(
        "Verrou",
        back_populates="diagnostic_run",
        cascade="all, delete-orphan",
    )


class Verrou(Base):
    __tablename__ = "verrous"

    id = Column(Integer, primary_key=True, index=True)
    diagnostic_run_id = Column(
        Integer,
        ForeignKey("diagnostic_runs.id"),
        nullable=False,
        index=True,
    )

    title = Column(Text, nullable=False)
    tag_cir = Column(String(100), nullable=True)
    score = Column(Float, nullable=True)
    consultant_status = Column(String(100), default="en_attente", nullable=False)
    justification = Column(Text, nullable=True)
    source_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    diagnostic_run = relationship("DiagnosticRun", back_populates="verrous")
    articles = relationship("Article", back_populates="verrou")


class ScholarRun(Base):
    __tablename__ = "scholar_runs"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)

    status = Column(String(100), default="created", nullable=False)
    report_path = Column(Text, nullable=True)
    raw_result_json = Column(JSON, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="scholar_runs")
    articles = relationship(
        "Article",
        back_populates="scholar_run",
        cascade="all, delete-orphan",
    )


class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True)
    scholar_run_id = Column(
        Integer,
        ForeignKey("scholar_runs.id"),
        nullable=False,
        index=True,
    )
    verrou_id = Column(Integer, ForeignKey("verrous.id"), nullable=True, index=True)

    title = Column(Text, nullable=False)
    year = Column(Integer, nullable=True)
    source = Column(String(100), nullable=True)
    tag_article = Column(String(100), nullable=True)
    score = Column(Float, nullable=True)
    url = Column(Text, nullable=True)
    doi = Column(Text, nullable=True)
    consultant_status = Column(String(100), default="en_attente", nullable=False)
    source_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    scholar_run = relationship("ScholarRun", back_populates="articles")
    verrou = relationship("Verrou", back_populates="articles")


class ImprovementSession(Base):
    """Conversation EnnoAmelioration rattachée à un projet.

    Le texte publié n'est jamais remplacé implicitement : ``active_version_id``
    ne change qu'après une décision explicite du consultant.
    """

    __tablename__ = "improvement_sessions"

    id = Column(String(36), primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    title = Column(String(255), nullable=False, default="Nouvelle amélioration")
    state = Column(String(50), nullable=False, default="target_identification")
    target_scope = Column(String(50), nullable=False, default="section")
    target_section_id = Column(String(80), nullable=True)
    target_section_title = Column(Text, nullable=True)
    source_document_id = Column(Integer, ForeignKey("documents.id"), nullable=True, index=True)
    active_version_id = Column(String(36), nullable=True, index=True)
    context_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="improvement_sessions")
    source_document = relationship("Document")
    messages = relationship(
        "ImprovementMessage",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ImprovementMessage.created_at",
    )
    versions = relationship(
        "ImprovementVersion",
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ImprovementVersion.version_number",
        foreign_keys="ImprovementVersion.session_id",
    )


class ImprovementMessage(Base):
    __tablename__ = "improvement_messages"

    id = Column(String(36), primary_key=True, index=True)
    session_id = Column(
        String(36),
        ForeignKey("improvement_sessions.id"),
        nullable=False,
        index=True,
    )
    role = Column(String(30), nullable=False)
    content = Column(Text, nullable=False)
    intent = Column(String(80), nullable=True)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    session = relationship("ImprovementSession", back_populates="messages")


class ImprovementVersion(Base):
    __tablename__ = "improvement_versions"

    id = Column(String(36), primary_key=True, index=True)
    session_id = Column(
        String(36),
        ForeignKey("improvement_sessions.id"),
        nullable=False,
        index=True,
    )
    version_number = Column(Integer, nullable=False)
    status = Column(String(30), nullable=False, default="candidate")
    content = Column(Text, nullable=False)
    parent_version_id = Column(String(36), nullable=True, index=True)
    instruction = Column(Text, nullable=True)
    diff_json = Column(JSON, nullable=True)
    audit_json = Column(JSON, nullable=True)
    evidence_json = Column(JSON, nullable=True)
    generation_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    decided_at = Column(DateTime, nullable=True)

    session = relationship(
        "ImprovementSession",
        back_populates="versions",
        foreign_keys=[session_id],
    )

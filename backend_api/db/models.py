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
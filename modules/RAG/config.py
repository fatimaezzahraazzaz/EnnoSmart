# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(os.getenv("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
ORGANISMES_DIR = BASE_DIR / "storage" / "organismes"

# Modèle sémantique déjà utilisé par le RAG. Il reste configurable sans changer
# le code, par exemple ENNOSMART_EMBEDDING_MODEL=BAAI/bge-m3.
EMBEDDING_MODEL_NAME = os.getenv(
    "ENNOSMART_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

DEFAULT_TOP_K = int(os.getenv("ENNOSMART_RAG_TOP_K", "8"))
EMBEDDING_OFFLINE = os.getenv("ENNOSMART_EMBEDDING_OFFLINE", "1").strip() == "1"

# Consolidation sémantique des groupes de verrous.
# Les seuils sont volontairement prudents : un groupe non fusionné est conservé
# séparément ; il n'est jamais supprimé ni rattaché arbitrairement.
LOCK_CLUSTER_ENABLED = os.getenv("ENNOSMART_LOCK_CLUSTER_ENABLED", "1").strip() == "1"
LOCK_CLUSTER_MIN_SIMILARITY = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_MIN_SIMILARITY", "0.60")
)
LOCK_CLUSTER_STRONG_SIMILARITY = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_STRONG_SIMILARITY", "0.73")
)
LOCK_CLUSTER_COMPLETE_LINK_MIN = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_COMPLETE_LINK_MIN", "0.56")
)
LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_MEASUREMENT_ATTACH_MIN", "0.53")
)
LOCK_CLUSTER_MEASUREMENT_MARGIN = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_MEASUREMENT_MARGIN", "0.035")
)
LOCK_CLUSTER_MAX_EVIDENCE_PASSAGES = int(
    os.getenv("ENNOSMART_LOCK_CLUSTER_MAX_EVIDENCE_PASSAGES", "10")
)


# Classification générique du rôle des clusters.
# Aucun identifiant de projet, domaine, document ou cluster n'est utilisé.
LOCK_CLUSTER_ROLE_CLASSIFICATION_ENABLED = os.getenv(
    "ENNOSMART_LOCK_CLUSTER_ROLE_CLASSIFICATION_ENABLED", "1"
).strip() == "1"
LOCK_CLUSTER_RELATED_MIN_SIMILARITY = float(
    os.getenv("ENNOSMART_LOCK_CLUSTER_RELATED_MIN_SIMILARITY", "0.52")
)

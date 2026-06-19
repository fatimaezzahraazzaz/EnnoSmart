# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(os.getenv("ENNOSMART_BASE_DIR", r"C:\EnnoSmart"))
ORGANISMES_DIR = BASE_DIR / "storage" / "organismes"

EMBEDDING_MODEL_NAME = os.getenv(
    "ENNOSMART_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

DEFAULT_TOP_K = int(os.getenv("ENNOSMART_RAG_TOP_K", "8"))

# Si tu veux forcer offline après téléchargement du modèle :
# set ENNOSMART_EMBEDDING_OFFLINE=1
EMBEDDING_OFFLINE = os.getenv("ENNOSMART_EMBEDDING_OFFLINE", "0").strip() == "1"

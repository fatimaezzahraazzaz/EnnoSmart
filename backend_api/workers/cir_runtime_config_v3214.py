from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

POLICY_VERSION = "ennoamel_cir_transport_v3_21_4"

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_FILE = REPO_ROOT / ".ennosmart-cir-runtime.env"

DEFAULTS = {
    "CIR_BROKER_URL": "redis://127.0.0.1:6379/1",
    "CIR_RESULT_BACKEND": "redis://127.0.0.1:6379/2",
    "CIR_LANGGRAPH_REDIS_URL": "redis://127.0.0.1:6379/0",
    "CIR_STATUS_REDIS_URL": "redis://127.0.0.1:6379/3",
    "CIR_QUEUE": "ennosmart.cir",
}


def _read_file() -> dict[str, str]:
    values: dict[str, str] = {}
    if not CONFIG_FILE.exists():
        return values

    for raw in CONFIG_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw.strip()
        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


_FILE_VALUES = _read_file()


def _value(name: str) -> str:
    # Variable dédiée V3.21.4 > fichier dédié > défaut.
    # On NE lit volontairement plus ENNOSMART_CELERY_BROKER_URL :
    # l'ancien worker Scholar peut l'utiliser avec Redis /0.
    env_name = f"ENNOSMART_{name}"
    return str(
        os.getenv(env_name)
        or _FILE_VALUES.get(name)
        or DEFAULTS[name]
    ).strip()


CIR_BROKER_URL = _value("CIR_BROKER_URL")
CIR_RESULT_BACKEND = _value("CIR_RESULT_BACKEND")
CIR_LANGGRAPH_REDIS_URL = _value(
    "CIR_LANGGRAPH_REDIS_URL"
)
CIR_STATUS_REDIS_URL = _value(
    "CIR_STATUS_REDIS_URL"
)
CIR_QUEUE = _value("CIR_QUEUE")


def _redis_db(url: str) -> int:
    parsed = urlparse(url)
    raw = str(parsed.path or "").strip("/")
    if not raw:
        return 0
    try:
        return int(raw.split("/", 1)[0])
    except Exception:
        return -1


def validate() -> None:
    errors: list[str] = []

    if _redis_db(CIR_BROKER_URL) != 1:
        errors.append(
            "CIR_BROKER_URL doit pointer vers Redis DB /1 "
            f"(actuel={CIR_BROKER_URL})"
        )
    if _redis_db(CIR_RESULT_BACKEND) != 2:
        errors.append(
            "CIR_RESULT_BACKEND doit pointer vers Redis DB /2 "
            f"(actuel={CIR_RESULT_BACKEND})"
        )
    if _redis_db(CIR_LANGGRAPH_REDIS_URL) != 0:
        errors.append(
            "CIR_LANGGRAPH_REDIS_URL doit pointer vers Redis DB /0 "
            f"(actuel={CIR_LANGGRAPH_REDIS_URL})"
        )
    if _redis_db(CIR_STATUS_REDIS_URL) != 3:
        errors.append(
            "CIR_STATUS_REDIS_URL doit pointer vers Redis DB /3 "
            f"(actuel={CIR_STATUS_REDIS_URL})"
        )
    if CIR_QUEUE != "ennosmart.cir":
        errors.append(
            "CIR_QUEUE doit être ennosmart.cir "
            f"(actuel={CIR_QUEUE})"
        )

    if errors:
        raise RuntimeError(
            "Configuration CIR Redis invalide : "
            + " | ".join(errors)
        )


def public_config() -> dict[str, str]:
    return {
        "policy_version": POLICY_VERSION,
        "config_file": str(CONFIG_FILE),
        "broker": CIR_BROKER_URL,
        "result_backend": CIR_RESULT_BACKEND,
        "langgraph": CIR_LANGGRAPH_REDIS_URL,
        "status": CIR_STATUS_REDIS_URL,
        "queue": CIR_QUEUE,
    }


validate()

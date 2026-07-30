#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Diagnostic des sources EnnoScholar.
- Lit C:/EnnoSmart/.env
- Ne montre jamais les clés
- Teste les API une par une
- Affiche OK / DESACTIVEE / CLE_ABSENTE / ERREUR
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

try:
    import requests
except ImportError:
    print("ERREUR: le paquet 'requests' n'est pas installé.")
    print("Installe-le avec : pip install requests")
    raise SystemExit(2)

ENV_PATH = Path(os.environ.get("ENNOSMART_ENV_FILE", "C:/EnnoSmart/.env"))
TIMEOUT = 25


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"Fichier .env introuvable : {path}")

    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
            os.environ.setdefault(key, value)
    return values


def enabled(env: Dict[str, str], name: str, default: str = "1") -> bool:
    value = str(env.get(name, default)).strip().lower()
    return value not in {"0", "false", "no", "off", ""}


def secret(env: Dict[str, str], *names: str) -> str:
    for name in names:
        value = str(env.get(name, "")).strip()
        if value:
            return value
    return ""


def safe_request(
    method: str,
    url: str,
    *,
    params: Optional[Dict[str, object]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[bool, int, str, float]:
    started = time.perf_counter()
    try:
        response = requests.request(
            method,
            url,
            params=params,
            headers=headers,
            timeout=TIMEOUT,
            allow_redirects=True,
        )
        elapsed = time.perf_counter() - started

        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(
                    payload.get("message")
                    or payload.get("error")
                    or payload.get("errorMessage")
                    or payload.get("status")
                    or ""
                )
        except Exception:
            detail = response.text[:160].replace("\n", " ").strip()

        return response.ok, response.status_code, detail, elapsed
    except requests.RequestException as exc:
        elapsed = time.perf_counter() - started
        return False, 0, f"{type(exc).__name__}: {exc}", elapsed


def classify(ok: bool, status: int) -> str:
    if ok:
        return "OK"
    if status in {401, 403}:
        return "AUTH/CLE"
    if status == 429:
        return "LIMITE API"
    if status == 0:
        return "CONNEXION"
    return "ERREUR"


def print_result(name: str, state: str, status: str = "", elapsed: float = 0.0, detail: str = "") -> None:
    left = f"{name:<20}"
    timing = f"{elapsed:>6.2f}s" if elapsed else "      "
    http = f"HTTP {status}" if status else ""
    print(f"{left} | {state:<12} | {timing} | {http}")
    if detail:
        clean = detail.replace("\r", " ").replace("\n", " ")[:220]
        print(f"{'':<20}   {clean}")


def main() -> int:
    try:
        env = load_env_file(ENV_PATH)
    except Exception as exc:
        print(f"ERREUR: {exc}")
        return 2

    print("=" * 78)
    print("DIAGNOSTIC ENNOSCHOLAR — SOURCES EXTERNES")
    print(f".env : {ENV_PATH}")
    print("Aucune clé n'est affichée.")
    print("=" * 78)

    tests = []

    if enabled(env, "ENNOSCHOLAR_USE_SEMANTIC_SCHOLAR"):
        api_key = secret(env, "SEMANTIC_SCHOLAR_API_KEY")
        headers = {"x-api-key": api_key} if api_key else {}
        tests.append(("Semantic Scholar", "GET", "https://api.semanticscholar.org/graph/v1/paper/search", {"query": "radar", "limit": 1, "fields": "title,year"}, headers))
    else:
        print_result("Semantic Scholar", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_OPENALEX"):
        api_key = secret(env, "OPENALEX_API_KEY")
        params = {"search": "radar", "per_page": 1}
        if api_key:
            params["api_key"] = api_key
        mailto = secret(env, "OPENALEX_MAILTO")
        if mailto:
            params["mailto"] = mailto
        tests.append(("OpenAlex", "GET", "https://api.openalex.org/works", params, {}))
    else:
        print_result("OpenAlex", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_ARXIV"):
        tests.append(("arXiv", "GET", "https://export.arxiv.org/api/query", {"search_query": "all:radar", "start": 0, "max_results": 1}, {"User-Agent": "EnnoSmart/1.0"}))
    else:
        print_result("arXiv", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_CROSSREF"):
        params = {"query.title": "radar", "rows": 1}
        mailto = secret(env, "CROSSREF_MAILTO")
        if mailto:
            params["mailto"] = mailto
        tests.append(("Crossref", "GET", "https://api.crossref.org/works", params, {"User-Agent": "EnnoSmart/1.0"}))
    else:
        print_result("Crossref", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_HAL"):
        tests.append(("HAL", "GET", "https://api.archives-ouvertes.fr/search/", {"q": "radar", "rows": 1, "fl": "docid,title_s"}, {}))
    else:
        print_result("HAL", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_ZENODO"):
        tests.append(("Zenodo", "GET", "https://zenodo.org/api/records", {"q": "radar", "size": 1}, {}))
    else:
        print_result("Zenodo", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_CORE"):
        api_key = secret(env, "CORE_API_KEY")
        if not api_key:
            print_result("CORE", "CLE ABSENTE")
        else:
            tests.append(("CORE", "GET", "https://api.core.ac.uk/v3/search/works", {"q": "radar", "limit": 1}, {"Authorization": f"Bearer {api_key}"}))
    else:
        print_result("CORE", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_IEEE"):
        api_key = secret(env, "IEEE_XPLORE_API_KEY")
        if not api_key:
            print_result("IEEE Xplore", "CLE ABSENTE")
        else:
            tests.append(("IEEE Xplore", "GET", "https://ieeexploreapi.ieee.org/api/v1/search/articles", {"apikey": api_key, "querytext": "radar", "max_records": 1, "start_record": 1, "format": "json"}, {}))
    else:
        print_result("IEEE Xplore", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_EUROPE_PMC"):
        tests.append(("Europe PMC", "GET", "https://www.ebi.ac.uk/europepmc/webservices/rest/search", {"query": "radar", "pageSize": 1, "format": "json"}, {}))
    else:
        print_result("Europe PMC", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_GITHUB"):
        token = secret(env, "GITHUB_TOKEN")
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "EnnoSmart"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        tests.append(("GitHub", "GET", "https://api.github.com/search/repositories", {"q": "radar", "per_page": 1}, headers))
    else:
        print_result("GitHub", "DESACTIVEE")

    if enabled(env, "ENNOSCHOLAR_USE_HUGGINGFACE"):
        token = secret(env, "HF_TOKEN", "HUGGINGFACE_TOKEN")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        tests.append(("Hugging Face", "GET", "https://huggingface.co/api/models", {"search": "radar", "limit": 1}, headers))
    else:
        print_result("Hugging Face", "DESACTIVEE")

    ok_count = 0
    fail_count = 0

    for name, method, url, params, headers in tests:
        ok, status, detail, elapsed = safe_request(method, url, params=params, headers=headers)
        state = classify(ok, status)
        print_result(name, state, str(status) if status else "", elapsed, detail)
        if ok:
            ok_count += 1
        else:
            fail_count += 1

    print("=" * 78)
    print(f"Sources accessibles : {ok_count}")
    print(f"Sources en erreur   : {fail_count}")
    print("=" * 78)
    print("Interpretation :")
    print("- OK          : la source répond correctement.")
    print("- AUTH/CLE    : clé invalide, expirée ou permissions insuffisantes.")
    print("- LIMITE API  : quota dépassé ou trop de requêtes.")
    print("- CONNEXION   : DNS, proxy, pare-feu, SSL ou Internet.")
    print("- CLE ABSENTE : source activée mais clé obligatoire non renseignée.")
    print("- DESACTIVEE  : ENNOSCHOLAR_USE_<SOURCE>=0.")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

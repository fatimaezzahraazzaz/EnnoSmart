"""Opt-in real API/LLM replay; credentials come from the environment, never the report."""
import json
import os
from pathlib import Path
import sqlite3
import time
import sys
import re

import httpx

ROOT = Path(__file__).resolve().parents[3]
REPORT = Path(__file__).parent


def main():
    connection = sqlite3.connect((ROOT / "ennoscholar_v4_test.db").as_uri() + "?mode=ro", uri=True)
    old = json.loads(connection.execute("select data from scholar_v4_runs where id=?", ("780535990a0a464094654ec3ae9eff47",)).fetchone()[0])
    regression = json.loads(connection.execute("select data from scholar_v4_runs where id=?", ("3c831892586f47b3804b73dfbf22ccd0",)).fetchone()[0])
    prompt = old["request"]
    connection.close()
    with httpx.Client(base_url="http://127.0.0.1:8002", timeout=30) as api:
        login = api.post("/auth/login", json={"email": os.environ["V4_TEST_EMAIL"], "password": os.environ["V4_TEST_PASSWORD"]})
        login.raise_for_status()
        api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        if "--resume" in sys.argv:
            saved = json.loads((REPORT / "replay_running.json").read_text(encoding="utf-8"))
            sid, rid = saved["session_id"], saved["run_id"]
        else:
            session = api.post("/scholar-v4/sessions", json={"title": os.getenv("V4_TEST_TITLE", "Rejeu exact maillages 3D — finalisation corrigée")})
            session.raise_for_status()
            sid = session.json()["id"]
            started = api.post("/scholar-v4/chat", json={"session_id": sid, "message": prompt})
            started.raise_for_status()
            rid = started.json()["id"]
        print(json.dumps({"session_id": sid, "run_id": rid, "exact_prompt": True}), flush=True)
        (REPORT / "replay_running.json").write_text(json.dumps({"session_id": sid, "run_id": rid}), encoding="utf-8")
        last = None
        end = time.monotonic() + 660
        while time.monotonic() < end:
            try:
                result = api.get("/scholar-v4/runs/" + rid)
            except httpx.TransportError:
                print("Temporary local polling interruption; durable run is unchanged.", flush=True)
                time.sleep(5)
                continue
            result.raise_for_status()
            run = result.json()
            activity = run.get("activity", [])
            progress = (run["status"], len(activity))
            if progress != last:
                print(json.dumps({"status": run["status"], "events": len(activity), "latest": activity[-1] if activity else None}, ensure_ascii=True), flush=True)
                last = progress
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Real replay did not complete within its configured deadline")
        current = api.get("/scholar-v4/sessions/" + sid).json()
        articles = api.get(f"/scholar-v4/sessions/{sid}/articles?limit=100").json()
        memory = api.get(f"/scholar-v4/sessions/{sid}/memory").json()
        keys = ("status", "partial", "reason", "fallback", "exploration_stop_reason", "exploration_rounds", "finalization_rounds", "finalization_attempt_count", "finalization_refusals", "tool_counts", "usage", "duration_seconds", "primary_article_ids", "displayed_article_ids", "complementary_count", "discovered_sources_count", "library_counts", "coverage_sufficient", "publication_accepted", "research_status")
        report = {"exact_prompt": prompt, "session_id": sid, "run_id": rid, "backend": "real_authenticated_api_sqlite", "before": {key: old.get(key) for key in ("usage", "duration_seconds", "tool_counts", "reason", "partial")}, "after": {key: run.get(key) for key in keys}, "library_total": articles["total"], "message_persisted": any(message.get("run_id") == rid and message["content"] == run["answer"] for message in current["messages"]), "articles": [{key: a.get(key) for key in ("id", "title", "doi", "arxiv_id", "verified", "access_status", "publisher_access", "analysis_basis", "selected")} for a in articles["articles"]], "used_sources": run.get("citations", []), "stored_discovered_url_count": len(memory.get("web_sources", []))}
        report["primary_articles_at_publication"] = [{key: a.get(key) for key in ("id", "title", "doi", "arxiv_id", "verified", "access_status", "analysis_basis")} for a in run.get("articles", [])]
        report["library_snapshot"] = "Current owner-scoped library, which may include later follow-up analyses. Publication metadata above is the original run snapshot."
        report["regression"] = {key: regression.get(key) for key in ("usage", "duration_seconds", "tool_counts", "exploration_stop_reason", "primary_article_ids", "coverage_sufficient")}
        report["transmission_trace"] = [{key: e.get(key) for key in ("round", "phase", "input_chars", "offered_article_ids", "offered_abstract_ids", "abstract_deliveries")} for e in run.get("activity", []) if e["tool"] == "responses" and e["status"] == "running"]
        prefix = os.getenv("V4_REPORT_PREFIX", "observed_finalization")
        (REPORT / (prefix+"_replay_20261013.json")).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (REPORT / (prefix+"_answer_20261013.md")).write_text(re.sub(r" ?cite[^]*", "", run["answer"]), encoding="utf-8")
        print(json.dumps({"result": report["after"], "library_total": articles["total"], "message_persisted": report["message_persisted"]}, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()

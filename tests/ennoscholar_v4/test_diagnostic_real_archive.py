"""Real stored diagnostics, loaded into an isolated temporary SQLite only.

No client JSON is checked into Git. Set ENNOSCHOLAR_DIAGNOSTIC_ARCHIVE and
PG_RESTORE_EXE on another machine; tests skip if the local archive is absent.
pg_restore --file=- only decodes COPY records to stdout, never connects to a DB.
"""
import copy
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import User, Project, DiagnosticRun, Verrou
from agents.EnnoScholarV4 import diagnostic_link as dl
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV4.service import ScholarV4Service
from agents.EnnoScholarV4.models import SessionCreate
from agents.EnnoScholarV4.toolkit import ScholarTools
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.argumentation import argument_packet, bounded_argument_packet


def archive_rows(path, executable, table):
    output = subprocess.check_output([str(executable), "--data-only", "--table="+table, "--file=-", str(path)],
                                     stderr=subprocess.PIPE).decode("utf-8").replace("\r\n", "\n")
    match = re.search(r"COPY public\."+re.escape(table)+r" \((.*?)\) FROM stdin;\n(.*?)\n\\\.", output, re.S)
    if not match:
        return []
    columns = match[1].split(", ")
    def unescape(value):
        if value == r"\N":
            return None
        controls = {"b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r", "\\": "\\"}
        def replace(m):
            char = m[1]
            return controls[char] if char in controls else chr(int(char[1:], 16)) if char.startswith("x") else chr(int(char, 8))
        return re.sub(r"\\([btnvfr\\]|[0-7]{1,3}|x[0-9a-fA-F]{1,2})", replace, value)
    return [dict(zip(columns, map(unescape, line.split("\t")))) for line in match[2].split("\n") if line]


@pytest.fixture(scope="module")
def real_diagnostics(tmp_path_factory):
    archive = Path(os.getenv("ENNOSCHOLAR_DIAGNOSTIC_ARCHIVE", r"D:/EnnoSmart-backups/ovh-history-deploy-20261014/remote-backups/postgres-before-contextual.dump"))
    executable = Path(os.getenv("PG_RESTORE_EXE", r"D:/EnnoSmartTools/postgresql/pgsql/bin/pg_restore.exe"))
    if not archive.is_file() or not executable.is_file():
        pytest.skip("Local real diagnostic archive/decoder not available; no download or restore.")
    fingerprint = hashlib.sha256(archive.read_bytes()).hexdigest()
    runs = [r for r in archive_rows(archive, executable, "diagnostic_runs") if r["id"] in {"17", "48"}]
    assert {r["id"] for r in runs} == {"17", "48"}
    project_ids = {r["project_id"] for r in runs}
    projects = [p for p in archive_rows(archive, executable, "projects") if p["id"] in project_ids]
    locks = [v for v in archive_rows(archive, executable, "verrous") if v["diagnostic_run_id"] in {"17", "48"}]
    directory = tmp_path_factory.mktemp("real-diagnostic-isolated")
    engine = create_engine("sqlite:///"+str(directory/"isolated.sqlite"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for owner in {int(p["consultant_id"]) for p in projects}:
            db.add(User(id=owner, full_name="Offline audit", email=f"offline-{owner}@example.invalid", hashed_password="offline-only"))
        db.flush()
        for p in projects:
            db.add(Project(**{k: int(p[k]) if k in {"id", "consultant_id"} else p[k] for k in
                ("id", "consultant_id", "organisme", "project_name", "subproject_name", "year")}))
        db.flush()
        for r in runs:
            db.add(DiagnosticRun(id=int(r["id"]), project_id=int(r["project_id"]), status=r["status"],
                raw_result_json=json.loads(r["raw_result_json"]), created_at=datetime.fromisoformat(r["created_at"]),
                completed_at=datetime.fromisoformat(r["completed_at"]) if r["completed_at"] else None))
        db.flush()
        for v in locks:
            db.add(Verrou(id=int(v["id"]), diagnostic_run_id=int(v["diagnostic_run_id"]), title=v["title"],
                consultant_status=v["consultant_status"], tag_cir=v["tag_cir"], justification=v["justification"],
                source_json=json.loads(v["source_json"] or "{}")))
        db.commit()
    service = ScholarV4Service(factory, ScholarV4Config(api_key="offline-test", web_search_enabled=False))
    yield service, factory
    service.pool.shutdown(wait=True)
    engine.dispose()
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == fingerprint


def linked_state(fixture, rid):
    service, factory = fixture
    with factory() as db:
        run = db.get(DiagnosticRun, rid)
        owner = db.get(Project, run.project_id).consultant_id
        source = dl.source_snapshot(db, owner, run.project_id, rid)
    ids = [v["id"] for v in source["locks"] if v["decision"] == "garde"]
    # Diagnostic 48 has no human Garder; do not change that historical decision.
    state = {"mode": "diagnostic_linked", "diagnostic_link": dl.make_link(source, ids or [source["locks"][0]["id"]], allow_unkept=not ids),
             "messages": [{"id": "offline", "role": "user", "content": "Compare les solutions publiées au verrou actif et aux travaux documentés du projet."}], "articles": {}}
    dl.set_scope(state, [state["diagnostic_link"]["selected_lock_ids"][0]])
    return state


def test_real_17_human_decisions_old_proofs_and_collision_safety(real_diagnostics):
    state = linked_state(real_diagnostics, 17)
    source = state["diagnostic_link"]["snapshot"]
    assert [v["id"] for v in source["locks"] if v["decision"] == "garde"] == ["35", "36", "37"]
    assert source["locks"][0]["source"]["full_persisted_verrou"]["consultant_status"] == "en_attente"
    assert len(source["evidence"]) >= 65
    assert all(e["quote_verified"] is None and e["verification_status"] == "not_established" for e in source["evidence"].values())
    assert len({e["passage_id"] for e in source["evidence"].values()}) >= 65
    ids = Counter(e["original_evidence_id"] for e in source["evidence"].values())
    assert any(n > 1 for n in ids.values())
    assert len(dl.retrieve(state, "evidence", limit=100)["items"]) == 45
    assert not dl.project_facts_view(state)


def test_real_17_legacy_sections_are_available_for_comparison_not_fake_verified_results(real_diagnostics):
    state = linked_state(real_diagnostics, 17)
    general = dl.retrieve(state, "facts", limit=1000, scope="general")["items"]
    assert {"objective", "demarche", "parameter", "result"} <= {f["kind"] for f in general}
    assert all(not f["lock_ids"] for f in general)
    assert any("5 points" in f["statement"] for f in general if f["kind"] == "parameter")
    packet = dl.comparison_project_packet(state)
    assert packet["observations"]
    assert all(o["verification_status"] == "not_established" for o in packet["observations"])
    assert all(not o["project_fact_ids"] for o in packet["observations"])


def test_real_48_no_association_from_shared_proof_and_no_hidden_other_lock(real_diagnostics):
    state = linked_state(real_diagnostics, 48)
    source = state["diagnostic_link"]["snapshot"]
    assert all(v["decision"] == "en_attente" for v in source["locks"])
    assert source["locks"][0]["source"]["qualification_status"] == "VALID_LOCK"
    locks, active = dl.active_records(state)
    assert [v["id"] for v in locks] == ["140"]
    assert len(active) == 1 and active[0]["id"] == source["locks"][0]["source"]["entity_id"]
    assert "it_fast_24_p254" not in {f["id"] for f in active}
    assert not any(f["kind"] in {"method", "parameter", "result"} for f in active)
    general = dl.retrieve(state, "facts", limit=1000, scope="general")["items"]
    assert {"method", "parameter", "result"} <= {f["kind"] for f in general}
    assert all(f["scope"] == "project_context_not_linked_to_lock" and f["lock_ids"] == [] for f in general)
    assert len(source["evidence"]) == 124


@pytest.mark.parametrize("rid", [17, 48])
def test_real_project_side_survives_bounded_comparison_and_recovery(real_diagnostics, rid):
    state = linked_state(real_diagnostics, rid)
    service, _ = real_diagnostics
    tools = ScholarTools(state, None, service.config)
    before = copy.deepcopy(state["diagnostic_link"]["snapshot"])
    argument = bounded_argument_packet(argument_packet(state, tools, []), 14000)
    assert argument["project_context"]["observations"]
    assert len(json.dumps(argument, ensure_ascii=False)) <= 14000
    kinds = {r["category"] for r in argument["project_context"]["observations"]}
    assert "parameter" in kinds and "result" in kinds
    if rid == 48:
        assert "method" in kinds
        assert argument["project_facts"]
    assert len(json.dumps(argument["project_context"], ensure_ascii=False)) <= 4200
    for row in argument["project_context"]["observations"]:
        result = tools.execute("get_project_context", {"kind": "facts", "scope": "general" if not row["lock_ids"] else "active",
            "offset": 0, "limit": 1, "ids": [row["source_fact_id"]]}, 9999999999)
        assert result["items"][0]["statement"] == row["statement"]
    inputs = model_context(state, tools, service.config.max_context_chars)
    assert len(json.dumps(inputs, ensure_ascii=False)) <= service.config.max_context_chars
    assert state["diagnostic_link"]["snapshot"] == before


@pytest.mark.parametrize("rid", [17, 48])
def test_real_project_and_publication_evidence_reach_comparison_as_separate_sources(real_diagnostics, rid):
    from tests.ennoscholar_v4.test_context_memory import rich_corpus
    tools, ids = rich_corpus(2)
    linked = linked_state(real_diagnostics, rid)
    tools.state.update(mode="diagnostic_linked", diagnostic_link=linked["diagnostic_link"], messages=linked["messages"])
    tools.state["argumentation_focus"] = {"stage": "comparison", "article_ids": ids, "claim_offsets": {}}
    inputs = model_context(tools.state, tools, tools.config.max_context_chars)
    memory = json.loads(inputs[0]["content"].split(" : ", 1)[1])
    packet = memory["argumentation_focus"]["packet"]
    assert {p["id"] for p in packet["papers"]} == set(ids)
    assert packet["project_context"]["observations"]
    assert "result" in {r["category"] for r in packet["project_context"]["observations"]}
    diagnostic_ids = set(linked["diagnostic_link"]["snapshot"]["evidence"])
    assert not diagnostic_ids.intersection(tools.evidence)
    assert all(p["proofs"] for p in packet["papers"])

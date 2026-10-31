"""Test-only V4-session repository + idempotency receipts.

Production should adapt V4 service/session transactions, not deploy a second
science database. Checkpoints contain references; this store keeps ONE current
session, as V4 does. Old graph checkpoints cannot replay against newer science.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from agents.EnnoScholarV4.task_results import fingerprint


class RevisionConflict(RuntimeError):
    pass


class UncertainOperation(RuntimeError):
    pass


class Repository:
    def __init__(self, path, *, writable_root):
        self.path = Path(path).resolve()
        if not self.path.is_relative_to(Path(writable_root).resolve()) or self.path.name == "ennoscholar_v4_test.db":
            raise PermissionError("Only the explicitly isolated experiment store is writable")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS sessions(scope TEXT PRIMARY KEY, revision INTEGER NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, scope TEXT NOT NULL, revision INTEGER NOT NULL,
                kind TEXT NOT NULL, request TEXT NOT NULL, status TEXT NOT NULL, response TEXT);
            ''')

    @contextmanager
    def connection(self):
        with sqlite3.connect(self.path) as db:
            db.execute("PRAGMA busy_timeout=5000")
            yield db

    def seed(self, scope, state):
        if state.get("project_id") != scope.project_id:
            raise PermissionError("Project mismatch")
        with self.connection() as db:
            db.execute("INSERT INTO sessions VALUES(?,?,?)", (scope.key, 0, json.dumps(state, ensure_ascii=False)))

    def load(self, scope):
        with self.connection() as db:
            row = db.execute("SELECT revision,payload FROM sessions WHERE scope=?", (scope.key,)).fetchone()
        if row is None:
            raise PermissionError("Unknown or unauthorised scope")
        return row[0], json.loads(row[1])

    def operation(self, scope, opid):
        with self.connection() as db:
            row = db.execute("SELECT revision,kind,request,status,response FROM operations WHERE id=? AND scope=?",
                (opid, scope.key)).fetchone()
        return None if not row else {"revision": row[0], "kind": row[1], "request": json.loads(row[2]),
            "status": row[3], "response": json.loads(row[4]) if row[4] else None}

    def begin(self, scope, opid, revision, kind, request):
        prior = self.operation(scope, opid)
        if prior:
            if prior["request"] != request or prior["kind"] != kind:
                raise RevisionConflict("An operation ID cannot change its meaning")
            return prior
        with self.connection() as db:
            actual = db.execute("SELECT revision FROM sessions WHERE scope=?", (scope.key,)).fetchone()
            if not actual or actual[0] != revision:
                raise RevisionConflict("Science changed since the graph checkpoint")
            db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,NULL)",
                (opid, scope.key, revision, kind, json.dumps(request), "prepared"))
        return None

    def claim(self, scope, opid):
        with self.connection() as db:
            changed = db.execute("UPDATE operations SET status='running' WHERE id=? AND scope=? AND status='prepared'",
                (opid,scope.key))
            return changed.rowcount == 1

    def finish(self, scope, opid, revision, state, response):
        """Artifact and receipt commit together, before graph checkpointing.

        An interrupted graph can recover the completed receipt without repeating
        the side effect. Network effects without this receipt remain uncertain.
        """
        with self.connection() as db:
            changed = db.execute("UPDATE sessions SET payload=?,revision=revision+1 WHERE scope=? AND revision=?",
                (json.dumps(state, ensure_ascii=False), scope.key, revision))
            if changed.rowcount != 1:
                raise RevisionConflict("Concurrent or historical science revision")
            changed = db.execute("UPDATE operations SET status='complete',response=? WHERE id=? AND scope=? AND status='running'",
                (json.dumps(response, ensure_ascii=False), opid, scope.key))
            if changed.rowcount != 1:
                raise RevisionConflict("Receipt not owned by this operation")
        return revision+1

    def signature(self, scope):
        rev, state = self.load(scope)
        return rev, fingerprint(state)

    def completed_actions(self,scope):
        with self.connection() as db:
            ids=[r[0] for r in db.execute("SELECT id FROM operations WHERE scope=? AND kind='action' AND status='complete' ORDER BY rowid",(scope.key,))]
        return [self.operation(scope,i) for i in ids]

"""Opt-in continuation: measure additional discovery separately from the exact replay."""
import json
import os
from pathlib import Path
import time
import httpx

ROOT = Path(__file__).resolve().parent


def main():
    sid = json.loads((ROOT / "replay_running.json").read_text())["session_id"]
    prompt = "Continue la recherche pour compléter la famille tétraédrique adaptée à une géométrie mécanique imposée : retrouve notamment MeshingNet3D et 3DMeshNet, vérifie leurs références et explique leur différence avec les méthodes polycube. Conserve les publications déjà trouvées. Ne relis pas tous les textes : un tableau ciblé et les limites effectivement documentées suffisent."
    with httpx.Client(base_url="http://127.0.0.1:8002", timeout=30) as api:
        login = api.post("/auth/login", json={"email": os.environ["V4_TEST_EMAIL"], "password": os.environ["V4_TEST_PASSWORD"]})
        login.raise_for_status()
        api.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        before = api.get(f"/scholar-v4/sessions/{sid}/articles?limit=100").json()
        start = api.post("/scholar-v4/chat", json={"session_id": sid, "message": prompt})
        start.raise_for_status()
        rid = start.json()["id"]
        print(json.dumps({"continuation_run": rid}), flush=True)
        end, last = time.monotonic() + 660, None
        while time.monotonic() < end:
            try:
                response = api.get("/scholar-v4/runs/" + rid)
                response.raise_for_status()
                run = response.json()
            except httpx.TransportError:
                time.sleep(5)
                continue
            activity = run.get("activity", [])
            if len(activity) != last:
                print(json.dumps({"status": run["status"], "events": len(activity), "latest": activity[-1] if activity else None}), flush=True)
                last = len(activity)
            if run["status"] not in {"queued", "running"}:
                break
            time.sleep(5)
        else:
            raise RuntimeError("Continuation exceeded deadline")
        after = api.get(f"/scholar-v4/sessions/{sid}/articles?limit=100").json()
        preserved = {a["id"] for a in before["articles"]} <= {a["id"] for a in after["articles"]}
        targets = [a for a in after["articles"] if any(name in a["title"].casefold() for name in ("meshingnet3d", "3dmeshnet"))]
        report = {"session_id": sid, "run_id": rid, "prompt": prompt, "status": run["status"], "partial": run["partial"],
            "library_before": before["total"], "library_after": after["total"], "prior_articles_preserved": preserved,
            "usage": run.get("usage"), "duration_seconds": run.get("duration_seconds"), "tool_counts": run.get("tool_counts"),
            "reason": run.get("reason"), "finalization_refusals": run.get("finalization_refusals"),
            "retrieved_targets": [{k: a.get(k) for k in ("id", "title", "doi", "verified", "access_status", "analysis_basis")} for a in targets]}
        (ROOT / "observed_continuation_20261013.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (ROOT / "observed_continuation_answer_20261013.md").write_text(run["answer"], encoding="utf-8")
        print(json.dumps(report, ensure_ascii=True), flush=True)
        assert run["status"] == "completed" and preserved and len(targets) >= 2


if __name__ == "__main__":
    main()

from __future__ import annotations
import argparse, os, py_compile, shutil, stat, time
from pathlib import Path

def atomic_write(path: Path, text: str) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IWRITE | stat.S_IREAD)
    except Exception:
        pass
    tmp = path.with_name(path.name + ".v300.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    last = None
    for attempt in range(5):
        try:
            os.replace(str(tmp), str(path))
            return
        except OSError as exc:
            last = exc
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"Impossible de remplacer {path}: {last}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=r"C:\EnnoSmart")
    args = parser.parse_args()
    repo = Path(args.repo)
    pack = Path(__file__).resolve().parents[1]
    agent = repo / "agents" / "EnnoDiagnostic" / "ennodiagnostic_agent.py"
    target = repo / "agents" / "EnnoDiagnostic" / "historical_continuity_reconciler.py"
    source = pack / "agents" / "EnnoDiagnostic" / "historical_continuity_reconciler.py"
    if not agent.exists():
        raise FileNotFoundError(agent)
    agent_text = agent.read_text(encoding="utf-8-sig")
    if '"historical_continuity_report": historical_continuity_report' not in agent_text:
        raise RuntimeError("Integration V200 absente dans ennodiagnostic_agent.py. Installe d'abord V200.")
    backup_agent = agent.with_name(agent.name + ".before-historical-v300")
    backup_module = target.with_name(target.name + ".before-v300")
    if not backup_agent.exists():
        shutil.copy2(agent, backup_agent)
        print("[BACKUP]", backup_agent)
    if target.exists() and not backup_module.exists():
        shutil.copy2(target, backup_module)
        print("[BACKUP]", backup_module)
    shutil.copy2(source, target)
    print("[OK] module V300", target)
    updated = (
        agent_text
        .replace("historical_continuity_reconciler_v200", "historical_continuity_reconciler_v300")
        .replace("ennodiagnostic_v200_historical_continuity_reconciliation", "ennodiagnostic_v300_historical_family_reconciliation")
        .replace("V200_HISTORICAL_RECONCILIATION", "V300_HISTORICAL_RECONCILIATION")
        .replace("EnnoDiagnostic V200 terminé", "EnnoDiagnostic V300 terminé")
    )
    atomic_write(agent, updated)
    py_compile.compile(str(agent), doraise=True)
    py_compile.compile(str(target), doraise=True)
    print("[OK] compilation")
    print("V300 INSTALLEE")

if __name__ == "__main__":
    main()

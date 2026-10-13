"""Run real V4 acceptance turns; no fake search and no silent model substitution."""
import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--state", type=Path, default=ROOT / "backend_api/storage/scholar_v4/validation_state.json")
    parser.add_argument("--output", type=Path, default=ROOT / "backend_api/storage/scholar_v4/validation_result.json")
    parser.add_argument("--message", default="Je travaille sur la génération automatique de maillages volumiques 3D à l'aide de modèles entraînés pour la mécanique. Recherche les articles scientifiques les plus pertinents, y compris payants. Explique les contributions des meilleurs travaux, leurs limites et indique ce que tu as réellement pu consulter.")
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    from agents.EnnoScholarV4.config import ScholarV4Config
    from agents.EnnoScholarV4.agent import ScholarChatAgent
    config = ScholarV4Config.from_env()
    config.validate()
    if args.check_config:
        from openai import OpenAI
        client = OpenAI(api_key=config.api_key, timeout=25, max_retries=0)
        print("Model access:", client.models.retrieve(config.model).id)
        response = client.responses.create(model=config.model, reasoning={"effort": config.reasoning_effort},
            input="Use the function check with ok=true.", tools=[{"type": "function", "name": "check", "strict": True,
            "parameters": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}}],
            tool_choice={"type": "function", "name": "check"}, max_output_tokens=512, store=False)
        print("Function calling:", any(item.type == "function_call" for item in response.output))
        return
    state = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {"messages": [], "articles": {}, "project_id": None, "selected_verrous": []}
    state["messages"].append({"id": uuid.uuid4().hex, "role": "user", "content": args.message})
    def emit(event):
        # Progress contains neither prompts, query strings nor downloaded documents.
        print(event["tool"], event["status"], event.get("action", ""), "round="+str(event.get("round", "")), flush=True)
    result = ScholarChatAgent(config).run(state, emit=emit)
    state["messages"].append({"id": uuid.uuid4().hex, "role": "assistant", "content": result["answer"], "citations": result["citations"]})
    for path, value in [(args.state, state), (args.output, result)]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: result.get(k) for k in ["partial", "reason", "articles_total", "rounds", "tool_counts", "usage", "duration_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

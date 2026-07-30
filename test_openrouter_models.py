# -*- coding: utf-8 -*-
import os
import json
import urllib.request
import urllib.error
import time

API_KEY = os.getenv("OPENROUTER_API_KEY")

if not API_KEY:
    raise RuntimeError("OPENROUTER_API_KEY manquant. Fais d'abord : $env:OPENROUTER_API_KEY='ta_cle'")

URL = "https://openrouter.ai/api/v1/chat/completions"

MODELS = [
    "openai/gpt-oss-120b:free",
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-20b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "meta-llama/llama-3.2-3b-instruct:free",
]

PROMPT = "Réponds seulement par OK en français."

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
    "HTTP-Referer": "http://localhost",
    "X-Title": "EnnoSmart-Test",
}

def test_model(model: str):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Tu réponds très brièvement."},
            {"role": "user", "content": PROMPT},
        ],
        "temperature": 0,
        "max_tokens": 30,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(URL, data=data, headers=headers, method="POST")

    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            elapsed = round(time.time() - start, 2)
            result = json.loads(raw)

            content = (
                result.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
            )

            return {
                "model": model,
                "status": resp.status,
                "ok": True,
                "time": elapsed,
                "answer": content[:120],
            }

    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="ignore")
        elapsed = round(time.time() - start, 2)

        try:
            error_json = json.loads(raw)
            message = error_json.get("error", {}).get("message") or raw[:300]
        except Exception:
            message = raw[:300]

        return {
            "model": model,
            "status": e.code,
            "ok": False,
            "time": elapsed,
            "error": message,
        }

    except Exception as e:
        elapsed = round(time.time() - start, 2)
        return {
            "model": model,
            "status": "ERROR",
            "ok": False,
            "time": elapsed,
            "error": str(e),
        }


print("\n=== TEST OPENROUTER MODELS ===\n")

working = []

for model in MODELS:
    res = test_model(model)

    if res["ok"]:
        working.append(model)
        print(f"✅ OK | {res['status']} | {res['time']}s | {model} | réponse={res['answer']}")
    else:
        print(f"❌ FAIL | {res['status']} | {res['time']}s | {model}")
        print(f"   erreur: {res['error']}\n")

print("\n=== MODELES QUI FONCTIONNENT ===")
for m in working:
    print("-", m)
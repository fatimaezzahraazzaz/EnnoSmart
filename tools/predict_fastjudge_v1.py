# -*- coding: utf-8 -*-
"""
predict_fastjudge_v1.py
------------------------------------------------------------
Tester rapidement FastJudge V1.

Usage :
cd C:\EnnoSmart
python tools\predict_fastjudge_v1.py --text "Aucune différence significative n'a été observée."
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path.cwd()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from modules.NLP.fast_judge.fast_role_classifier import predict_role


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", required=True)
    parser.add_argument("--model-path", default=r"C:\EnnoSmart\models\fastjudge\fastjudge_role_classifier.pkl")
    parser.add_argument("--candidate-role", default="")
    parser.add_argument("--source-type", default="raw")
    args = parser.parse_args()

    out = predict_role(
        args.text,
        model_path=args.model_path,
        candidate_role=args.candidate_role,
        source_type=args.source_type,
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

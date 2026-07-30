#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test batch avec classification anti-bot / paywall (version corrigée).
Prend en compte les 403 courts et utilise networkidle pour les sites protégés.
Usage : python test_classify_batch_corrected.py
"""

import os
import sys
import time
import json
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"
os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_HTTP_TIMEOUT"] = "120"  # timeout plus long

sys.path.insert(0, str(Path(__file__).parent))

from services.http_client import GLOBAL_FETCHER
from services.scholar_direct_fulltext_service import _classify_html_content
from services.scholar_fulltext_fetcher import _is_antibot_html, _is_probably_paywall_or_login

URLS = [
    ("A12825", "https://ieeexplore.ieee.org/document/9374668/"),
    ("A12831", "https://ieeexplore.ieee.org/document/11082447/"),
    ("A12834", "https://www.spiedigitallibrary.org/conference-proceedings-of-spie/11393/2558258/Aerial-and-ground-vehicles-synthetic-SAR-dataset-generation-for-automatic/10.1117/12.2558258.full"),
    ("A12835", "https://ieeexplore.ieee.org/document/8383689/"),
    ("A12836", "https://ieeexplore.ieee.org/document/8938701/"),
    ("A12595", "https://journals.tubitak.gov.tr/cgi/viewcontent.cgi?article=3489&context=elektrik"),
    ("A12849", "https://www.tandfonline.com/doi/full/10.1080/01431161.2023.2176722"),
    ("A12853", "https://ieeexplore.ieee.org/document/10246308/"),
    ("A12869", "https://ieeexplore.ieee.org/document/7460942/"),
    ("A12722", "https://ieeexplore.ieee.org/document/9581475/"),
    ("A12885", "https://www.mdpi.com/2072-4292/10/6/846/pdf?version=1527580542"),
    ("A12754", "https://ieeexplore.ieee.org/document/1236100/"),
    ("A12756", "https://onlinelibrary.wiley.com/doi/10.1002/num.23148"),
    ("A12758", "https://ieeexplore.ieee.org/document/851956/"),
    ("A12759", "https://ieeexplore.ieee.org/document/1514593/"),
    ("A12623", "https://journals.tubitak.gov.tr/cgi/viewcontent.cgi?article=3014&context=elektrik"),
    ("A12765", "https://ieeexplore.ieee.org/document/633855/"),
    ("A12643", "https://www.mdpi.com/2079-9292/8/12/1388/pdf?version=1576048582"),
    ("A12809", "https://ieeexplore.ieee.org/document/7740018/"),
    ("A12809_alt", "https://www.semanticscholar.org/paper/8b8b9c4beece8d11bcf3e6c0a5f70be4b0f4dd8b"),
    ("A12612", "https://avia.ftmd.itb.ac.id/index.php/jav/article/viewFile/19/20"),
]


def test_url(article_id: str, url: str) -> Dict[str, Any]:
    print(f"\n{'='*70}")
    print(f"📄 {article_id} : {url}")
    print("-" * 70)

    result = {
        "article_id": article_id,
        "url": url,
        "timestamp": datetime.now().isoformat(),
        "ok": False,
        "status": "",
        "final_url": "",
        "http_status": None,
        "content_type": "",
        "bytes_downloaded": 0,
        "elapsed": 0,
        "is_pdf": False,
        "classification_status": "",
        "classification_message": "",
        "needs_consultant_upload": False,
        "error": None,
    }

    try:
        ok, info, content = GLOBAL_FETCHER.fetch_bytes(
            url=url,
            headers={"Accept": "text/html,application/pdf,*/*"},
            max_bytes=0,
            referer="https://www.google.com/",
        )

        result["ok"] = ok
        result["status"] = info.get("status", "unknown")
        result["final_url"] = info.get("final_url", url)
        result["http_status"] = info.get("http_status")
        result["content_type"] = info.get("content_type", "")
        result["bytes_downloaded"] = info.get("content_bytes", 0)
        result["elapsed"] = info.get("elapsed_seconds", 0)

        if info.get("reason"):
            result["error"] = info.get("reason")

        if ok and content:
            if content[:5] == b"%PDF-":
                result["is_pdf"] = True
                result["classification_status"] = "pdf_direct"
                result["classification_message"] = "PDF direct (accès ouvert)"
                result["needs_consultant_upload"] = False
                print(f"   📄 PDF direct ({len(content):,} octets)")
            else:
                try:
                    html = content.decode("utf-8", errors="ignore")
                    # Classification avec le statut HTTP
                    classification = _classify_html_content(
                        html,
                        "",  # pas de titre pour rester générique
                        http_status=result["http_status"]
                    )
                    result["classification_status"] = classification.get("status", "unknown")
                    result["classification_message"] = classification.get("message", "")
                    result["needs_consultant_upload"] = (classification.get("status") == "paywall_blocked")
                except Exception as e:
                    result["classification_status"] = "decode_error"
                    result["classification_message"] = f"Erreur : {e}"

        # Affichage
        status_icon = "✅" if result["ok"] else "❌"
        print(f"   {status_icon} HTTP {result['http_status']} | {result['bytes_downloaded']:,} octets | {result['elapsed']:.2f}s")

        if result["is_pdf"]:
            print(f"   📄 Classification : PDF direct (accès ouvert)")
        else:
            print(f"   🏷️  Classification : {result.get('classification_status', 'non_classifie')}")
            if result.get("classification_message"):
                print(f"   💬 {result.get('classification_message')}")

        if result.get("needs_consultant_upload"):
            print("   📤 Action : Upload du PDF par le consultant requis")
        elif result.get("classification_status") == "antibot_blocked":
            print("   🛡️ Action : Proxy résidentiel recommandé ou augmentation du timeout")
        elif result.get("classification_status") == "success":
            print("   ✅ Action : Extraction automatique possible")

    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        print(f"   ❌ Erreur : {e}")

    return result


def main():
    print("\n" + "="*70)
    print("🚀 TEST BATCH AVEC CLASSIFICATION CORRIGÉE (networkidle + 403 court)")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 {len(URLS)} URLs à tester")
    print("="*70)

    results = []
    stats = {
        "success": 0,
        "paywall_blocked": 0,
        "antibot_blocked": 0,
        "pdf_direct": 0,
        "error": 0,
        "unknown": 0,
        "needs_upload": 0,
    }

    for i, (article_id, url) in enumerate(URLS, 1):
        print(f"\n[{i}/{len(URLS)}] Test de {article_id}...")
        result = test_url(article_id, url)
        results.append(result)

        cls = result.get("classification_status", "unknown")
        if result.get("is_pdf"):
            stats["pdf_direct"] += 1
            stats["success"] += 1
        elif cls == "paywall_blocked":
            stats["paywall_blocked"] += 1
            if result.get("needs_consultant_upload"):
                stats["needs_upload"] += 1
        elif cls == "antibot_blocked":
            stats["antibot_blocked"] += 1
        elif cls == "success":
            stats["success"] += 1
        elif cls == "unknown" and result["ok"]:
            stats["unknown"] += 1
        else:
            stats["error"] += 1

        time.sleep(1.0)  # pause plus longue pour éviter les blocages

    print("\n" + "="*70)
    print("📊 RÉSUMÉ DES CLASSIFICATIONS (CORRIGÉ)")
    print("="*70)
    print(f"   ✅ Succès (contenu complet ou PDF) : {stats['success']}")
    print(f"   📄 PDF direct : {stats['pdf_direct']}")
    print(f"   🔒 Paywall (upload requis) : {stats['paywall_blocked']}")
    print(f"   🛡️ Anti-bot bloqué : {stats['antibot_blocked']}")
    print(f"   ❓ Non reconnu : {stats['unknown']}")
    print(f"   ❌ Erreur technique : {stats['error']}")
    print(f"\n   📤 Upload requis (total) : {stats['needs_upload']}")

    print("\n📋 Détail par article :")
    print("-"*70)
    for r in results:
        cls = r.get("classification_status", "unknown")
        icon = {
            "success": "✅",
            "pdf_direct": "📄",
            "paywall_blocked": "🔒",
            "antibot_blocked": "🛡️",
            "empty_content": "⚠️",
            "unknown": "❓",
        }.get(cls, "⚪")
        upload_icon = "📤" if r.get("needs_consultant_upload") else ""
        print(f"   {icon} {r['article_id']:12} : {cls:20} | HTTP {r['http_status']} | {r['bytes_downloaded']:,} octets {upload_icon}")

    report_path = Path(__file__).parent / "classification_results_corrected.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Résultats sauvegardés dans : {report_path}")

    GLOBAL_FETCHER.close()
    print("\n✅ Test terminé.")


if __name__ == "__main__":
    main()
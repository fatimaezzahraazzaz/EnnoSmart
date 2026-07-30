#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test batch des URLs anti-robot / 403 avec Playwright.
Usage : python test_all_articles.py
"""

import os
import sys
import time
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime
import json

# Forcer Playwright
os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"
os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_HTTP_TIMEOUT"] = "90"

sys.path.insert(0, str(Path(__file__).parent))

from services.http_client import GLOBAL_FETCHER

# Liste des URLs à tester
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
    """Teste une URL avec Playwright et retourne les résultats."""
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
        "has_challenge": False,
        "has_abstract": False,
        "error": None,
    }

    try:
        ok, info, content = GLOBAL_FETCHER.fetch_bytes(
            url=url,
            headers={"Accept": "text/html,application/pdf,*/*"},
            max_bytes=0,  # pas de limite
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

        # Analyse du contenu
        if content:
            # Vérifier si c'est un PDF
            if content[:5] == b"%PDF-":
                result["is_pdf"] = True
                print(f"   ✅ PDF valide ({len(content)} octets)")
            else:
                # Vérifier la présence de challenge
                html = content.decode("utf-8", errors="ignore").lower()
                if "challenge" in html or "captcha" in html or "aws waf" in html:
                    result["has_challenge"] = True
                    print(f"   ⚠️  Page de challenge détectée")
                elif "abstract" in html or "article" in html:
                    result["has_abstract"] = True
                    print(f"   ✅ Contenu scientifique détecté (abstract, article...)")
                else:
                    print(f"   ℹ️  Contenu non identifié ({len(content)} octets)")

        # Affichage des résultats
        print(f"   Status      : {result['status']}")
        print(f"   HTTP        : {result['http_status']}")
        print(f"   Taille      : {result['bytes_downloaded']} octets")
        print(f"   Temps       : {result['elapsed']}s")

    except Exception as e:
        result["ok"] = False
        result["error"] = str(e)
        print(f"   ❌ Erreur : {e}")

    return result

def main():
    print("\n" + "="*70)
    print("🚀 TEST BATCH DES ARTICLES BLOQUÉS AVEC PLAYWRIGHT")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 {len(URLS)} URLs à tester")
    print("="*70)

    results = []
    success_count = 0
    challenge_count = 0
    pdf_count = 0
    error_count = 0

    for i, (article_id, url) in enumerate(URLS, 1):
        print(f"\n[{i}/{len(URLS)}] Test de {article_id}...")
        result = test_url(article_id, url)
        results.append(result)

        if result["ok"]:
            success_count += 1
            if result["is_pdf"]:
                pdf_count += 1
            if result["has_challenge"]:
                challenge_count += 1
        else:
            error_count += 1

        # Petite pause entre les requêtes pour ne pas être trop agressif
        time.sleep(1)

    # Résumé final
    print("\n" + "="*70)
    print("📊 RÉSUMÉ DES TESTS")
    print("="*70)
    print(f"   Total URLs testées : {len(URLS)}")
    print(f"   ✅ Succès (Playwright) : {success_count}")
    print(f"   ❌ Échecs : {error_count}")
    print(f"   📄 PDF valides : {pdf_count}")
    print(f"   ⚠️  Pages de challenge : {challenge_count}")

    # Détail par article
    print("\n📋 Détail par article :")
    print("-"*70)
    for r in results:
        status_icon = "✅" if r["ok"] else "❌"
        challenge_icon = "⚠️" if r.get("has_challenge") else ""
        pdf_icon = "📄" if r.get("is_pdf") else ""
        abstract_icon = "📝" if r.get("has_abstract") else ""
        print(f"   {status_icon} {r['article_id']:12} : {r['status']:20} | HTTP {r['http_status']} | {r['bytes_downloaded']:,} octets {challenge_icon}{pdf_icon}{abstract_icon}")

    # Sauvegarde des résultats
    report_path = Path(__file__).parent / "test_results_batch.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Résultats sauvegardés dans : {report_path}")

    # Nettoyage
    GLOBAL_FETCHER.close()
    print("\n✅ Test terminé.")

if __name__ == "__main__":
    main()
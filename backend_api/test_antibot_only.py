#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Test spécifique des 5 articles anti-bot avec le nouveau http_client
et la configuration proxy si présente.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

os.environ["ENNOSMART_USE_PLAYWRIGHT"] = "1"
os.environ["ENNOSMART_USE_CLOUDSCRAPER"] = "0"
os.environ["ENNOSMART_HTTP_TIMEOUT"] = "120"

sys.path.insert(0, str(Path(__file__).parent))

from services.http_client import GLOBAL_FETCHER
from services.scholar_direct_fulltext_service import _classify_html_content

# Les 5 articles anti-bot identifiés
ANTIBOT_URLS = [
    ("A12595", "https://journals.tubitak.gov.tr/cgi/viewcontent.cgi?article=3489&context=elektrik"),
    ("A12849", "https://www.tandfonline.com/doi/full/10.1080/01431161.2023.2176722"),
    ("A12885", "https://www.mdpi.com/2072-4292/10/6/846/pdf?version=1527580542"),
    ("A12756", "https://onlinelibrary.wiley.com/doi/10.1002/num.23148"),
    ("A12623", "https://journals.tubitak.gov.tr/cgi/viewcontent.cgi?article=3014&context=elektrik"),
    ("A12612", "https://avia.ftmd.itb.ac.id/index.php/jav/article/viewFile/19/20"),
]

def test_url(article_id, url):
    print(f"\n{'='*70}")
    print(f"🔍 {article_id} : {url}")
    print("-" * 70)

    ok, info, content = GLOBAL_FETCHER.fetch_bytes(
        url=url,
        headers={"Accept": "text/html,application/pdf,*/*"},
        max_bytes=0,
        referer="https://www.google.com/",
    )

    result = {
        "article_id": article_id,
        "url": url,
        "ok": ok,
        "status": info.get("status"),
        "http_status": info.get("http_status"),
        "bytes": len(content),
        "error": info.get("reason"),
        "classification": None,
    }

    if ok and content:
        if content[:5] == b"%PDF-":
            result["classification"] = "pdf_direct"
            print("   ✅ PDF direct (succès)")
        else:
            try:
                html = content.decode("utf-8", errors="ignore")
                cls = _classify_html_content(html, "", http_status=info.get("http_status"))
                result["classification"] = cls.get("status")
                print(f"   🏷️  Classification : {cls.get('status')} — {cls.get('message')}")
                if cls.get("status") == "paywall_blocked":
                    print("   📤 Action : Upload du PDF par le consultant")
                elif cls.get("status") == "antibot_blocked":
                    print("   🛡️ Action : Utiliser un proxy résidentiel ou augmenter le timeout")
                elif cls.get("status") == "success":
                    print("   ✅ Action : Extraction automatique possible")
            except Exception as e:
                print(f"   ❌ Erreur classification : {e}")
    else:
        print(f"   ❌ Échec : {info.get('reason')}")

    return result

def main():
    print("\n" + "="*70)
    print("🧪 TEST DES 5 ARTICLES ANTI-BOT AVEC PROXY")
    print(f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("="*70)

    results = []
    for article_id, url in ANTIBOT_URLS:
        r = test_url(article_id, url)
        results.append(r)

    print("\n" + "="*70)
    print("📊 RÉSUMÉ")
    print("="*70)
    for r in results:
        status = r.get("classification") or r.get("status") or "unknown"
        icon = "✅" if r["ok"] else "❌"
        print(f"   {icon} {r['article_id']:12} : {status:20} | HTTP {r['http_status']} | {r['bytes']:,} octets")

    # Sauvegarde
    report_path = Path(__file__).parent / "antibot_test_results.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"\n💾 Résultats sauvegardés dans : {report_path}")

    GLOBAL_FETCHER.close()
    print("\n✅ Test terminé.")

if __name__ == "__main__":
    main()
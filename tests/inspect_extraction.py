"""
inspect_extraction.py — EnnoSmart debug tool
Usage :
  python inspect_extraction.py <fichier> [options]

Options :
  --text          Affiche tous les chunks texte
  --visual        Affiche tous les chunks visuels
  --chunk N       Affiche un chunk spécifique (index 0-based)
  --search MOT    Recherche un mot dans tous les chunks
  --export        Exporte dans inspection_result.json
  --summary       Résumé uniquement (défaut)
"""
import sys, json, re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from modules.extraction.router import extract, SourceTag


def sep(title="", char="═", width=70):
    if title:
        pad = (width - len(title) - 2) // 2
        print(f"\n{char * pad} {title} {char * pad}")
    else:
        print(char * width)


def print_chunk(index, chunk, chunk_type="TEXT"):
    sep(f"{chunk_type} CHUNK {index}", char="─")
    print(f"Longueur : {len(chunk)} chars | {len(chunk.split())} mots")
    # Signaler les sections intégrées
    flags = []
    if "[FORMULES DÉTECTÉES]" in chunk: flags.append("⚗️  FORMULES")
    if "[FORMULES OMML]" in chunk:      flags.append("⚗️  FORMULES OMML")
    if "[IMAGES]" in chunk:             flags.append("🖼️  IMAGES")
    if flags:
        print("Contient : " + " | ".join(flags))
    print()
    print(chunk)


def chunk_preview(chunk, max_chars=200):
    """Résumé d'un chunk : header + indicateurs + début."""
    lines = chunk.split("\n")
    header = lines[0] if lines else ""
    flags = []
    if "[FORMULES DÉTECTÉES]" in chunk: flags.append("⚗️ formules")
    if "[FORMULES OMML]" in chunk:      flags.append("⚗️ omml")
    if "[IMAGES]" in chunk:             flags.append("🖼️ images")
    preview = " ".join(chunk.split())[:max_chars]
    flag_str = "  [" + " | ".join(flags) + "]" if flags else ""
    return header + flag_str, preview


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__); sys.exit(1)

    file_path   = args[0]
    show_text   = "--text"   in args
    show_visual = "--visual" in args
    do_export   = "--export" in args
    show_summary = "--summary" in args or not any(
        a in args for a in ["--text", "--visual", "--chunk", "--search"]
    )

    chunk_index = None
    if "--chunk" in args:
        chunk_index = int(args[args.index("--chunk") + 1])

    search_term = None
    if "--search" in args:
        search_term = args[args.index("--search") + 1].lower()

    print(f"\n🔍 Extraction de : {file_path}")
    import logging; logging.basicConfig(level=logging.WARNING)
    result = extract(file_path)
    all_chunks = result.text_chunks + result.visual_chunks

    # ── Résumé ───────────────────────────────────────────────────────────
    if show_summary:
        sep("RÉSUMÉ EXTRACTION")
        print(f"  Fichier        : {result.file_name}")
        print(f"  Catégorie      : {result.file_category.value}")
        print(f"  Score          : {result.confidence_score:.2f}")
        print(f"  Chunks texte   : {len(result.text_chunks)}")
        print(f"  Chunks visuels : {len(result.visual_chunks)}")
        print(f"  Total chunks   : {result.total_chunks}")
        print(f"  Pages          : {result.page_count}")
        print(f"  Tags           : {result.tags}")
        print(f"  Erreurs        : {result.extraction_errors or 'aucune'}")

        # Stats intégration
        n_formules = sum(1 for c in result.text_chunks if "[FORMULES" in c)
        n_images   = sum(1 for c in result.text_chunks if "[IMAGES]" in c)
        if n_formules or n_images:
            print(f"\n  Chunks avec formules intégrées : {n_formules}")
            print(f"  Chunks avec images intégrées   : {n_images}")

        if result.detected_rd_sections:
            print("\n  Sections R&D :")
            for s in result.detected_rd_sections: print(f"    • {s}")
        if result.title:  print(f"\n  Titre  : {result.title}")
        if result.author: print(f"  Auteur : {result.author}")

        sep("APERÇU CHUNKS TEXTE (5 premiers)", char="─")
        for i, chunk in enumerate(result.text_chunks[:5]):
            header, preview = chunk_preview(chunk)
            print(f"  [{i}] {header}")
            print(f"       {preview}…")
            print()

        if result.visual_chunks:
            sep("APERÇU CHUNKS VISUELS (3 premiers)", char="─")
            for i, chunk in enumerate(result.visual_chunks[:3]):
                header, preview = chunk_preview(chunk)
                print(f"  [{i + len(result.text_chunks)}] {header}")
                print(f"       {preview}…")
                print()

    # ── Tous les chunks texte ─────────────────────────────────────────────
    if show_text:
        sep(f"TOUS LES CHUNKS TEXTE ({len(result.text_chunks)})")
        for i, chunk in enumerate(result.text_chunks):
            print_chunk(i, chunk, "TEXT")

    # ── Tous les chunks visuels ───────────────────────────────────────────
    if show_visual:
        sep(f"TOUS LES CHUNKS VISUELS ({len(result.visual_chunks)})")
        for i, chunk in enumerate(result.visual_chunks):
            print_chunk(i + len(result.text_chunks), chunk, "VISUAL")

    # ── Chunk spécifique ──────────────────────────────────────────────────
    if chunk_index is not None:
        if chunk_index < len(result.text_chunks):
            print_chunk(chunk_index, result.text_chunks[chunk_index], "TEXT")
        elif chunk_index < len(all_chunks):
            print_chunk(chunk_index, result.visual_chunks[chunk_index - len(result.text_chunks)], "VISUAL")
        else:
            print(f"❌ Index {chunk_index} hors limites (max: {len(all_chunks)-1})")

    # ── Recherche ─────────────────────────────────────────────────────────
    if search_term:
        sep(f"RECHERCHE : '{search_term}'")
        found = 0
        for i, chunk in enumerate(all_chunks):
            if search_term in chunk.lower():
                found += 1
                chunk_type = "TEXT" if i < len(result.text_chunks) else "VISUAL"
                lower = chunk.lower(); pos = lower.find(search_term)
                ctx = "..." + chunk[max(0,pos-100):min(len(chunk),pos+len(search_term)+100)] + "..."
                highlighted = ctx.replace(search_term, f"\033[1;33m{search_term}\033[0m")
                print(f"\n[{chunk_type} #{i}] {chunk.split(chr(10))[0]}")
                print(f"  {highlighted}")
        print(f"\n→ {found} chunk(s) contenant '{search_term}'")

    # ── Export JSON ───────────────────────────────────────────────────────
    if do_export:
        export_path = Path("inspection_result.json")
        data = {
            "summary": result.summary(),
            "metadata": {
                "title": result.title, "author": result.author,
                "creation_date": result.creation_date, "page_count": result.page_count,
            },
            "rd_sections": result.detected_rd_sections,
            "text_chunks": [
                {"index": i, "type": "text", "length": len(c), "words": len(c.split()),
                 "header": c.split("\n")[0] if c else "",
                 "has_formulas": "[FORMULES" in c, "has_images": "[IMAGES]" in c,
                 "content": c}
                for i, c in enumerate(result.text_chunks)
            ],
            "visual_chunks": [
                {"index": i + len(result.text_chunks), "type": "visual",
                 "length": len(c), "words": len(c.split()),
                 "header": c.split("\n")[0] if c else "", "content": c}
                for i, c in enumerate(result.visual_chunks)
            ],
        }
        export_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n✅ Exporté → {export_path.resolve()}")
        print(f"   {len(result.text_chunks)} chunks texte + {len(result.visual_chunks)} visuels")


if __name__ == "__main__":
    main()
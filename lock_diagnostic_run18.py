r"""Audit local autonome du run 18 : aucune génération, aucun accès OpenAI.

Exécution : .\.venv\Scripts\python.exe -B lock_diagnostic_run18.py
Seul le rapport texte est écrit. PostgreSQL est consulté dans une transaction
READ ONLY. Les reconstructions sont distinguées des données archivées.
"""
from __future__ import annotations

import argparse
import ast
import copy
import difflib
import gzip
import hashlib
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT), str(ROOT / "backend_api")]
sys.dont_write_bytecode = True


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def constant(path: Path, name: str) -> str:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
    value = node.value
    if isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == "strip":
        return ast.literal_eval(value.func.value).strip()
    return ast.literal_eval(value)


def offline_method(path: Path, class_name: str, method: str):
    """Exécute un constructeur pur de prompt, sans initialiser son client LLM."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    function = copy.deepcopy(next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method))
    function.decorator_list = []
    function.returns = None
    for arg in [*function.args.posonlyargs, *function.args.args, *function.args.kwonlyargs]:
        arg.annotation = None
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {}
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[method]


def read_run(run_id: int):
    from db.database import engine
    from sqlalchemy import text

    with engine.connect() as connection:
        try:
            connection.execute(text("SET TRANSACTION READ ONLY"))
            run = connection.execute(text("SELECT * FROM diagnostic_runs WHERE id=:id"),
                                     {"id": run_id}).mappings().one()
            artifacts = connection.execute(text(
                "SELECT id, artifact_key, metadata_json, payload_data FROM project_artifacts "
                "WHERE project_id=:project ORDER BY id DESC"), {"project": run["project_id"]}).mappings()
            selected = next((dict(row) for row in artifacts
                             if (row["metadata_json"] or {}).get("diagnostic_run_id") == run_id
                             and row["artifact_key"].endswith("full_report.json")), None)
            if selected is None:
                raise RuntimeError(f"Aucun artefact complet attribué au run {run_id}; refus d'utiliser un autre run.")
            payload = bytes(selected.pop("payload_data"))
            if payload.startswith(b"\x1f\x8b"):
                payload = gzip.decompress(payload)
            report = json.loads(payload)
            persisted = connection.execute(text(
                "SELECT count(*) FROM verrous WHERE diagnostic_run_id=:id"), {"id": run_id}).scalar_one()
            return dict(run), selected, report, persisted
        finally:
            connection.rollback()


def build_report(run_id: int) -> str:
    from agents.EnnoDiagnostic.V2.ennodiagnostic_v2 import global_fast_path as fast
    from agents.EnnoDiagnostic.V2.ennodiagnostic_v2.schemas import DocumentInput

    source = ROOT / "agents/EnnoDiagnostic/V2/ennodiagnostic_v2"
    llm_source = ROOT / "modules/LLM/llm_client.py"
    backup = ROOT / "ennodiagnostic_patch_backups/global_fast_lock_candidates_20261001_015453"
    protected = [*source.glob("*.py"), llm_source]
    before_hashes = {p: sha(p.read_bytes()) for p in protected}
    run, artifact, report, persisted = read_run(run_id)
    core = report["diagnostic_v2"]["core"]
    review = core.get("global_review", {})
    meta = core["audit"]["global_fast_path"]
    if core["audit"].get("extraction_path") != "GLOBAL_FAST_PATH" or meta.get("mode") != "global":
        raise RuntimeError("Cet audit attend un run Global Fast Path à appel global unique.")

    corpus_path = Path(report["fulltext_corpus"]["path"])
    corpus_files = {}
    for path in sorted(corpus_path.glob("*.json")):
        if path.name == "manifest.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        corpus_files[str(data["document_id"])] = (path, data)
    docs = [DocumentInput(str(d["document_id"]), d["name"], corpus_files[str(d["document_id"])][1]["text"])
            for d in core["documents"]]
    config = SimpleNamespace(
        passage_max_chars=650, model=review.get("model", meta["generation_audit"][0]["model"]),
        provider=review.get("provider", "openai"), reasoning_effort=review.get("reasoning_effort", "medium"),
    )
    # Aucun extract(), extract_json(), review() ou generate() réel n'est appelé.
    extractor = fast.GlobalFastExtractor(None, config)
    passages = fast.build_passages(docs, config.passage_max_chars)
    by_passage = {p.passage_id: p for p in passages}
    fingerprint = extractor._fingerprint(docs)
    fingerprint_ok = fingerprint == meta["fingerprint"]
    backup_manifest = json.loads((backup / "manifest.json").read_text(encoding="utf-8"))
    constructor_checks = {
        str(p.relative_to(ROOT)): sha(p.read_bytes()) == backup_manifest["files_sha256"][p.relative_to(ROOT).as_posix()]
        for p in (source / "runtime_adapters.py", llm_source)
    }
    registry = fast._registry_prompt(passages)
    preflight_chars = len(fast.GLOBAL_SYSTEM) + len(registry) + 64

    # Capture de la construction réelle de l'adaptateur avec un client inerte.
    # Cela ne constitue PAS une récupération de la requête HTTP historique.
    capture = {}
    class Recorder:
        def generate(self, **kwargs):
            capture.update(kwargs)
            return "CAPTURE_LOCALE_SANS_GENERATION"
    adapter_config = SimpleNamespace(**vars(config), max_output_tokens=meta["reserved_output_tokens"],
                                     max_prompt_chars=150000)
    adapter = SimpleNamespace(client=Recorder(), config=adapter_config)
    construct = offline_method(source / "runtime_adapters.py", "GlobalFastLLMAdapter", "extract_json")
    construct(adapter, fast.GLOBAL_SYSTEM, registry)
    adapter_input = capture["prompt"]
    client_tree = ast.parse(llm_source.read_text(encoding="utf-8"))
    cls = next(n for n in client_tree.body if isinstance(n, ast.ClassDef) and n.name == "LLMClient")
    transport = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_generate_openai_background")
    prefix_node = next(n for n in ast.walk(transport) if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Constant)
                               and t.slice.value == "input" for t in n.targets))
    expected_prefix = ast.literal_eval(prefix_node.value.left)
    if not isinstance(prefix_node.value, ast.BinOp) or not isinstance(prefix_node.value.right, ast.Name) or prefix_node.value.right.id != "prompt":
        raise RuntimeError("Construction du transport non reconnue; refus d'inventer la requête.")
    api_input = expected_prefix + adapter_input
    api_instructions = offline_method(llm_source, "LLMClient", "_system_message")(None, True)

    quote_errors = []
    for item in core["extractions"]:
        evidence = item["evidence"]
        p = by_passage.get(evidence.get("passage_id"))
        if p is None or p.document_id != str(evidence["document_id"]) or p.text != evidence["quote"]:
            quote_errors.append(item["item_id"])
    reconstruction_ok = (fingerprint_ok and not quote_errors
                         and len(passages) == meta["passages"] and len(docs) == meta["documents"]
                         and sum(len(d.text) for d in docs) == meta["source_chars"]
                         and preflight_chars == meta["prompt_chars"] and meta.get("no_truncation") is True)

    cache_path = corpus_path.parent.parent.parent / "analysis_cache/global_fast" / (meta["fingerprint"] + ".json")
    # corpus = year/documents/processed/fulltext_rag_v1 -> year/analysis_cache/...
    if not cache_path.is_file():
        raise RuntimeError(f"Cache normalisé du run introuvable : {cache_path}")
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    if cache.get("fingerprint") != meta["fingerprint"]:
        raise RuntimeError("Cache d'un autre corpus : audit arrêté.")
    facts = cache["facts"]
    groups = {}
    for item in core["extractions"]:
        groups.setdefault(item["metadata"]["fact_group"], []).append(item)
    cache_consistent = len(groups) == len(facts) and all(
        all(x["statement"] == facts[g-1]["statement"] and x["kind"] == facts[g-1]["kind"]
            for x in items) for g, items in groups.items())
    if not cache_consistent:
        raise RuntimeError("Le cache ne correspond pas aux faits enregistrés du run.")

    patterns = {
        "PERFORMANCES / COMPILABILITÉ": r"compil|couvert|performance|erreurs? de|echecs?",
        "A — ÉVALUATION DE LA QUALITÉ": r"criteres? d.?evalu|qualite|qualitatif|qualitative|correctness|sufficiency|lisibilite|oracle|smells|assertion",
        "B — OPÉRATIONNEL / CONFIDENTIALITÉ / SOUVERAINETÉ": r"confidential|souverain|donnees sensibles|ressources? (?:de |du |pour |limite)|modeles? locaux|\blocal(?:ement)?\b|partager|fournisseurs?|gpu|memoire|8000|8.?000|7000000|millions de parametres",
    }
    direct = {name: {p.passage_id for p in passages if re.search(pattern, fast._fold(p.text))}
              for name, pattern in patterns.items()}
    extended = {}
    for name, ids in direct.items():
        related = set(ids)
        # Un voisin immédiat du même document est affiché pour les titres courts.
        for index, p in enumerate(passages):
            if p.passage_id in ids:
                for other in passages[max(0,index-1):index+2]:
                    if other.document_id == p.document_id:
                        related.add(other.passage_id)
        extended[name] = related

    lines = []
    def add(value=""):
        lines.append(str(value))
    def heading(title):
        add("\n" + "=" * 88); add(title); add("=" * 88)
    add(f"DIAGNOSTIC DES VERROUS — RUN {run_id}")
    add(f"Rapport généré localement : {datetime.now(timezone.utc).isoformat()}")
    add(f"Run créé : {run['created_at']} ; statut : {run['status']} ; projet : {run['project_id']}")
    add(f"Artefact PostgreSQL : id={artifact['id']} ; {artifact['artifact_key']}")
    add("Aucune modification du pipeline, aucun accès OpenAI, aucune génération LLM, aucun diagnostic relancé.")
    add("IMPORTANT : l'artefact et le cache stockent les faits APRÈS validation/retry.")
    add("La requête HTTP complète et les réponses brutes AVANT validation ne sont pas archivées localement.")
    add("L'entrée ci-dessous est une reconstruction déterministe contrôlée, pas une copie d'une requête HTTP sauvegardée.")

    heading("1. PROMPT RÉEL ENVOYÉ À GPT-5 — RECONSTRUCTION CONTRÔLÉE")
    add("L'adaptateur de production est exécuté avec un client local inerte pour capturer sa construction du prompt.")
    add(f"Correspondance de l'empreinte du run (documents, ordre, prompt, modèle, effort, schéma) : {fingerprint_ok}")
    add(f"Correspondance de tous les contrôles de reconstruction : {reconstruction_ok}")
    add("Constructeur d'entrée et transport identiques à la sauvegarde précédant le run : " + json_text(constructor_checks))
    add(f"Empreinte archivée : {meta['fingerprint']} ; reconstruite : {fingerprint}")
    add(f"Citations enregistrées incompatibles avec le registre reconstruit : {quote_errors}")
    add("Ces contrôles attestent la concordance avec les données du run ; ils ne remplacent pas une archive HTTP.")
    add("Champ API instructions reconstruit :\n" + api_instructions)
    add("Début exact du champ input reconstruit, incluant provenance, lock, multi-label et relecture :")
    add(expected_prefix + "INSTRUCTIONS SPECIFIQUES\n" + fast.GLOBAL_SYSTEM + "\n\nDOSSIER BRUT\n")
    add("Le registre exact est construit ensuite ; son ordre et les passages thématiques sont détaillés en section 2.")

    heading("2. INPUT RÉEL DU RUN 18 — CORPUS ARCHIVÉ ET REGISTRE RECONSTRUIT")
    add(json_text({"documents": len(docs), "passages": len(passages), "caracteres_source": meta["source_chars"],
                   "taille_preflight_archivee": meta["prompt_chars"], "taille_preflight_reconstruite": preflight_chars,
                   "caracteres_adaptateur": len(adapter_input), "caracteres_input_API_reconstruit": len(api_input),
                   "sha256_input_API_reconstruit": sha(api_input.encode("utf-8")),
                   "tokens_entree_reels_premier_appel": meta["generation_audit"][0]["prompt_tokens"],
                   "mode": meta["mode"], "no_truncation_archive": meta["no_truncation"]}))
    add("La taille préflight comprend une marge de 64 caractères ; ce n'est pas la longueur exacte du champ input API.")
    add("ORDRE DES DOCUMENTS ET EMPREINTES DE TEXTE :")
    for d in docs:
        add(f"{d.document_id} | {d.name} | {len(d.text)} caractères | sha256={sha(d.text.encode('utf-8'))}")
    add("ORDRE COMPLET DES PASSAGES (650 caractères maximum par passage) :")
    for p in passages:
        add(f"{p.passage_id}|{p.document_id}|{p.char_start}:{p.char_end}")
    add("SÉLECTION THÉMATIQUE : recherche lexicale, sans qualification automatique ni attribution de provenance.")
    add("Les voisins immédiats sont inclus pour garder le contexte des titres ; les passages sont imprimés une seule fois.")
    for name in patterns:
        add(f"{name} : {len(direct[name])} correspondances directes, {len(extended[name])} avec contexte.")
    add("VÉRIFICATIONS DÉTAILLÉES DES SOUS-THÈMES OPÉRATIONNELS :")
    for label, pattern in {
        "confidentialité / données sensibles": r"confidential|donnees sensibles|partager.{0,90}donnees",
        "mot souveraineté explicite": r"souverain",
        "expression modèles locaux explicite": r"modeles? locaux|llms? locaux",
        "ressources / taille / contexte": r"ressources?.{0,30}(?:calcul|limite)|8000|8.?000|7000000|millions de parametres",
    }.items():
        found = [p.passage_id for p in passages if re.search(pattern, fast._fold(p.text))]
        add(f"{label} : {found or 'aucune occurrence lexicale exacte'}")
    add("L'absence d'un mot ne prouve pas l'absence du concept ; les passages voisins sont disponibles ci-dessous.")
    selected = set().union(*extended.values())
    for p in passages:
        if p.passage_id not in selected:
            continue
        names = [name + (" [direct]" if p.passage_id in direct[name] else " [voisin]")
                 for name in patterns if p.passage_id in extended[name]]
        add(f"\n--- {p.passage_id} | document_id={p.document_id} | {p.document_name} | chars={p.char_start}:{p.char_end}")
        add("Thèmes : " + "; ".join(names))
        add("PASSAGE EXACT — DÉBUT"); add(p.text); add("PASSAGE EXACT — FIN")

    heading("3. RÉPONSE BRUTE GPT-5 — DISPONIBILITÉ ET FAITS EFFECTIVEMENT ARCHIVÉS")
    add("RÉPONSE BRUTE AVANT VALIDATION : NON DISPONIBLE dans les artefacts/logs locaux examinés.")
    add("Le code _call() analyse la réponse, mais generation_audit n'enregistre que métadonnées et identifiants.")
    add("_write_cache() sauvegarde les faits validés APRÈS retry. Ils ne doivent pas être présentés comme JSON brut initial.")
    add("Le run a utilisé un retry ; des formulations/catégories ont donc pu changer. Le nombre brut initial est indéterminable localement.")
    add("Métadonnées des deux réponses déjà produites (aucune récupération OpenAI effectuée) :")
    add(json_text(meta["generation_audit"]))
    add(f"Cache normalisé : {cache_path}")
    add(f"Correspondance cache / core.extractions : {cache_consistent}")
    add(f"Faits normalisés : {len(facts)} ; ExtractionItems : {len(core['extractions'])}")
    add("Comptages normalisés : " + json_text(dict(Counter(f["kind"] for f in facts))))
    for kind in ("objective", "lock", "method", "demarche", "parameter", "metric", "result"):
        add("\n" + kind.upper())
        for index, f in enumerate(facts, 1):
            if f["kind"] == kind:
                add(f"F{index} (fact_group={index}, APRÈS validation)")
                add(json_text({key: f.get(key) for key in ("kind", "statement", "passage_ids", "source_role", "temporal_status")}))

    heading("4. ANALYSE DES DEUX THÈMES MANQUANTS")
    analyses = {}
    for name in list(patterns)[1:]:
        ids = direct[name]
        refs = [(index, f) for index, f in enumerate(facts, 1) if set(f["passage_ids"]) & ids]
        locks = [(index, f) for index, f in refs if f["kind"] == "lock"]
        analyses[name] = (refs, locks)
        add("\n" + name)
        add("Passages directs : " + ", ".join(p.passage_id for p in passages if p.passage_id in ids))
        add("Leurs textes exacts figurent en section 2. Association ci-dessous par passage_id, sans inventer de lien sémantique.")
        add(f"Faits normalisés référant ces passages : {len(refs)} ; candidats lock référant ces passages : {len(locks)}")
        for index, f in refs:
            add(f"F{index} | {f['kind']} | {f['source_role']} | {f['temporal_status']} | passages={f['passage_ids']}")
            add(f["statement"])
            add("Thème également nommé dans l'énoncé : " + ("OUI" if re.search(patterns[name], fast._fold(f["statement"]))
                                                          else "NON (association par passage seulement)"))
        covered = set().union(*(set(f["passage_ids"]) for _, f in refs)) if refs else set()
        add("Passages directs sans AUCUN fait normalisé référencé : " + ", ".join(
            p.passage_id for p in passages if p.passage_id in ids - covered))
        add("La simple coïncidence de passage n'assure pas que chaque phrase de ce passage ait été extraite.")
        add("Une contrainte ou un objectif n'établit pas, à lui seul, une difficulté technique non maîtrisée.")
        if name.startswith("A"):
            criteria_ids = {p.passage_id for p in passages if re.search(
                r"criteres? d.?evalu|evaluer la qualite|evaluation qualitative|mesures de qualite", fast._fold(p.text))}
            focused_locks = [index for index, f in enumerate(facts, 1)
                             if f["kind"] == "lock" and set(f["passage_ids"]) & criteria_ids]
            add("FOCUS SUR LA DÉFINITION DES CRITÈRES : " + ", ".join(p.passage_id for p in passages if p.passage_id in criteria_ids))
            add("Locks référant ces passages précis : " + str(focused_locks))
            add("Le lock F34 concerne compilation/couverture : son intersection avec le thème large qualité")
            add("ne constitue PAS un candidat distinct sur la difficulté de définir des critères d'évaluation.")
        else:
            add("F35 nomme explicitement données sensibles et ressources limitées, mais est classé method.")
            add("F24/F37 évoquent des embeddings ; leur passage p219 contient aussi un masque de confidentialité")
            add("administratif. Cette coïncidence n'est PAS une extraction de difficulté de confidentialité.")
            add("p116 contient l'impossibilité de partager le code avec des fournisseurs ; aucun fait normalisé ne le référence.")
    add("\nLECTURE DES DONNÉES :")
    add("Les critères sont représentés notamment par des objectifs et des métriques. Les contraintes opérationnelles")
    add("apparaissent notamment dans des faits classés method/parameter ; cela ne prouve pas qu'elles aient été évaluées comme difficultés.")
    add("L'extraction partielle des passages et l'absence de lock sont mesurables ; le raisonnement interne du modèle ne l'est pas.")
    add("Confidentialité, souveraineté et ressources sont recherchées séparément des simples mentions administratives de masque de diapositive.")

    heading("5. CONTRAT DE SORTIE ET MULTI-LABEL")
    schema_start = fast.GLOBAL_SYSTEM.index('{"reviewed_passage_ids"')
    schema_end = fast.GLOBAL_SYSTEM.index("Référence les passage_id")
    add("Structure littérale demandée dans le prompt (les ... sont des placeholders) :")
    add(fast.GLOBAL_SYSTEM[schema_start:schema_end].strip())
    add("Enums Python actuelles :")
    add(json_text({"kind": sorted(fast.KINDS), "source_role": sorted(fast.ROLES), "temporal_status": sorted(fast.TEMPORAL)}))
    add("Champs : kind, statement, source_role, temporal_status, passage_ids, document_ids, needs_review.")
    add("facts est une liste de faits. Chaque fait a UN kind ; plusieurs faits peuvent partager les mêmes passages.")
    add("Pas de maxItems ni de longueur maximale par statement ; budget global de sortie : " + str(meta["reserved_output_tokens"]))
    add("Adaptateur : json_mode=True ; aucun response_schema fourni. Le transport utilise json_object, pas un JSON Schema strict.")
    add("constraint n'est PAS une catégorie disponible. Une contrainte ne doit pas être appelée paramètre par obligation du format.")
    add("Contrôles inchangés : enums, preuve existante, document correspondant, compatibilité lexicale, provenance et booléen needs_review.")
    add("VÉRIFICATION PYTHON HORS LLM DU PARTAGE DE PREUVE :")
    project_passage = by_passage["p14"]
    for first in ("result", "objective", "parameter"):
        examples = [{"kind": kind, "statement": project_passage.text.strip(), "source_role": "project_evidence",
                     "temporal_status": "planned", "passage_ids": [project_passage.passage_id],
                     "document_ids": [project_passage.document_id], "needs_review": False} for kind in (first, "lock")]
        validated = [extractor._validate(f, by_passage) for f in examples]
        converted = extractor._items(validated, by_passage)
        assert [x.kind for x in converted] == [first, "lock"]
        add(f"{first} + lock avec le même passage : ACCEPTÉ ; deux items distincts, aucun choix exclusif imposé.")
    add("Ces exemples testent la structure/validation, PAS la justesse de ces catégories ni la validité scientifique d'un verrou.")
    add("PARTAGE DE PREUVES RÉEL DANS LES FAITS NORMALISÉS DU RUN :")
    for index, f in enumerate(facts, 1):
        if f["kind"] == "lock":
            for other_index, other in enumerate(facts, 1):
                common = sorted(set(f["passage_ids"]) & set(other["passage_ids"]), key=lambda p: int(p[1:]))
                if other["kind"] != "lock" and common:
                    add(f"F{index} lock + F{other_index} {other['kind']} : {common}")

    heading("6. COMPARAISON AVANT / APRÈS LA DERNIÈRE CORRECTION")
    old_path = backup / "agents/EnnoDiagnostic/V2/ennodiagnostic_v2/global_fast_path.py"
    old_system = constant(old_path, "GLOBAL_SYSTEM")
    add(f"AVANT : fichier sauvegardé dans {backup} (état avant modification)")
    add("APRÈS : code actuel ; correspondance avec l'empreinte du run : " + str(fingerprint_ok))
    add("Diff exact du prompt GLOBAL_SYSTEM :")
    add("\n".join(difflib.unified_diff(old_system.splitlines(), fast.GLOBAL_SYSTEM.splitlines(),
                                      fromfile="avant/GLOBAL_SYSTEM", tofile="actuel/GLOBAL_SYSTEM", lineterm="")))
    add("Le prompt initial est conservé ; ajout du candidat implicite, indices concordants, partage de preuves et relecture au même appel.")
    add("Il ne promet pas d'extraire tout intitulé de verrou : pour l'implicite il exige plusieurs observations concordantes.")
    add("Les critères/contraintes seulement formulés comme objectifs ou conditions peuvent donc rester dans d'autres catégories.")
    add("Le prompt donne aussi surtout des indices expérimentaux (échecs, faibles performances, correction insuffisante),")
    add("sans développer de contrat positif spécifique aux incertitudes d'évaluation ni aux difficultés opérationnelles documentées.")
    add("Le multi-label mentionne explicitement result + lock ; objective + lock et parameter + lock sont possibles, mais moins explicitement guidés.")
    add("L'explication causale est une hypothèse fondée sur le contrat et les faits archivés, pas une lecture du raisonnement de GPT.")

    heading("7. CONCLUSION")
    lock_facts = [f for f in facts if f["kind"] == "lock"]
    lock_items = [x for x in core["extractions"] if x["kind"] == "lock"]
    entities = [e for e in core["entities"] if e["kind"] == "lock"]
    add(f"Traçabilité observée : {len(lock_facts)} fait(s) lock normalisé(s) -> {len(lock_items)} items/preuves")
    add(f"-> {len(entities)} entité(s) consolidée(s)/qualifiée(s) -> {persisted} verrou(s) métier persisté(s).")
    for entity in entities:
        add(f"{entity['entity_id']} | {entity['metadata'].get('qualification_status')} | {entity['source_item_ids']}")
    add("Rejets archivés : " + json_text(meta.get("rejected_candidates", [])))
    add("\nLe modèle n'a pas vu les preuves :")
    add("NON — entrée reconstruite concordante avec l'empreinte du run." if reconstruction_ok
        else "INDÉTERMINABLE — la reconstruction ne concorde pas intégralement; aucune affirmation historique autorisée.")
    add("\nLe modèle a vu les preuves mais les a classées dans d'autres catégories :")
    add("OUI, pour une partie des thèmes : les faits normalisés ci-dessus le montrent. D'autres passages n'ont aucun fait référencé.")
    add("\nLe prompt multi-label est réellement présent dans l'appel :")
    add("OUI — présent dans la construction concordante avec l'empreinte; pas de requête HTTP brute archivée." if fingerprint_ok
        else "INDÉTERMINABLE — code courant seulement, empreinte non concordante.")
    add("\nLe format de sortie permet réellement plusieurs catégories pour une même preuve :")
    add("OUI — contrôlé en Python et observé dans les faits du run (résultats/métriques/démarche + lock).")
    add("\nUne étape Python supprime les locks :")
    add("NON observé dans ce run : le lock normalisé est conservé jusqu'à la persistance. Aucun rejet final archivé.")
    add("Limite : sans réponse brute initiale, on ne peut exclure une reclassification lors du retry. Littérature/needs_review restent filtrés avant qualification par conception.")
    add("\nCause la plus probable du rappel faible :")
    add("Le prompt favorise les difficultés persistantes démontrées par des résultats et corrections. Les thèmes qualité/critères")
    add("et confidentialité/ressources sont partiellement extraits comme objectifs, métriques, méthodes ou paramètres, sans lock distinct.")
    add("Cela peut être approprié pour une simple exigence ; il faut établir une difficulté concrète avant d'exiger un candidat supplémentaire.")
    add("Le multi-label est permis, mais les associations objective/contraintes + lock sont moins explicitement demandées que result + lock.")
    add("L'absence de JSON brut archivé limite le diagnostic exact du premier appel versus son retry.")
    add("\nEndroit précis à modifier :")
    add(str(source / "global_fast_path.py") + " — GLOBAL_SYSTEM, bloc « LOCK seulement », garde-fou pour l'implicite et relecture finale.")
    add("Fonction d'utilisation : GlobalFastExtractor._covered_call -> _call. La politique des candidats doit être clarifiée ici si elle est validée.")
    add("Le script n'a modifié ni ce bloc ni le pipeline. Aucun patch recommandé sans examiner les passages exacts.")

    after_hashes = {p: sha(p.read_bytes()) for p in protected}
    if before_hashes != after_hashes:
        raise RuntimeError("Des fichiers de production ont changé pendant l'audit; rapport non écrit pour éviter une comparaison incohérente.")
    add(f"\nContrôle d'intégrité : {len(protected)} fichiers source inchangés pendant l'audit.")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", type=int, default=18)
    parser.add_argument("--output", type=Path, default=ROOT / "lock_diagnostic_run18.txt")
    args = parser.parse_args()
    output = args.output.resolve()
    if output.parent != ROOT or output.suffix.lower() != ".txt":
        parser.error("Le rapport doit être un fichier .txt directement dans le workspace.")
    if output.exists() and output.is_symlink():
        parser.error("Refus d'écrire via un lien symbolique.")
    report = build_report(args.run_id)
    output.write_text(report, encoding="utf-8")
    print(f"Rapport créé : {output} ({len(report)} caractères)")
    print("0 appel OpenAI/LLM ; 0 diagnostic relancé ; transaction PostgreSQL READ ONLY.")
    print("Entrée reconstruite vérifiée ; réponse brute avant validation non archivée localement.")


if __name__ == "__main__":
    main()

"""Optional, additive completeness review of already validated Fast Path facts."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
from dataclasses import asdict
from pathlib import Path

from .config import GlobalFastPathConfig, _global_setting
from .fusion import _norm, _tokens
from .global_fast_path import Passage, _registry_prompt
from .semantic_extractor import parse_json_payload
from .lock_controls import CONTROL_VERSION, NOT_LOCK_REASONS, lock_signals, validate_lock_coverage
from .diagnostic_debug import record

VERSION = "completeness-audit-v3-lock-coverage"
AUDIT_SYSTEM = """
Contrôle ADDITIF de complétude d'un dossier R&D brut, après son extraction validée.
Les FAITS INITIAUX sont immuables. Ne les supprime, modifie, reclasse, fusionne
ou remplace jamais. Ne produis aucune synthèse ni qualification de verrou.
Ta seule mission : proposer les faits IMPORTANTS directement soutenus par les
PASSAGES EXACTS mais absents de l'inventaire, en contrôlant séparément les sept
catégories objective, lock, method, demarche, parameter, metric et result.
Réponds uniquement {"facts":[...],"lock_coverage_decisions":[...]}.
Une liste facts vide est valable, mais chaque LOCK SIGNAL exige une décision.
Chaque proposition a exactement les champs du contrat initial :
{"kind":"objective|lock|method|demarche|parameter|metric|result",
"statement":"fait précis sans invention",
"source_role":"project_evidence|literature|context|admin",
"temporal_status":"planned|in_progress|performed|unclear",
"passage_ids":["p1"],"document_ids":["document_id"],"needs_review":false}.
Référence uniquement les identifiants fournis. Ne recopie pas de citations.
Les catégories ne sont PAS exclusives : une information présente dans une
DEMARCHE peut soutenir une METHOD distincte, ou un PARAMETER/METRIC/RESULT.
METHOD = mécanisme, technique, algorithme, protocole, architecture, stratégie
technique, procédure technique ou principe de transformation utilisé ou testé
pour réaliser une opération technique. Aucun nom d'algorithme n'est obligatoire.
DEMARCHE = étape des travaux ; entraîner/tester/étudier sont des démarches.
L'architecture ou le mécanisme utilisé pour cette étape peut être une méthode.
Une transformation modèle source → règles de transformation → modèle cible
peut être une méthode ; la validation croisée k-fold est une méthode.
PARAMETER = variable, configuration, réglage, condition expérimentale ou
contrainte technique ayant une influence directe sur le comportement,
l'évaluation, la reproductibilité ou les performances du système.
Inclure les seuils de confiance, hyperparamètres, configurations, valeurs
choisies, pondérations de classes, politiques d'augmentation, résolution,
jeux de paramètres et conditions d'entrée techniquement importantes.
Une contrainte est un PARAMETER seulement si elle caractérise réellement les
conditions expérimentales ou opérationnelles, influence le comportement ou
la difficulté technique et est explicitement soutenue par les sources.
La densité/qualité variable des données, les labels partiels ou décalés et
les contraintes de sortie (par exemple validité topologique) peuvent être
des conditions techniques ; ne les transforme pas automatiquement en réglages.
EXCLURE PAR DÉFAUT de PARAMETER : simple description ou nom d'un dataset,
nombre total d'images, kilomètres, liste exhaustive de classes/IDs,
pourcentages descriptifs de répartition, résultat mesuré, métrique,
méthode/algorithme ou objectif.
EXCEPTION : une caractéristique du dataset peut être PARAMETER si les sources
montrent explicitement son utilisation comme variable/condition expérimentale
ou une comparaison du projet dépendant directement de cette caractéristique.
Un inventaire d'images ou de classes seul reste du contexte. Une comparaison
sous-ensemble/jeu complet peut justifier la quantité de données utilisée.
Un déséquilibre de données expliquant un biais et corrigé expérimentalement
peut être une contrainte ; formule la condition, sans recopier tous les
pourcentages. Conserve séparément les seuils et jeux de réglages différents.
Ne duplique pas automatiquement un optimiseur déjà correctement décrit comme
METHOD. Si son choix/configuration constitue réellement un PARAMETER distinct,
formule « choix de l'optimiseur » ou son réglage et conserve la preuve exacte.
Cette définition s'applique UNIQUEMENT aux ajouts : aucun PARAMETER initial
ne peut être supprimé, modifié ou reclassé par cet audit.
METRIC = ce qu'on mesure ;
RESULT = valeur ou constat effectivement obtenu ; OBJECTIVE = but recherché.
Ne confonds pas paramètre, contrainte, étape, mesure et résultat.
Sépare littérature et projet, passage par passage : une méthode publiée n'est
pas une méthode utilisée par le projet, ni un résultat publié un résultat projet.
Conserve la littérature pour audit avec source_role=literature.
LOCK = candidat seulement ; LockQualifier reste seul responsable du statut.
N'ajoute un LOCK que si une difficulté/incertitude technique importante est
directement soutenue par les preuves du projet et absente de l'inventaire.
Un objectif, une exigence, une contrainte métier, un mauvais résultat isolé ou
une limite de littérature ne deviennent pas automatiquement un verrou projet.
Un verrou implicite exige des preuves projet concordantes de difficulté réelle.
Contrôle LOCK dans ce MÊME appel, uniquement additif :
1. Examine les passages signalés par les titres OU le texte exact comme
« verrou », « difficulté technique », « incertitude scientifique ou technique »
ou « problème à résoudre ». Le titre est seulement un SIGNAL, jamais une preuve.
2. Relis leurs facts OBJECTIVE/METRIC/DEMARCHE, ainsi que PARAMETER/RESULT/METHOD
et les passages projet pertinents, même si l'information est déjà conservée.
Vérifie : « Difficulté/incertitude technique réelle, ou seulement objectif/exigence ? »
3. Vérifie si un LOCK existant couvre déjà cette incertitude précise : une
difficulté techniquement différente n'est pas couverte par un verrou voisin.
4. S'il manque ET que les preuves projet montrent une difficulté non maîtrisée,
une incertitude scientifique/technique, une limite persistante, un comportement
instable/non reproductible, une propriété technique impossible à obtenir,
une absence de méthode suffisamment fiable, un compromis/une contradiction
technique non résolu, ajoute seulement un LOCK distinct. Une difficulté de
validation, mesure, reproductibilité ou définition d'un oracle n'est pas un
simple engineering_context si les preuves projet montrent qu'elle empêche
techniquement de déterminer de façon fiable si une sortie est correcte.
Si ces preuves soutiennent une difficulté technique plausible et que tu hésites
avec engineering_context, ajoute le candidat avec needs_review=true et
decision=new_lock_added ; LockQualifier décidera du statut.
5. Sinon, ne propose aucun LOCK. Ne supprime, modifie ou reclasse aucun fact.
Les catégories ne sont pas exclusives : OBJECTIVE/METRIC et LOCK peuvent
coexister sur les mêmes preuves ; retourne seulement le LOCK manquant.
Garde-fous : section « verrou » seule, objectif seul, tâche, exigence seule,
contrainte seule, métrique seule, livrable, cadrage, difficulté business ou
organisationnelle seule et littérature seule ne suffisent pas à un LOCK projet.
« Définir un protocole » reste OBJECTIVE seul ; une couverture mesurée seule
reste METRIC/RESULT. N'invente pas une absence de méthode fiable à partir du seul
souhait de reproductibilité. Aucun nouvel appel LLM pour ce contrôle.
Ne répète aucun fait déjà présent dans la même catégorie, même reformulé.
Ne propose pas deux formulations du même fait. Garde les valeurs, conditions,
expériences et difficultés distinctes. En cas d'attribution incertaine, utilise
needs_review=true. Aucun quota de faits, aucun fait ou lien inventé.

POST-CONDITION LOCK OBLIGATOIRE : traite CHAQUE signal_id fourni, même si un
OBJECTIVE, METRIC, PARAMETER ou DEMARCHE contient déjà cette information.
Les faits initiaux portent des fact_id f1, f2, ... ; chaque nouveau fact porte
un fact_id a1, a2, ... unique dans la réponse. Aucun fait initial ne doit être renvoyé.
Pour chaque signal retourne exactement une décision :
{"signal_id":"identifiant fourni","passage_ids":["passages du signal et preuves du LOCK"],
"decision":"covered_by_existing_lock|new_lock_added|not_a_lock",
"existing_lock_ids":[],"new_lock_ids":[],"reason_code":"code","reason":"justification précise"}.
covered_by_existing_lock : existing_lock_ids doit référencer un LOCK projet fN,
jamais un OBJECTIVE/METRIC/PARAMETER ; vérifie même objet, incertitude et portée.
new_lock_added : propose le candidat dans facts et référence son fact_id aN
dans new_lock_ids ; toutes ses preuves doivent figurer dans passage_ids.
not_a_lock : aucune référence de LOCK ; reason_code vaut exclusivement
objective_only, requirement_only, business_constraint, metric_only, literature_only,
engineering_context, task_only, context_only, isolated_result, no_technical_uncertainty,
ou insufficient_project_evidence. Donne une raison technique, pas seulement « planned ».
Un travail projet prévu est project_evidence + planned, pas context par temporalité.
Ne considère pas la présence d'un fact d'une AUTRE catégorie comme couverture LOCK.
Examine les passages concordants de TOUS les documents : un objectif de critère,
une exigence de reproductibilité et une difficulté observée de comparaison peuvent
ensemble soutenir une incertitude, même si chaque passage seul ne suffit pas.
Recherche aussi la conséquence technique non maîtrisée des contraintes, au-delà
de leur classement PARAMETER. Ne crée pas de LOCK sur une contrainte seule.
Une formulation de méthode trop étroite n'établit pas automatiquement la couverture
d'une incertitude d'évaluation plus large ; explique la portée à partir des preuves.
Conserve les difficultés déclarées mais insuffisamment démontrées comme candidats
needs_review=true si leur contenu et les preuves concordantes expriment réellement
une incertitude technique. Le LockQualifier décidera du statut, jamais cet audit.
""".strip()


def _duplicate(left: dict, right: dict) -> bool:
    """Conservative equivalence; never compare different semantic categories."""
    if any(left[key] != right[key] for key in ("kind", "source_role", "temporal_status")):
        return False
    a, b = left["statement"], right["statement"]
    if _norm(a) == _norm(b):
        return True
    # Reordered equivalent formulations on overlapping proofs. Different values,
    # negation or technical terms must remain separate, even on the same passage.
    if not set(left["passage_ids"]) & set(right["passage_ids"]):
        return False
    values = lambda text: tuple(re.findall(r"\d+(?:[.,]\d+)?", text))
    qualifiers = {"pas", "non", "sans", "aucun", "jamais", "moins", "plus"}
    qa = set(_norm(a).split()) & qualifiers
    qb = set(_norm(b).split()) & qualifiers
    # Preserve direction/order: source→target is not target→source. A pair of
    # measured values in reversed order must not be silently treated as equal.
    directional = {"source", "cible", "entree", "sortie", "avant", "apres", "initial", "final"}
    direction = lambda text: tuple(t for t in _norm(text).split() if t in directional)
    return (bool(_tokens(a)) and _tokens(a) == _tokens(b) and values(a) == values(b)
            and qa == qb and direction(a) == direction(b))


class CompletenessAuditor:
    def __init__(self, llm, config: GlobalFastPathConfig, cache_dir=None):
        self.llm = llm
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.debug_sink = None
        self.repair_callback = None

    def _fingerprint(self, passages, facts):
        payload = {
            "version": VERSION, "schema": "facts-v1", "controls": CONTROL_VERSION, "prompt": AUDIT_SYSTEM,
            "provider": self.config.provider, "model": self.config.model,
            "effort": self.config.reasoning_effort,
            "passages": [asdict(p) for p in passages], "initial_facts": facts,
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    @staticmethod
    def _prompt(passages, facts):
        indexed = [{"fact_id": f.get("fact_id", f"f{i}"), **f} for i, f in enumerate(facts, 1)]
        signals = lock_signals(passages, facts)
        return "FAITS INITIAUX VALIDÉS (ne pas les retourner):\n" + json.dumps(
            indexed, ensure_ascii=False, separators=(",", ":"),
        ) + "\nLOCK_SIGNALS:\n" + json.dumps(
            [{k: s[k] for k in ("signal_id", "passage_ids")} for s in signals],
            ensure_ascii=False, separators=(",", ":")) + "\n" + _registry_prompt(passages)

    def _fits(self, prompt):
        chars = len(AUDIT_SYSTEM) + len(prompt) + 64
        cap = _global_setting("ENNOSMART_LLM_MAX_OUTPUT_TOKENS_CAP", "").strip()
        output = min(self.config.max_output_tokens, int(cap)) if cap.isdigit() and int(cap) > 0 else self.config.max_output_tokens
        client = getattr(self.llm, "client", None)
        context = (client._context_window_for_provider(self.config.provider)
                   if client is not None and hasattr(client, "_context_window_for_provider")
                   else self.config.context_window_tokens)
        return chars <= self.config.max_prompt_chars and (chars + 2) // 3 + output + 4096 < context

    def _batches(self, passages, facts):
        facts = [{**f, "fact_id": f"f{i}"} for i, f in enumerate(facts, 1)]
        prompt = self._prompt(passages, facts)
        if self._fits(prompt):
            return [(passages, facts, prompt)]
        # Large dossiers: exact passages, multi-document round robin and bounded
        # inventories. No call per kind/passage, no silent text truncation.
        by_doc = {}
        for passage in passages:
            by_doc.setdefault(passage.document_id, []).append(passage)
        ordered = []
        for index in range(max(map(len, by_doc.values()), default=0)):
            ordered.extend(rows[index] for rows in by_doc.values() if index < len(rows))
        batches, current = [], []
        def request(rows):
            ids = {p.passage_id for p in rows}
            inventory = [f for f in facts if ids & set(f["passage_ids"])]
            return rows, inventory, self._prompt(rows, inventory)
        for passage in ordered:
            trial = request([*current, passage])
            if not self._fits(trial[2]):
                if not current:
                    raise ValueError("audit_passage_and_inventory_over_budget")
                batches.append(request(current))
                current = [passage]
                if not self._fits(request(current)[2]):
                    raise ValueError("audit_passage_and_inventory_over_budget")
            else:
                current.append(passage)
        if current:
            batches.append(request(current))
        if sorted(p.passage_id for batch, _, _ in batches for p in batch) != sorted(p.passage_id for p in passages):
            raise ValueError("audit_batch_coverage_lost")
        return batches

    @staticmethod
    def _accept(rows, initial, by_passage, validate):
        additions, rejected, duplicates = [], [], 0
        for index, row in enumerate(rows):
            try:
                fact = validate(row, by_passage)
            except (ValueError, TypeError, KeyError) as exc:
                rejected.append({"index": index, "reason": str(exc), "candidate": row})
                continue
            if any(_duplicate(fact, previous) for previous in [*initial, *additions]):
                duplicates += 1
                continue
            additions.append(fact)
        return additions, rejected, duplicates

    def audit(self, passages: list[Passage], initial_facts: list[dict], validate):
        initial = copy.deepcopy(initial_facts)
        by_passage = {p.passage_id: p for p in passages}
        key = self._fingerprint(passages, initial)
        path = self.cache_dir / f"completeness_{key}.json" if self.cache_dir else None
        audit = {"version": VERSION, "fingerprint": key, "status": "failed",
                 "llm_calls": 0, "cache_hit": False, "added_count": 0,
                 "model": self.config.model, "provider": self.config.provider,
                 "reasoning_effort": self.config.reasoning_effort}
        proposals = []
        signals = lock_signals(passages, initial)
        record(self, "completeness.input", {"initial_facts": initial,
            "passages": [asdict(p) for p in passages], "lock_signals": signals})
        try:
            if path and path.is_file():
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                    if cached.get("fingerprint") != key or cached.get("version") != VERSION or not isinstance(cached.get("facts"), list):
                        raise ValueError("invalid_audit_cache")
                    additions, rejected, duplicates = self._accept(cached["facts"], initial, by_passage, validate)
                    if rejected or duplicates:
                        raise ValueError("invalid_audit_cache_facts")
                    cached_index = cached.get("additions_by_id", {})
                    if not isinstance(cached_index, dict):
                        raise ValueError("invalid_cached_additions_index")
                    for ref, row in cached_index.items():
                        if not isinstance(ref, str) or validate(row, by_passage) not in additions:
                            raise ValueError("unvalidated_cached_coverage_fact")
                    decisions, errors = validate_lock_coverage(cached.get("lock_coverage_decisions"),
                        signals, initial, cached_index, by_passage)
                    if errors:
                        raise ValueError("invalid_cached_lock_coverage")
                    record(self, "completeness.cache_read", cached)
                    return additions, {**audit, "status": "complete", "cache_hit": True,
                                       "added_count": len(additions), "rejected_candidates": cached.get("rejected_candidates", []),
                                       "duplicate_count": cached.get("duplicate_count", 0), "mode": cached.get("mode", "global"),
                                       "lock_signal_count": len(signals), "lock_decision_count": len(decisions),
                                       "lock_coverage_complete": True}
                except (OSError, ValueError, TypeError, KeyError):
                    pass
            batches = self._batches(passages, initial)
            audit.update({"mode": "global" if len(batches) == 1 else "multi_lot", "batch_count": len(batches)})
            additions, rejected, duplicates = [], [], 0
            coverage, additions_by_id = [], {}
            for batch_index, (subset, _, prompt) in enumerate(batches, 1):
                audit["llm_calls"] += 1
                raw = self.llm.extract_json(AUDIT_SYSTEM, prompt)
                record(self, "completeness.raw_response", {"batch": batch_index,
                    "system": AUDIT_SYSTEM, "prompt": prompt, "raw": raw})
                payload = parse_json_payload(raw)
                if not isinstance(payload, dict) or not isinstance(payload.get("facts"), list):
                    raise ValueError("invalid_completeness_response")
                proposals.extend(payload["facts"])
                # Reuse the existing validator with ONLY passages supplied to
                # this batch. Preserve each rejected proposal exactly for audit.
                accepted, invalid, repeated = self._accept(
                    payload["facts"], [*initial, *additions],
                    {p.passage_id: p for p in subset}, validate,
                )
                additions.extend(accepted)
                rejected.extend(invalid)
                duplicates += repeated
                current = self._index_additions(payload, accepted, additions_by_id, f"b{batch_index}_")
                subset_ids = {p.passage_id for p in subset}
                batch_signals = [s for s in signals if set(s["passage_ids"]) <= subset_ids]
                batch_decisions, errors = validate_lock_coverage(current, batch_signals,
                    initial, additions_by_id, by_passage)
                if errors and self.repair_callback is not None:
                    targets = batch_signals if "__unexpected__" in errors else [
                        s for s in batch_signals if s["signal_id"] in errors]
                    target_ids = {s["signal_id"] for s in targets}
                    # Keep exact neighbouring passages and all referenced LOCK
                    # proofs; no regeneration of the complete dossier.
                    wanted = {p for s in targets for p in s["passage_ids"]}
                    seeds = set(wanted)
                    for index, passage in enumerate(passages):
                        if passage.passage_id in seeds:
                            for neighbour in passages[max(0, index-1):index+2]:
                                if neighbour.document_id == passage.document_id:
                                    wanted.add(neighbour.passage_id)
                    for f in [*initial, *additions_by_id.values()]:
                        if f["kind"] == "lock":
                            wanted.update(f["passage_ids"])
                    repair_passages = [p for p in passages if p.passage_id in wanted]
                    repair_prompt = self._prompt(repair_passages, initial) + "\nSIGNAUX CIBLES SEULEMENT:\n" + json.dumps(targets, ensure_ascii=False)
                    repair_prompt += "\nFAITS AJOUTES VALIDES (références new_lock_ids permises):\n" + json.dumps(additions_by_id, ensure_ascii=False)
                    repair_prompt += "\nERREURS A REPARER:\n" + json.dumps(errors, ensure_ascii=False)
                    repair_system = AUDIT_SYSTEM + "\nRETRY CIBLE : retourne une décision seulement pour chaque signal de SIGNAUX CIBLES SEULEMENT. Conserve les facts existants ; facts contient seulement d'éventuels nouveaux faits manquants."
                    if len(repair_prompt) + len(repair_system) + 64 <= self.config.max_prompt_chars:
                        audit["llm_calls"] += 1
                        audit["targeted_retries"] = audit.get("targeted_retries", 0) + 1
                        repaired = parse_json_payload(self.repair_callback(repair_system, repair_prompt))
                        record(self, "completeness.retry_result", repaired)
                        if not isinstance(repaired, dict) or not isinstance(repaired.get("facts"), list):
                            raise ValueError("invalid_completeness_repair")
                        new, invalid, repeated = self._accept(repaired["facts"],
                            [*initial, *additions], {p.passage_id: p for p in repair_passages}, validate)
                        additions.extend(new)
                        rejected.extend(invalid)
                        duplicates += repeated
                        rows = self._index_additions(repaired, new, additions_by_id, f"b{batch_index}r_")
                        repaired_decisions, remaining = validate_lock_coverage(rows, targets,
                            initial, additions_by_id, by_passage)
                        batch_decisions = [d for d in batch_decisions if d["signal_id"] not in target_ids] + repaired_decisions
                        errors = remaining
                record(self, "completeness.coverage_validation", {"batch": batch_index,
                    "decisions": batch_decisions, "errors": errors,
                    "additions": accepted, "rejected": invalid})
                coverage.extend(batch_decisions)
            # Batches may share context, but cannot silently contradict decisions.
            final_by_signal = {}
            for decision in coverage:
                previous = final_by_signal.get(decision["signal_id"])
                if previous is not None and previous != decision:
                    raise ValueError("conflicting_inter_batch_lock_coverage")
                final_by_signal[decision["signal_id"]] = decision
            coverage, errors = validate_lock_coverage(list(final_by_signal.values()),
                signals, initial, additions_by_id, by_passage)
            audit.update({"status": "complete", "added_count": len(additions),
                          "rejected_candidates": rejected, "duplicate_count": duplicates,
                          "lock_signal_count": len(signals), "lock_decision_count": len(coverage),
                          "lock_coverage_complete": not errors})
            record(self, "completeness.final", {"facts": additions,
                "lock_coverage_decisions": coverage, "coverage_errors": errors})
            if errors:
                audit.update({"status": "incomplete", "lock_coverage_errors": errors})
            if path and not errors:
                try:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    payload = {"version": VERSION, "fingerprint": key, "facts": additions,
                               "rejected_candidates": rejected, "duplicate_count": duplicates, "mode": audit["mode"],
                               "lock_coverage_decisions": coverage, "additions_by_id": additions_by_id,
                               "debug_reference": str(self.debug_sink.path) if self.debug_sink else None}
                    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
                        json.dump(payload, handle, ensure_ascii=False)
                        temporary = Path(handle.name)
                    os.replace(temporary, path)
                except OSError as exc:
                    audit["cache_error"] = str(exc)
            print(f"[EnnoDiagnostic][CompletenessAudit] {audit['mode']}: {len(additions)} ajout(s), "
                  f"{len(rejected)} rejet(s), {duplicates} doublon(s), {audit['llm_calls']} appel(s)", flush=True)
            return additions, audit
        except Exception as exc:
            audit.update({"error": f"{type(exc).__name__}:{exc}", "partial_proposals": proposals})
            print(f"[EnnoDiagnostic][CompletenessAudit] échec isolé: {audit['error']}; extraction initiale conservée", flush=True)
            return [], audit

    @staticmethod
    def _index_additions(payload, accepted, index, prefix):
        """Resolve model IDs only to additions that passed the normal validator."""
        mapping = {}
        duplicates = set()
        for position, row in enumerate(payload["facts"], 1):
            if not isinstance(row, dict):
                continue
            local_id = row.get("fact_id", f"a{position}")
            if not isinstance(local_id, str) or not local_id.startswith("a"):
                continue
            if local_id in mapping:
                duplicates.add(local_id)
                continue
            normalized = next((f for f in accepted if all(
                f.get(k) == row.get(k) for k in ("kind", "statement", "source_role", "temporal_status", "needs_review"))
                and set(f["passage_ids"]) == set(row.get("passage_ids") or [])), None)
            if normalized is not None:
                mapping[local_id] = prefix + local_id
                index[prefix + local_id] = normalized
        for key in duplicates:
            index.pop(mapping.pop(key, ""), None)
        decisions = copy.deepcopy(payload.get("lock_coverage_decisions"))
        if isinstance(decisions, list):
            for row in decisions:
                if isinstance(row, dict) and isinstance(row.get("new_lock_ids"), list):
                    row["new_lock_ids"] = [mapping.get(value, value) for value in row["new_lock_ids"]]
        return decisions

"""Existing scientific tools; per-article failures never discard their batch."""
import copy
from contextvars import ContextVar
from typing import Literal
from pydantic import Field
from .reuse import load
from .science import CoverageReview, Measurement, save_review, passage_index, reading_status, validate_measurements
base = load("_toolkit_base", "toolkit.py")
for name in dir(base):
    if not name.startswith("_"):
        globals()[name] = getattr(base, name)


# The reused toolkit calls passages only for analyze_article(read). Scope its
# view before ranking, so totals and cursors use the same population, without
# changing snapshots, IDs or reading receipts.
# Context-local filtering keeps concurrent conversations independent; V4's own
# module and evidence functions are untouched.
_read_section = ContextVar('v5_read_section', default=None)
_passages = base.passages


def _section_passages(article):
    rows = _passages(article)
    from .evidence import proof_scope, proof_status
    state = proof_scope.get()
    if state is not None:
        rows = [r for r in rows if proof_status(state, r['evidence_id']) in {'usable', 'missing'}]
    section = _read_section.get()
    if section:
        rows = [r for r in rows if section.casefold() in str(r.get('section') or '').casefold()]
    return rows


base.passages = _section_passages


class ClaimReplacement(base.Args):
    field: str = Field(min_length=1, max_length=100)
    statement: str = Field(min_length=1, max_length=20000)
    evidence_ids: list[str] = Field(min_length=1, max_length=40)


class AnalyzeArgs(base.AnalyzeArgs):
    action: Literal['read', 'save', 'repair', 'index', 'review']
    replace_claims: list[ClaimReplacement] = Field(default_factory=list, max_length=20,
        description='save uniquement : anciennes affirmations à remplacer explicitement, identifiées par champ, '
            'statement exact et evidence_ids ; historique conservé, aucun remplacement implicite.')
    section_filter: str | None = Field(default=None, max_length=300,
        description='index/read : filtre littéral de section, null = toutes les sections, y compris JATS sans pages. '
            'Les evidence_ids explicites ont priorité sur ce filtre.')
    index_offset: int = Field(default=0, ge=0)
    index_limit: int = Field(default=20, ge=1, le=80)
    coverage_review: CoverageReview | None = Field(default=None,
        description='Revue de Sol pour la question courante, non exhaustive : dimensions essentielles, preuves, incertitudes. '
            'action=review ou save ; une présentation seule ne valide aucune dimension.')
    measurements: list[Measurement] = Field(default_factory=list, max_length=30,
        description='save : mesures explicitement structurées par Sol, valeur/unité littérales, conditions, méthode et preuves. '
            'Aucune conversion automatique des anciens Claim ; auteur, observation et inférence restent distincts.')


class ClaimsArgs(base.ClaimsArgs):
    measurement_offset: int = Field(default=0, ge=0)
    measurement_limit: int = Field(default=8, ge=1, le=20)


base.TOOLS['analyze_article'] = (AnalyzeArgs, base.TOOLS['analyze_article'][1] +
    ' V5 : index fournit les passages disponibles par section sans les lire ; read présente les preuves ; '
    'save enrichit la fiche existante ; review enregistre la couverture de la question. '
    'method décrit l’approche ; parameters ses réglages ; results les observations ; metrics les critères chiffrés ; '
    'limitations les limites. Chaque affirmation cite ses preuves exactes ; une inférence reste signalée.')
base.TOOLS['get_analysis_claims'] = (ClaimsArgs, base.TOOLS['get_analysis_claims'][1] +
    ' Les structured_measurements sont récupérées par measurement_offset/measurement_limit, avec conditions et preuves.')


def definitions(*args, **kwargs):
    """Same strict schemas/descriptions; omit Pydantic's redundant UI titles."""
    def compact(value):
        if isinstance(value, dict):
            return {k: compact(v) for k, v in value.items() if not (k == 'title' and isinstance(v, str))}
        if isinstance(value, list):
            return [compact(v) for v in value]
        return value
    return compact(base.definitions(*args, **kwargs))

def annotate_depth(state, registry, result):
    """Point 4 of run 48d2c500: next to V4's access_status (provenance, unchanged), each
    analysed work shown to Sol carries its actual reading depth, so an abstract or an
    introduction from the PDF is never taken for a finished deep analysis."""
    from .science import analysis_depth
    if not isinstance(result, dict):
        return result
    rows = [a for a in result.get('articles', []) if isinstance(a, dict)]
    rows += [r['article'] for r in result.get('reading', []) if isinstance(r, dict) and isinstance(r.get('article'), dict)]
    for row in rows:
        article = registry.get(row.get('id'))
        if article is not None and article.analysis:
            depth = analysis_depth(state, article)
            row['analysis_depth'] = {k: depth[k] for k in ('depth', 'label', 'question_sufficient')}
    return result


EMPTY_LIBRARY = ("Bibliothèque de cette conversation vide et aucune notice retrouvée dans la mémoire autorisée pour "
    "cette requête : une nouvelle consultation locale ne trouvera rien de plus tant que rien n'est collecté. "
    "La découverte passe par search_scientific.")


def empty_library_guidance(result, registry):
    """P1: an empty local search on an empty library says so (run b10ab481: V4's guidance
    asked to evaluate collected candidates first, priority 'synthesize', while none existed;
    Sol re-declared its goal and repeated the search). Facts and receipts are unchanged."""
    if not isinstance(result, dict) or result.get('error') or result.get('articles') or registry or result.get('reused_analyses'):
        return result
    result = copy.deepcopy(result)
    page = result.get('memory_page') or {}
    guidance = EMPTY_LIBRARY + (f" Page suivante de la mémoire seulement : offset={page['next_offset']}."
        if page.get('has_more') and page.get('next_offset') is not None else "")
    result['discovery_attention'] = {**(result.get('discovery_attention') or {}), 'priority': 'discover', 'guidance': guidance}
    if isinstance(result.get('delivery'), dict):
        result['delivery'] = {**result['delivery'], 'guidance': guidance}
    return result


class ScholarTools(base.ScholarTools):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        save = self.store.save

        def save_with_identities():
            # N05: before the registry overwrites the saved articles, an extraction of
            # the same document that changed keeps the IDs of its unchanged passages.
            from .evidence import carry_passage_identities
            for aid, article in self.registry.items():
                previous = (self.state.get('articles', {}).get(aid) or {}).get('fulltext')
                if previous and previous is not article.fulltext:
                    carry_passage_identities(self.state, previous, article)
            save()
        self.store.save = save_with_identities

    def analysis_claims(self, args, page_chars=12000):
        from .evidence import active_article
        view = copy.copy(self)
        view.store = copy.copy(self.store)
        view.registry = dict(self.registry)
        view.registry[args.article_id] = active_article(self.state, self.store.article(args.article_id))
        view.store.registry = view.registry
        return base.ScholarTools.analysis_claims(view, args, page_chars)

    def verify(self, ids, deadline, local_only=False):
        from .stops import StopResearch
        rows = []
        for aid in ids:
            from .evidence import active_article
            article = self.registry.get(aid)
            original = (article.analysis, article.analysis_basis) if article is not None else None
            try:
                if article is not None:
                    view = active_article(self.state, article)
                    article.analysis, article.analysis_basis = view.analysis, view.analysis_basis
                rows.extend(super().verify([aid], deadline, local_only)["articles"])
            except StopResearch:
                raise
            except Exception as exc:
                rows.append({"article_id": aid, "valid": False, "errors": [type(exc).__name__], "retry_required": True})
            finally:
                if original is not None:
                    article.analysis, article.analysis_basis = original
                    self.save()
        self.save()
        return {"articles": rows, "valid": all(row["valid"] for row in rows)}

    def refresh_html_extractions(self, ids):
        """Only the current HTML extractor's text is read (see html_currency), for the
        articles a tool actually uses; other stored articles are left as they are."""
        from .html_currency import refresh_article
        changed = {}
        for aid in dict.fromkeys(ids):
            article = self.registry.get(aid)
            if article is not None:
                outcome = refresh_article(article, self.state, self.snapshots, self.evidence, self.config)
                if outcome:
                    changed[aid] = outcome
        if changed:
            receipts = self.state.setdefault('html_extraction_refreshes', [])
            receipts.extend({'article_id': aid, 'outcome': outcome} for aid, outcome in changed.items())
            self.store.save()
        return changed

    def reuse_articles(self, query, *, exact=False, limit=8, offset=0):
        # Science imported from another session is made current before any reading.
        reused = super().reuse_articles(query, exact=exact, limit=limit, offset=offset)
        self.refresh_html_extractions(reused)
        return reused

    def execute(self, name, raw, deadline, **kwargs):
        from .evidence import proof_scope
        token = proof_scope.set(self.state)
        try:
            return self._execute(name, raw, deadline, **kwargs)
        finally:
            proof_scope.reset(token)

    def _execute(self, name, raw, deadline, **kwargs):
        from .stops import StopResearch
        named = raw.get('article_ids') if isinstance(raw, dict) else None
        named = [*(named if isinstance(named, list) else []), *([raw['article_id']] if isinstance(raw, dict) and isinstance(raw.get('article_id'), str) else [])]
        self.refresh_html_extractions([a for a in named if isinstance(a, str)])
        if name == 'manage_outline' and not getattr(self, 'consultant_edit', False):
            from .workflow import explicit_plan_request
            if explicit_plan_request(self.state):
                # The consultant's explicit request: V4 applies it as the consultant's edit
                # (previous version kept, approval withdrawn, to be given again).
                self.consultant_edit = True
                try:
                    return super().execute(name, raw, deadline, **kwargs)
                finally:
                    self.consultant_edit = False
        if name == 'search_collected_articles':
            return empty_library_guidance(super().execute(name, raw, deadline, **kwargs), self.registry)
        if name == 'get_article' and isinstance(raw, dict) and raw.get('identifiers'):
            from experiments.ennoscholar_v5.policy import known_notices, notice_receipt, resolve_id
            known = known_notices(self.state, raw['identifiers'])
            remaining = [i for i in raw['identifiers'] if resolve_id(self.state, i) not in known]
            if known and not remaining:
                return notice_receipt(known)
            # Overlapping batch: only the new or changed works are looked up and sent.
            result = self.fetch_notices({**raw, 'identifiers': remaining}, deadline, **kwargs)
            if known and isinstance(result, dict) and not result.get('error'):
                receipt = notice_receipt(known)
                result = {**result, 'already_received': receipt['already_received'],
                    'already_received_guidance': receipt['guidance']}
            return annotate_depth(self.state, self.registry, result)
        if name == 'compare_articles':
            from .science import check_comparative_measurements, check_relation_attribution, task_for
            args = base.CompareArgs.model_validate(raw)
            check_comparative_measurements(self,args)
            check_relation_attribution(self, args)
            from .argumentation import relation_key, relation_current
            existing = {relation_key(r) for r in self.state.get('scientific_relations', []) if relation_current(self, r)}
            if args.relations is not None:
                unique = []
                for relation in args.relations:
                    key = relation_key(relation.model_dump())
                    if key not in existing:
                        unique.append(relation.model_dump())
                        existing.add(key)
                # Validate through the existing path, even for an all-known batch.
                raw = {**raw, 'relations': unique}
            # V4 explicitly admits needs_review as a partial comparison. An index
            # created by V5 must not leave a valid saved card permanently queued.
            for aid in args.article_ids:
                article = self.store.article(aid)
                status = reading_status(self.state,article)
                if status['states']['analyzed']:
                    task = task_for(self.state,aid)
                    task.update(core_covered=status['question_sufficient'],
                        status='partial' if status['question_sufficient'] else 'needs_review')
            before = len(self.state.get('comparisons', []))
            result = super().execute(name, raw, deadline, **kwargs)
            # Preserve historical records; omit only an identical new append.
            from .task_results import fingerprint
            old = self.state.get('comparisons', [])[:before]
            for row in self.state.get('comparisons', [])[before:]:
                if not any(fingerprint(row)==fingerprint(previous) for previous in old):
                    old.append(row)
            if 'comparisons' in self.state:
                self.state['comparisons'] = old
            self.save()
            return result
        if name == 'identify_research_gaps':
            from .comparison_memory import identity
            before = len(self.state.get('research_gaps', []))
            result = super().execute(name, raw, deadline, **kwargs)
            for bundle in self.state.get('research_gaps', [])[before:]:
                bundle['proof_identity'] = identity(self.state, bundle.get('article_ids') or [])
            if len(self.state.get('research_gaps', [])) != before:
                self.save()
            return result
        if name == 'get_analysis_claims':
            args = ClaimsArgs.model_validate(raw)
            result = super().execute(name, raw, deadline, **kwargs)
            from .evidence import usable_proof
            measurements = [m for m in self.store.article(args.article_id).analysis.get('structured_measurements', {}).values()
                if m.get('evidence_ids') and all(usable_proof(self.state, eid, article_id=args.article_id)
                    for eid in m['evidence_ids'])]
            end = args.measurement_offset+args.measurement_limit
            from .evidence import snapshot
            current = snapshot(self.store.article(args.article_id))['fingerprint']
            result.update(structured_measurements=[{**m, 'current_source':current in m.get('source_fingerprints', [])}
                for m in measurements[args.measurement_offset:end]],
                next_measurement_offset=end if end<len(measurements) else None, total_measurements=len(measurements))
            return result
        if name == 'analyze_article':
            args = AnalyzeArgs.model_validate(raw)
            if args.replace_claims:
                return self.replace_claims(args, raw, deadline, **kwargs)
            if args.action == 'index':
                return {'indexes': [passage_index(self.state, self.store.article(aid), section=args.section_filter,
                    offset=args.index_offset, limit=args.index_limit) for aid in args.article_ids]}
            if args.action == 'review':
                if len(args.article_ids) != 1 or args.coverage_review is None:
                    raise ValueError('review exige un article et coverage_review.')
                article = self.store.article(args.article_ids[0])
                result = save_review(self.state, article, args.coverage_review)
                self.save()
                return {'saved': True, 'coverage_review': result, 'coverage': reading_status(self.state, article)}
            measurements = validate_measurements(self.state, self.store.article(args.article_ids[0]), args.measurements) if args.action=='save' else {}
            if args.action == 'read' and args.evidence_ids and args.table_number is not None:
                # Exact IDs are exact: a table filter must not hide a requested caption
                # or note that is not itself a numbered table row.
                raw = {**raw, 'table_number': None}
            token = _read_section.set(args.section_filter if args.action == 'read' and not args.evidence_ids else None)
            try:
                result = super().execute(name, raw, deadline, **kwargs)
            finally:
                _read_section.reset(token)
            if args.action == 'read' and args.table_number is not None and not args.evidence_ids:
                from .evidence import passages
                for entry in result.get('reading', []):
                    if entry.get('passages_total') == 0 and not entry.get('passages'):
                        rows = passages(self.store.article(entry['article']['id']))
                        entry['available_table_numbers'] = sorted({str(r['table']['number'])
                            for r in rows if (r.get('table') or {}).get('number') is not None})
                        entry['guidance'] = (f"Aucun tableau numéroté {args.table_number} dans l'extraction ; "
                            "utiliser available_table_numbers, passage_query ou evidence_ids.")
            if args.action in {'save', 'repair'} and result.get('saved'):
                article = self.store.article(result['article_id'])
                if measurements:
                    article.analysis.setdefault('structured_measurements', {}).update(measurements)
                    result['measurements_saved'] = len(measurements)
                if args.coverage_review:
                    try:
                        save_review(self.state, article, args.coverage_review)
                    except ValueError as exc:
                        # A valid scientific card survives an insufficient review.
                        result['coverage_rejected'] = str(exc)
                result['coverage'] = reading_status(self.state, article)
                from .science import analysis_depth
                depth = analysis_depth(self.state, article)
                result['analysis_depth'] = {k: depth[k] for k in ('provenance', 'coverage', 'depth', 'question_sufficient', 'label')}
                self.save()
            return annotate_depth(self.state, self.registry, result)
        if name == "get_fulltext":
            from .tools.extraction import previous_chunks
            article = self.registry.get(raw.get("article_id"))
            token = previous_chunks.set(article.fulltext.get("chunks", ()) if article else ())
            try:
                return super().execute(name, raw, deadline, **kwargs)
            finally:
                previous_chunks.reset(token)
        if name != "get_article":
            return super().execute(name, raw, deadline, **kwargs)
        return self.fetch_notices(raw, deadline, **kwargs)

    def fetch_notices(self, raw, deadline, **kwargs):
        """get_article, one work at a time for a batch (a failed lookup never hides the others)."""
        from .stops import StopResearch
        name = "get_article"
        if len(raw.get("identifiers", [])) < 2:
            return super().execute(name, raw, deadline, **kwargs)
        outputs, errors = [], []
        for identifier in raw["identifiers"]:
            try:
                outputs.append(super().execute(name, {**raw, "identifiers": [identifier]}, deadline, **kwargs))
            except StopResearch:
                raise
            except Exception as exc:
                errors.append({"identifier": identifier, "error_type": type(exc).__name__, "retry_required": True})
        result = copy.deepcopy(outputs[-1]) if outputs else {}
        result["articles"] = [a for output in outputs for a in output.get("articles", [])]
        result["errors"] = errors
        self.save()
        return result

    def replace_claims(self, args, raw, deadline, **kwargs):
        if args.action != 'save' or len(args.article_ids) != 1 or args.analysis is None:
            raise ValueError('Replacing claims requires one article and a new saved analysis')
        article = self.store.article(args.article_ids[0])
        previous = copy.deepcopy(article.analysis)
        previous_basis, previous_status = article.analysis_basis, article.access_status
        updated = copy.deepcopy(previous)
        aliases = {'scientific_problem': 'problem', 'research_objective': 'objective', 'proposed_method': 'method',
            'main_results': 'results', 'contributions': 'contribution', 'relevance_to_current_topic': 'relevance'}
        for replacement in args.replace_claims:
            field = aliases.get(replacement.field, replacement.field)
            new_claims = getattr(args.analysis, field, None)
            if not isinstance(new_claims, list) or not new_claims:
                raise ValueError('A replacement requires new claims in the same scientific field')
            matches = [c for c in updated.get(field, []) if c.get('statement') == replacement.statement
                and set(c.get('evidence_ids', [])) == set(replacement.evidence_ids)]
            if len(matches) != 1:
                raise ValueError('The claim to replace must be identified without ambiguity')
            claim = matches[0]
            updated[field] = [c for c in updated[field] if c is not claim]
            for alias, canonical in aliases.items():
                if canonical == field and alias in updated:
                    updated[alias] = copy.deepcopy(updated[field])
            updated.setdefault('superseded_claims', []).append({'field': field, 'claim': copy.deepcopy(claim)})
        article.analysis = updated
        try:
            result = self.execute('analyze_article', {**raw, 'replace_claims': []}, deadline, **kwargs)
            if not result.get('saved'):
                raise ValueError('The replacement analysis was not saved')
            return result
        except BaseException:
            article.analysis = previous
            article.analysis_basis, article.access_status = previous_basis, previous_status
            self.save()
            raise

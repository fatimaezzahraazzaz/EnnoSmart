"use client"

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { ArrowUp, BookOpen, Check, ExternalLink, Loader2, Plus, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import ScholarMarkdown from "@/components/ennosmart/ennoscholar-v2-markdown"
import { cancelRun, createSession, getRun, getSession, getArticlePage, uploadPdf, isRunning, listSessions, safeUrl, selectArticle, sendMessage,
  type Article, type Run, type Session, type SessionSummary } from "@/lib/scholar-v31"

const toolLabels: Record<string, string> = {
  web_search: "Recherche Web", search_scientific: "Recherche académique", get_articles: "Lecture des notices",
  search_collected_articles: "Exploration du corpus", verify_bibliography: "Vérification bibliographique",
  get_fulltext: "Lecture d’une publication", analyze_articles: "Analyse d’une publication", compare_articles: "Comparaison des publications",
  find_similar_articles: "Recherche de travaux connexes", update_research_state: "Mise à jour du contexte", record_candidate_decisions: "Comparaison des candidats",
  identify_research_gaps: "Identification des lacunes", finalize_response: "Vérification de la réponse",
  responses: "Traitement de la demande", limits: "Arrêt de la recherche",
}
const accessLabels = { metadata_only: "Métadonnées récupérées", abstract_only: "Abstract récupéré", fulltext: "Texte complet extrait" }
const basisLabels = { metadata: "Métadonnées uniquement", abstract: "Abstract analysé", fulltext: "Passages du texte complet analysés" }
const describeClaim = (claim: NonNullable<Article["analysis"]["method"]>[number]) => `${claim.origin === "inference" ? "Inférence : " : ""}${claim.statement}`

function ArticleCard({ article, onSelect, saving, rank, onAction, onCompare, onUpload, comparing, busy }: { article: Article; onSelect: () => void; saving: boolean; rank?: number; onAction: (message: string) => void; onCompare: () => void; onUpload: () => void; comparing: boolean; busy: boolean }) {
  const link = safeUrl(article.doi ? `https://doi.org/${article.doi}` : article.url)
  return <article className="min-w-0 rounded-xl border border-border bg-card p-4">
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="min-w-0 flex-1">
        <h3 className="break-words font-semibold leading-6">{rank ? `${rank}. ` : ""}{link ? <a href={link} target="_blank" rel="noopener noreferrer" className="text-brand hover:underline">{article.title}</a> : article.title}</h3>
        <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">{article.authors.join(", ") || "Auteurs non disponibles"} — {article.year || "Année non disponible"}</p>
      </div>
      {article.relevance_tag && <span className="rounded-full bg-brand/10 px-2 py-1 text-xs font-medium text-brand">{article.relevance_tag}</span>}
    </div>
    <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
      <span>{article.verified ? "Référence vérifiée" : "Référence à vérifier"}</span><span>·</span>
      <span>{accessLabels[article.access_level]}</span><span>·</span>
      <span>{article.analysis_basis ? basisLabels[article.analysis_basis] : "Pas encore analysé"}</span>
      {article.publisher_access !== "unknown" && <><span>·</span><span>{article.publisher_access === "paywalled" ? "Éditeur payant" : "Éditeur en accès libre"}</span></>}
    </div>
    {article.analysis.method?.length ? <p className="mt-3 text-sm leading-6">{article.analysis.method.map(describeClaim).join(" ")}</p> : null}
    {article.analysis.relevance?.length ? <p className="mt-2 text-sm leading-6"><span className="font-medium">Pertinence. </span>{article.analysis.relevance.map(describeClaim).join(" ")}</p> : null}
    {article.analysis.limitations?.length ? <p className="mt-2 text-sm leading-6 text-muted-foreground"><span className="font-medium">Limites. </span>{article.analysis.limitations.map(describeClaim).join(" ")}</p> : null}
    {article.analysis_basis && <details className="mt-3 text-sm">
      <summary className="cursor-pointer py-2 font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">Analyse scientifique détaillée</summary>
      <dl className="mt-2 space-y-3">{(["problem", "objective", "contribution", "results", "generalization", "open_questions"] as const).map(key => {
        const labels = { problem: "Problème étudié", objective: "Objectif", contribution: "Contribution", results: "Résultats démontrés", generalization: "Généralisation", open_questions: "Questions ouvertes" }
        return article.analysis[key]?.length ? <div key={key}><dt className="font-medium">{labels[key]}</dt><dd className="mt-1 leading-6 text-muted-foreground">{article.analysis[key]?.map(describeClaim).join(" ")}</dd></div> : null
      })}</dl>
    </details>}
    {article.analysis.method_family && <p className="mt-2 text-xs text-muted-foreground">Famille méthodologique : {article.analysis.method_family}</p>}
    {article.analysis.fulltext_truncated && <p className="mt-2 text-xs text-muted-foreground">Analyse de passages sélectionnés du texte intégral.</p>}
    {article.metadata_errors.length > 0 && <p className="mt-2 text-xs text-muted-foreground">Vérification : {article.metadata_errors.join(" ; ")}</p>}
    {article.analysis.evidence?.length ? <details className="mt-3 text-xs text-muted-foreground">
      <summary className="cursor-pointer rounded py-2 focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">Passages utilisés et limites de lecture</summary>
      <div className="mt-2 space-y-3">{article.analysis.evidence.map(e => <blockquote key={e.evidence_id} className="border-l-2 border-brand/30 pl-3 leading-5">
        <p>{e.text}</p>{safeUrl(e.source_url) ? <a href={safeUrl(e.source_url)} target="_blank" rel="noopener noreferrer" className="text-brand underline">{e.section || "Publication"}{e.page ? `, page PDF ${e.page}` : ""}</a> : <p>{e.section || "PDF fourni"}{e.page ? `, page PDF ${e.page}` : ""}</p>}
        {e.source_version && <p className="mt-1 break-all">Version : {e.source_version}</p>}
      </blockquote>)}{article.analysis.analysis_limit && <p>{article.analysis.analysis_limit}</p>}</div>
    </details> : null}
    <div className="mt-3 flex flex-wrap items-center gap-3">
      <Button size="sm" variant={article.selected ? "secondary" : "outline"} disabled={saving || !article.verified} onClick={onSelect} aria-pressed={article.selected}>
        {saving ? <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" /> : article.selected ? <Check aria-hidden className="size-4" /> : <Plus aria-hidden className="size-4" />}
        {article.selected ? "Article conservé" : "Conserver cet article"}
      </Button>
      {article.fulltext_source && <a href={safeUrl(article.fulltext_source)} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 py-2 text-xs text-brand underline">Texte consulté <ExternalLink aria-hidden className="size-3" /></a>}
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(`Analyse l’article ${article.id} (${article.title}). Quel problème scientifique cherche-t-il à résoudre ? Distingue résultats démontrés, limites, questions ouvertes et inférences, avec les preuves exactes.`)}>Analyser</Button>
      <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAction(`Comprends la contribution de l’article ${article.id}, puis trouve des travaux similaires, complémentaires et plus récents. Explique chaque rapprochement scientifique.`)}>Travaux similaires</Button>
      <Button size="sm" variant="outline" disabled={busy} aria-pressed={comparing} onClick={onCompare}>{comparing ? "Ajouté à la comparaison" : "Comparer"}</Button>
      <Button size="sm" variant="ghost" disabled={busy} onClick={onUpload}>Ajouter son PDF</Button>
    </div>
  </article>
}

export default function EnnoScholarV31Chat({ userId }: { userId: number }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [draft, setDraft] = useState("")
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(true)
  const [sending, setSending] = useState(false)
  const [saving, setSaving] = useState<string | null>(null)
  const [showSaved, setShowSaved] = useState(false)
  const [showCandidates, setShowCandidates] = useState(false)
  const [visiblePapers, setVisiblePapers] = useState(24)
  const [corpusArticles, setCorpusArticles] = useState<Article[]>([])
  const [corpusTotal, setCorpusTotal] = useState(0)
  const [corpusQuery, setCorpusQuery] = useState("")
  const [corpusLoading, setCorpusLoading] = useState(false)
  const [compareIds, setCompareIds] = useState<string[]>([])
  const [uploading, setUploading] = useState(false)
  const pdfInput = useRef<HTMLInputElement>(null)
  const pdfTarget = useRef("")
  const bottom = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)
  const busy = sending || uploading || isRunning(run)
  const storageKey = `ennoscholar-v3-1-session-${userId}`

  async function openSession(id: string) {
    setError(""); setLoading(true)
    try {
      const result = await getSession(id)
      setSession(result); localStorage.setItem(storageKey, id)
      setRun(result.active_run_id ? await getRun(result.active_run_id) : null)
      setShowSaved(false)
    } catch (err) { setError(err instanceof Error ? err.message : "Conversation indisponible.") }
    finally { setLoading(false) }
  }

  useEffect(() => {
    let active = true
    listSessions().then(async results => {
      if (!active) return
      setSessions(results)
      const stored = localStorage.getItem(storageKey)
      const id = results.find(s => s.id === stored)?.id || results[0]?.id
      if (id) {
        const current = await getSession(id)
        if (!active) return
        setSession(current)
        if (current.active_run_id) {
          const status = await getRun(current.active_run_id)
          if (active) setRun(status)
        }
      }
    }).catch(err => { if (active) setError(err.message || "Impossible de charger les conversations.") })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [storageKey])

  useEffect(() => {
    if (!run || !isRunning(run)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const rid = run.id
    const poll = async () => {
      try {
        const current = await getRun(rid, controller.signal)
        if (controller.signal.aborted) return
        setRun(current)
        setError("")
        if (!isRunning(current)) {
          const [updated, list] = await Promise.all([getSession(current.session_id, controller.signal), listSessions()])
          if (controller.signal.aborted) return
          setSession(updated); setSessions(list)
          if (current.status === "failed") setError(current.answer)
          input.current?.focus()
          return
        }
      } catch (err) {
        if (controller.signal.aborted) return
        setError(err instanceof Error ? err.message : "Connexion interrompue. Nouvelle tentative en cours.")
      }
      timer = setTimeout(poll, 1500)
    }
    timer = setTimeout(poll, 500)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [run?.id, run?.status])

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "instant", block: "end" }) }, [session?.messages.length, run?.status])

  async function newConversation() {
    setLoading(true); setError("")
    try {
      const current = await createSession()
      setSession(current); setRun(null); setDraft(""); setShowSaved(false)
      localStorage.setItem(storageKey, current.id)
      setSessions(await listSessions())
      input.current?.focus()
    } catch (err) { setError(err instanceof Error ? err.message : "Création impossible.") }
    finally { setLoading(false) }
  }

  async function submit() {
    if (!draft.trim() || busy || loading) return
    setSending(true); setError("")
    const message = draft.trim()
    try {
      const current = session || await createSession()
      setSession(current); localStorage.setItem(storageKey, current.id)
      const result = await sendMessage(current.id, message)
      setRun(result); setDraft("")
      setSession(await getSession(current.id))
      setSessions(await listSessions())
    } catch (err) { setError(err instanceof Error ? err.message : "Envoi impossible. Votre demande reste dans le champ de saisie.") }
    finally { setSending(false) }
  }

  async function toggle(article: Article) {
    if (!session) return
    setSaving(article.id); setError("")
    try {
      const updated = await selectArticle(session.id, article.id, !article.selected)
      setSession(current => current ? { ...current, articles: current.articles.some(a => a.id === updated.id) ? current.articles.map(a => a.id === updated.id ? updated : a) : [...current.articles, updated] } : null)
      setCorpusArticles(current => current.map(a => a.id === updated.id ? updated : a))
      setSessions(await listSessions())
    } catch (err) { setError(err instanceof Error ? err.message : "Conservation impossible.") }
    finally { setSaving(null) }
  }

  const activity = [...(run?.activity || [])].reverse().find(a => a.status === "running")
  const selected = session?.articles.filter(a => a.selected) || []
  const recommended = run?.recommended_article_ids || session?.recommended_article_ids || []
  const ordered = [...(session?.articles || [])].sort((a, b) => {
    const ai = recommended.indexOf(a.id), bi = recommended.indexOf(b.id)
    return (ai < 0 ? 999 : ai) - (bi < 0 ? 999 : bi)
  })
  const papers = showSaved ? selected : showCandidates ? corpusArticles : recommended.length ? ordered.filter(a => recommended.includes(a.id)) : ordered
  useEffect(() => { setVisiblePapers(24); setShowCandidates(false); setCompareIds([]); setCorpusArticles([]); setCorpusQuery("") }, [session?.id])
  useEffect(() => {
    if (!session || !showCandidates || showSaved) return
    const controller = new AbortController()
    const sid = session.id
    const timer = setTimeout(() => {
      setCorpusLoading(true)
      getArticlePage(sid, 0, corpusQuery, controller.signal).then(page => {
        if (!controller.signal.aborted) { setCorpusArticles(page.articles); setCorpusTotal(page.total) }
      }).catch(err => { if (!controller.signal.aborted) setError(err.message || "Corpus indisponible.") })
        .finally(() => { if (!controller.signal.aborted) setCorpusLoading(false) })
    }, 250)
    return () => { clearTimeout(timer); controller.abort() }
  }, [session?.id, session?.articles_total, showCandidates, showSaved, corpusQuery])
  async function moreCandidates() {
    if (!session || corpusLoading) return
    setCorpusLoading(true)
    try { const page = await getArticlePage(session.id, corpusArticles.length, corpusQuery); setCorpusArticles(current => [...current, ...page.articles]); setCorpusTotal(page.total) }
    catch (err) { setError(err instanceof Error ? err.message : "Corpus indisponible.") }
    finally { setCorpusLoading(false) }
  }
  function prepareAction(message: string) { setDraft(message); input.current?.focus() }
  function choosePdf(articleId = "") { pdfTarget.current = articleId; pdfInput.current?.click() }
  async function importPdf(file: File) {
    if (file.size > 15 * 1024 * 1024) { setError("Le PDF doit faire au maximum 15 Mo."); return }
    setUploading(true); setError("")
    try {
      const current = session || await createSession()
      localStorage.setItem(storageKey, current.id)
      const article = await uploadPdf(current.id, file, pdfTarget.current)
      setSession(await getSession(current.id)); setSessions(await listSessions())
      setDraft(previous => previous.trim() ? `${previous}\nArticle importé : ${article.id}` : `Analyse le problème scientifique, les résultats démontrés et les limites de l’article importé ${article.id}. Vérifie son identité et cite les passages avec leurs pages.`)
      input.current?.focus()
    } catch (err) { setError(err instanceof Error ? err.message : "Import PDF impossible.") }
    finally { setUploading(false); if (pdfInput.current) pdfInput.current.value = "" }
  }

  return <div className="flex min-h-[75dvh] min-w-0 flex-col gap-4 lg:flex-row">
    <aside className="shrink-0 lg:w-56" aria-label="Conversations scientifiques">
      <Button variant="outline" className="w-full" onClick={newConversation} disabled={busy || loading}><Plus aria-hidden className="size-4" />Nouvelle conversation</Button>
      <details className="mt-3 rounded-xl border border-border p-3" open>
        <summary className="cursor-pointer text-sm font-medium">Historique</summary>
        <div className="mt-2 max-h-44 space-y-1 overflow-y-auto lg:max-h-[55dvh]">{sessions.map(s => <button key={s.id} disabled={busy || loading} onClick={() => openSession(s.id)} aria-current={session?.id === s.id ? "page" : undefined}
          className={`w-full rounded-lg px-3 py-2 text-left text-sm hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand disabled:opacity-50 ${session?.id === s.id ? "bg-brand/10 text-brand" : "text-muted-foreground"}`}>
          <span className="line-clamp-2 break-words">{s.title}</span><span className="mt-1 block text-xs">{s.selected_count} article{s.selected_count > 1 ? "s" : ""} conservé{s.selected_count > 1 ? "s" : ""}</span>
        </button>)}{sessions.length === 0 && <p className="py-3 text-xs text-muted-foreground">Vos conversations apparaîtront ici.</p>}</div>
      </details>
      <Link href="/ennoscholar-v2" className="mt-3 block rounded-lg py-2 text-center text-xs text-muted-foreground underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">Ouvrir EnnoScholar V2</Link>
      <Link href="/ennoscholar-v3" className="block rounded-lg py-2 text-center text-xs text-muted-foreground underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand">Ouvrir EnnoScholar V3</Link>
    </aside>
    <section className="flex min-w-0 flex-1 flex-col rounded-2xl border border-border bg-background" aria-label="Chat EnnoScholar V3.1">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3 sm:px-6">
        <h1 className="flex items-center gap-2 text-base font-semibold"><BookOpen aria-hidden className="size-5 text-brand" /> EnnoScholar V3.1</h1>
        <Button variant="ghost" size="sm" onClick={() => setShowSaved(!showSaved)} aria-pressed={showSaved}>Articles conservés ({selected.length})</Button>
      </div>
      <div className="max-h-[65dvh] min-h-64 flex-1 overflow-y-auto px-4 py-6 sm:px-6">
        {loading && <p role="status" className="text-sm text-muted-foreground">Chargement de la conversation…</p>}
        {!loading && !session?.messages.length && <div className="mx-auto max-w-2xl py-10 text-center">
          <BookOpen aria-hidden className="mx-auto mb-5 size-9 text-brand" />
          <h2 className="text-xl font-semibold sm:text-2xl">Bonjour, je suis EnnoScholar V3.1.</h2>
          <p className="mt-4 text-sm leading-7 text-muted-foreground sm:text-base">Décrivez votre sujet scientifique, votre verrou technologique ou partagez un article. Je peux rechercher, analyser et comparer la littérature scientifique pour vous aider à construire votre état de l’art.</p>
        </div>}
        <div className="mx-auto max-w-3xl space-y-6">{session?.messages.map(m => <div key={m.id} className={m.role === "user" ? "ml-auto max-w-[92%] rounded-2xl bg-brand/10 px-4 py-3" : "min-w-0"}>
          <p className="mb-2 text-xs font-semibold text-muted-foreground">{m.role === "user" ? "Vous" : "EnnoScholar V3.1"}</p>
          {m.role === "user" ? <p className="whitespace-pre-wrap break-words text-sm leading-7">{m.content}</p> : <ScholarMarkdown text={m.content} citations={m.citations || []} />}
          {m.partial && <p className="mt-2 text-xs text-muted-foreground">Recherche partielle — consultez les limites indiquées dans la réponse.</p>}
        </div>)}</div>
        {busy && <div role="status" aria-live="polite" className="mx-auto mt-6 flex max-w-3xl items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 motion-safe:animate-spin" />{run?.cancel_requested ? "Annulation demandée…" : activity ? `${toolLabels[activity.tool] || "Opération scientifique"} en cours…` : run?.status === "queued" ? "Demande en attente…" : "Envoi de la demande…"}
        </div>}
        {!busy && showCandidates && !showSaved && <div className="mx-auto mt-6 max-w-3xl">
          <label htmlFor="v31-corpus-search" className="mb-2 block text-sm font-medium">Rechercher dans le corpus ({session?.articles_total || 0} articles)</label>
          <input id="v31-corpus-search" value={corpusQuery} onChange={e => setCorpusQuery(e.target.value)} placeholder="Concept, titre ou DOI" className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-brand" />
          {corpusLoading && <p role="status" className="mt-2 text-sm text-muted-foreground">Chargement du corpus…</p>}
          {!corpusLoading && corpusArticles.length === 0 && <p className="mt-2 text-sm text-muted-foreground">Aucun article trouvé dans le corpus consulté.</p>}
        </div>}
        {!busy && compareIds.length > 0 && <div className="mx-auto mt-4 flex max-w-3xl flex-wrap items-center gap-3">
          <p className="text-sm">{compareIds.length} article(s) pour la comparaison</p>
          <Button variant="outline" disabled={compareIds.length < 2} onClick={() => prepareAction(`Compare scientifiquement les articles ${compareIds.join(", ")}. Distingue problème, contribution, méthode, résultats, limites, généralisation et niveau de preuve, puis identifie les lacunes avec leur périmètre.`)}>Préparer la comparaison</Button>
          <Button variant="ghost" onClick={() => setCompareIds([])}>Effacer la sélection</Button>
        </div>}
        {!busy && papers.length > 0 && <div className="mx-auto mt-8 max-w-3xl space-y-3">
          <h2 className="text-sm font-semibold">{showSaved ? "Articles conservés pour votre état de l’art" : showCandidates ? "Corpus — références et pertinence à examiner" : recommended.length ? "Sélection classée par l’assistant" : "Articles collectés — classement à établir"}</h2>
          {(showCandidates && !showSaved ? papers : papers.slice(0, visiblePapers)).map(article => <ArticleCard key={article.id} article={article} rank={recommended.includes(article.id) ? recommended.indexOf(article.id) + 1 : undefined} onSelect={() => toggle(article)} saving={saving === article.id} busy={busy} onAction={prepareAction} onUpload={() => choosePdf(article.id)} comparing={compareIds.includes(article.id)} onCompare={() => setCompareIds(current => current.includes(article.id) ? current.filter(id => id !== article.id) : [...current.slice(-7), article.id])} />)}
          {showCandidates && !showSaved ? corpusArticles.length < corpusTotal && <Button variant="outline" disabled={corpusLoading} onClick={moreCandidates}>Afficher davantage ({corpusArticles.length}/{corpusTotal})</Button> : papers.length > visiblePapers && <Button variant="outline" onClick={() => setVisiblePapers(n => n + 24)}>Afficher davantage de publications</Button>}
        </div>}
        {!busy && !showSaved && Boolean(session?.articles_total) && <Button variant="ghost" className="mx-auto mt-4 block" onClick={() => { setShowCandidates(!showCandidates); setVisiblePapers(24) }}>{showCandidates ? "Revenir à la sélection" : `Explorer tout le corpus (${session?.articles_total})`}</Button>}
        {showSaved && selected.length === 0 && <p className="mt-6 text-center text-sm text-muted-foreground">Conservez un article pour le retrouver ici.</p>}
        {run?.activity.length ? <details className="mx-auto mt-6 max-w-3xl text-xs text-muted-foreground">
          <summary className="cursor-pointer py-2">Activité de recherche ({run.activity.length} opérations)</summary>
          <ol className="mt-2 space-y-2">{run.activity.map((a, i) => <li key={`${a.at}-${i}`}>{toolLabels[a.tool] || a.tool} — {a.status}{a.query ? ` : ${a.query}` : ""}{a.family ? ` (${a.family})` : ""}{a.found !== undefined ? ` · ${a.found} articles` : ""}</li>)}</ol>
          {run.usage?.estimated_cost_usd !== undefined && <p className="mt-3">Coût estimé : ${run.usage.estimated_cost_usd.toFixed(3)}{run.duration_seconds ? ` · ${Math.round(run.duration_seconds)} s` : ""}</p>}
        </details> : null}
        <div ref={bottom} />
      </div>
      <form className="border-t border-border p-4 sm:px-6" onSubmit={e => { e.preventDefault(); void submit() }}>
        {error && <p role="alert" className="mb-3 break-words text-sm text-destructive">{error}</p>}
        <label htmlFor="scholar-v3-1-input" className="sr-only">Votre question scientifique</label>
        <input ref={pdfInput} type="file" accept="application/pdf,.pdf" className="hidden" aria-label="Importer un article PDF" onChange={e => { const file = e.target.files?.[0]; if (file) void importPdf(file) }} />
        <Button type="button" variant="ghost" size="sm" disabled={busy || loading} onClick={() => choosePdf()}>{uploading ? "Import du PDF…" : "Joindre un article PDF"}</Button>
        <div className="flex items-end gap-3 rounded-xl border border-border bg-card p-3 focus-within:ring-2 focus-within:ring-brand/40">
          <textarea ref={input} id="scholar-v3-1-input" placeholder="Posez votre question scientifique..." value={draft} onChange={e => setDraft(e.target.value)} maxLength={30000} rows={3}
            className="max-h-56 min-h-20 min-w-0 flex-1 resize-y bg-transparent text-sm leading-6 outline-none disabled:opacity-60" disabled={busy}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); void submit() } }} />
          {isRunning(run) ? <Button type="button" size="icon" variant="outline" aria-label="Annuler la recherche" disabled={run?.cancel_requested} onClick={async () => {
            if (!run) return
            try { setRun(await cancelRun(run.id)) } catch (err) { setError(err instanceof Error ? err.message : "Annulation impossible.") }
          }}><Square aria-hidden className="size-4" /></Button> : <Button type="submit" size="icon" aria-label="Envoyer la question" disabled={busy || loading || !draft.trim()}><ArrowUp aria-hidden className="size-5" /></Button>}
        </div>
        <p className="mt-2 text-xs text-muted-foreground">Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne. Les limites de lecture sont indiquées pour chaque article.</p>
      </form>
    </section>
  </div>
}

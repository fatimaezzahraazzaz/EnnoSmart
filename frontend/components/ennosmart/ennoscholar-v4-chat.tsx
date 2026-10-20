"use client"

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import dynamic from "next/dynamic"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowUp, BookOpen, Check, Copy, FileText, Loader2, Menu, Paperclip, Plus, Square, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cancelRun, changeDocument, decideArticle, getArticlePage, getDocument, getRun, getSession, isRunning, listSessions, safeUrl, sendMessage, uploadDocument, type Article, type Decision, type Message, type Run, type ScientificDocument, type Session, type SessionSummary } from "@/lib/scholar-v4"

import SelectionPanel, { ArticleCard, decisionLabels } from "./scholar-v4-selection"
import ScholarV4StartDialog from "./scholar-v4-start-dialog"
import ScholarV4ProjectContext from "./scholar-v4-project-context"

const ScholarV4Workspace = dynamic(() => import("./scholar-v4-workspace"), { loading: () => <p role="status">Chargement de l’espace de rédaction…</p> })

const activityLabels: Record<string, string> = {
  search_scientific: "Recherche scientifique en cours…", web_search: "Recherche sur le Web…",
  get_article: "Vérification des publications…", get_fulltext: "Recherche des textes accessibles…",
  analyze_article: "Analyse des passages consultés…", compare_articles: "Comparaison des travaux…",
  manage_outline: "Mise à jour du plan…", search_for_section: "Recherche pour cette section…",
  write_section: "Préparation de la rédaction…", finalize_response: "Vérification des sources…",
  review_literature_coverage: "Vérification de la couverture scientifique…", review_document: "Revue scientifique du document…", create_scientific_visual: "Préparation d’une figure scientifique…",
}

const visibleResponse = (content: string) => content.replace(/ ?cite[^]*/g, "")

function Markdown({ content }: { content: string }) {
  return <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    a: ({ href, children }) => {
      const url = safeUrl(href || "")
      return url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4 break-words">{children}</a> : <span>{children}</span>
    },
    table: ({ children }) => <div className="my-4 max-w-full overflow-x-auto rounded-xl border"><table className="w-full min-w-[480px] text-left text-sm">{children}</table></div>,
    th: ({ children }) => <th className="border-b bg-muted/60 px-4 py-3 font-semibold">{children}</th>,
    td: ({ children }) => <td className="border-b px-4 py-3 align-top">{children}</td>,
    p: ({ children }) => <p className="my-3 leading-7">{children}</p>,
    h1: ({ children }) => <h1 className="mb-3 mt-6 text-xl font-semibold">{children}</h1>,
    h2: ({ children }) => <h2 className="mb-3 mt-6 text-lg font-semibold">{children}</h2>,
    h3: ({ children }) => <h3 className="mb-2 mt-5 font-semibold">{children}</h3>,
    ul: ({ children }) => <ul className="my-3 list-disc space-y-2 pl-6">{children}</ul>,
    ol: ({ children }) => <ol className="my-3 list-decimal space-y-2 pl-6">{children}</ol>,
    pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-xl bg-muted p-4 text-sm">{children}</pre>,
    blockquote: ({ children }) => <blockquote className="my-4 border-l-4 border-primary/40 pl-4 text-muted-foreground">{children}</blockquote>,
  }}>{visibleResponse(content)}</ReactMarkdown>
}

const accessLabels: Record<string, string> = { metadata_only: "Métadonnées uniquement", abstract_only: "Abstract disponible",
  fulltext_available: "Lien intégral repéré, lecture non confirmée", fulltext_extracted: "Texte extrait", fulltext_analyzed: "Analyse sauvegardée sur le texte accessible",
  access_blocked: "Accès bloqué", extraction_failed: "Extraction non aboutie" }

const libraryLabels: Record<string, { title: string; note: string }> = {
  retained_complementary: { title: "Références complémentaires retenues", note: "Travaux jugés pertinents par l’assistant et conservés pour un approfondissement. Leur identité et leur niveau de lecture sont indiqués séparément." },
  unexamined: { title: "Candidats non évalués", note: "Notices découvertes dont la pertinence scientifique n’a pas encore été évaluée. Leur présence ne constitue pas une recommandation." },
  examined_pending: { title: "Candidats à confirmer", note: "Notices examinées dont la sélection scientifique reste à confirmer." },
  rejected: { title: "Notices écartées", note: "Travaux écartés de la sélection scientifique, conservés pour la traçabilité." },
  on_hold: { title: "Publications en attente", note: "Mises en attente par le consultant : ni retenues, ni écartées. Leurs analyses sont conservées." },
  all: { title: "Autres notices conservées", note: "Bibliothèque historique : ces notices ne sont pas toutes des références recommandées ou des textes consultés." },
}

function LibraryPublications({ sessionId, excludeIds, count, category, onExplore, onChanged }: { sessionId: string; excludeIds: string[]; count: number; category: string; onExplore: (article: Article) => void; onChanged: () => Promise<void> }) {
  const [articles, setArticles] = useState<Article[]>([])
  const [decided, setDecided] = useState<Record<string, Decision>>({})
  const [card, setCard] = useState("")
  const [total, setTotal] = useState(count)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [loaded, setLoaded] = useState(false)
  async function decide(article: Article, decision: Decision) {
    setError("")
    try { await decideArticle(sessionId, article.id, decision); setDecided(current => ({ ...current, [article.id]: decision })); await onChanged() }
    catch (e) { setError(e instanceof Error ? e.message : "Décision non enregistrée.") }
  }
  async function load(offset: number) {
    setLoading(true); setError("")
    try {
      const page = await getArticlePage(sessionId, offset, "", undefined, excludeIds, category)
      setArticles(current => offset ? [...current, ...page.articles] : page.articles)
      setTotal(page.total); setLoaded(true)
    } catch (e) { setError(e instanceof Error ? e.message : "Bibliothèque indisponible.") }
    finally { setLoading(false) }
  }
  return <details className="my-4 rounded-xl border p-3 text-sm" onToggle={e => { if (e.currentTarget.open && !loaded && !loading) load(0) }}>
    <summary className="cursor-pointer font-medium">{libraryLabels[category].title} ({total})</summary>
    <p className="mt-2 text-muted-foreground">{libraryLabels[category].note}</p>
    <ul className="mt-3 space-y-4">{articles.map(a => {
      const url = safeUrl(a.doi ? `https://doi.org/${a.doi}` : a.arxiv_id ? `https://arxiv.org/abs/${a.arxiv_id}` : a.url)
      return <li key={a.id} className="border-t pt-3"><p className="font-medium">{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{a.title}</a> : a.title}</p><p className="mt-1 text-xs text-muted-foreground">{a.year || "Année non confirmée"} · {accessLabels[a.access_status] || a.access_status}{a.publisher_access === "paywalled" ? " · Éditeur payant" : ""}{!a.verified ? " · Identité bibliographique à vérifier" : ""}</p><div className="mt-1 flex flex-wrap gap-1">{(category === "rejected" || category === "on_hold" ? ["restore", "accept"] as Decision[] : ["accept", "hold", "reject"] as Decision[]).map(d => <Button key={d} type="button" size="sm" variant={decided[a.id] === d ? "secondary" : "ghost"} onClick={() => decide(a, d)}>{decisionLabels[d]}</Button>)}<Button type="button" variant="ghost" size="sm" aria-expanded={card === a.id} onClick={() => setCard(card === a.id ? "" : a.id)}>Fiche et preuves</Button><Button type="button" variant="ghost" size="sm" onClick={() => onExplore(a)}>Approfondir cette publication</Button></div>{card === a.id && <ArticleCard sid={sessionId} articleId={a.id} />}</li>
    })}</ul>
    {loading && <p role="status" className="mt-3">Chargement de la bibliothèque…</p>}
    {error && <p role="alert" className="mt-3 text-destructive">{error} <button className="underline" onClick={() => load(articles.length)}>Réessayer</button></p>}
    {!loading && loaded && articles.length < total && <Button variant="outline" size="sm" className="mt-3" onClick={() => load(articles.length)}>Voir davantage de publications</Button>}
  </details>
}

function Response({ message, sessionId, onExplore, onChanged }: { message: Message; sessionId: string; onExplore: (article: Article) => void; onChanged: () => Promise<void> }) {
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  if (message.role === "user") return <div className="ml-auto max-w-[90%] rounded-2xl bg-primary/10 px-5 py-3 whitespace-pre-wrap break-words text-sm sm:max-w-[80%]">{message.content}</div>
  return <article className="min-w-0 text-sm sm:text-base" aria-label="Réponse EnnoScholar">
    <Markdown content={message.content} />
    {message.research_status === "incomplete" && <p className="my-3 text-xs text-muted-foreground">La couverture scientifique reste à compléter ; les limites sont précisées dans la réponse.</p>}
    {message.citations?.some(c => c.cited) && <details className="my-3 text-sm"><summary className="cursor-pointer text-muted-foreground">Sources utilisées dans cette réponse ({message.citations.filter(c => c.cited).length})</summary><ul className="mt-2 space-y-2">{message.citations.filter(c => c.cited).map((c, i) => safeUrl(c.url) && <li key={`${c.url}-${i}`}><a href={safeUrl(c.url)} target="_blank" rel="noopener noreferrer" className="text-primary underline break-words">{c.title || c.url}</a></li>)}</ul></details>}
    {(message.library_counts && Object.values(message.library_counts).some(Boolean) || Boolean(message.complementary_count)) && <details className="my-3 text-sm"><summary className="cursor-pointer text-muted-foreground">Bibliothèque et autres notices</summary><div className="mt-2">{message.library_counts ? ["retained_complementary", "unexamined", "examined_pending", "on_hold", "rejected"].map(category => Boolean(message.library_counts![category]) && <LibraryPublications key={category} sessionId={sessionId} excludeIds={message.primary_article_ids || []} count={message.library_counts![category]} category={category} onExplore={onExplore} onChanged={onChanged} />) : <LibraryPublications sessionId={sessionId} excludeIds={message.primary_article_ids || []} count={message.complementary_count!} category="all" onExplore={onExplore} onChanged={onChanged} />}</div></details>}
    <Button variant="ghost" size="sm" aria-label="Copier la réponse" onClick={async () => { try { await navigator.clipboard.writeText(visibleResponse(message.content)); setCopied(true); setCopyError(false); setTimeout(() => setCopied(false), 2000) } catch { setCopyError(true) } }}>{copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />} {copied ? "Copié" : "Copier"}</Button>
    {copyError && <span role="alert" className="text-sm text-destructive">Copie indisponible dans ce navigateur.</span>}
  </article>
}

export default function EnnoScholarV4Chat({ userId, onOpenAppMenu }: { userId: number; onOpenAppMenu: () => void }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [pollRetry, setPollRetry] = useState(0)
  const [workspaceOpen, setWorkspaceOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [startOpen, setStartOpen] = useState(false)
  const [documentSummary, setDocumentSummary] = useState<ScientificDocument | null>(null)
  const viewport = useRef<HTMLDivElement>(null)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const requestEpoch = useRef(0)
  const storageKey = `ennoscholar-v4-session:${userId}`
  const running = isRunning(run)

  const openSession = useCallback(async (id: string) => {
    const epoch = ++requestEpoch.current
    setLoading(true); setError("")
    try {
      const current = await getSession(id)
      if (epoch !== requestEpoch.current) return
      setSession(current); setRun(null); setDraft("")
      localStorage.setItem(storageKey, id)
      if (current.active_run_id) {
        const active = await getRun(current.active_run_id)
        if (epoch === requestEpoch.current) setRun(active)
      }
    } catch (e) { if (epoch === requestEpoch.current) setError(e instanceof Error ? e.message : "Conversation inaccessible.") }
    finally { if (epoch === requestEpoch.current) setLoading(false) }
  }, [storageKey])

  useEffect(() => {
    let active = true
    setSession(null); setRun(null); setLoading(true)
    listSessions().then(async (items) => {
      if (!active) return
      setSessions(items)
      const saved = localStorage.getItem(storageKey)
      const id = items.find(s => s.id === saved)?.id || items[0]?.id
      if (id) await openSession(id)
      else setStartOpen(true)
    }).catch(e => { if (active) setError(e instanceof Error ? e.message : "Historique indisponible.") }).finally(() => { if (active) setLoading(false) })
    return () => { active = false; requestEpoch.current++ }
  }, [storageKey, openSession])

  useEffect(() => {
    if (!run || !isRunning(run)) return
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout>
    const epoch = requestEpoch.current
    async function poll() {
      try {
        const current = await getRun(run!.id, controller.signal)
        if (controller.signal.aborted || epoch !== requestEpoch.current) return
        setError("")
        if (isRunning(current)) { setRun(current); timer = setTimeout(poll, 1200) }
        else {
          const updated = await getSession(current.session_id, controller.signal)
          if (controller.signal.aborted || epoch !== requestEpoch.current) return
          // Complete the final read before changing the effect dependency `running`.
          // Otherwise cleanup aborts this request as soon as the terminal run renders.
          setSession(updated); setRun(current); setSessions(await listSessions())
        }
      } catch (e) {
        if (!controller.signal.aborted && epoch === requestEpoch.current) {
          setError(e instanceof Error ? e.message : "Connexion interrompue. Le travail reste conservé.")
          timer = setTimeout(poll, 5000)
        }
      }
    }
    timer = setTimeout(poll, 600)
    return () => { controller.abort(); clearTimeout(timer) }
  }, [run?.id, running, pollRetry])

  useEffect(() => {
    if (!session) { setDocumentSummary(null); return }
    const controller = new AbortController()
    getDocument(session.id, controller.signal).then(document => {
      if (controller.signal.aborted) return
      setDocumentSummary(document)
    }).catch(() => { if (!controller.signal.aborted) setDocumentSummary(null) })
    return () => controller.abort()
  }, [session?.id, session?.updated_at])

  useLayoutEffect(() => {
    const field = textarea.current
    if (!field) return
    field.style.height = "0px"
    field.style.height = `${Math.min(field.scrollHeight, 180)}px`
    field.style.overflowY = field.scrollHeight > 180 ? "auto" : "hidden"
  }, [draft])

  useEffect(() => {
    if (!historyOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setHistoryOpen(false) }
    window.addEventListener("keydown", close)
    return () => window.removeEventListener("keydown", close)
  }, [historyOpen])

  useEffect(() => { viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "instant" }) }, [session?.messages.length, running])

  async function newConversation() {
    setHistoryOpen(false); setStartOpen(true)
  }

  async function created(current: Session) {
    requestEpoch.current++; setSession(current); setRun(null); setDraft(""); setError("")
    localStorage.setItem(storageKey, current.id)
    try { setSessions(await listSessions()) } catch { setSessions(items => [current, ...items]) }
  }

  async function send() {
    if (!draft.trim() || busy || running || loading) return
    if (!session) { setStartOpen(true); return }
    const message = draft.trim()
    setBusy(true); setError("")
    try {
      const current = session
      setSession(current); localStorage.setItem(storageKey, current.id)
      const started = await sendMessage(current.id, message)
      setRun(started); setDraft("")
      setSession(await getSession(current.id)); setSessions(await listSessions())
    } catch (e) { setError(e instanceof Error ? e.message : "Envoi impossible. Votre message est conservé dans le champ de saisie.") }
    finally { setBusy(false) }
  }

  async function upload(file: File) {
    if (!session) { setStartOpen(true); return }
    if (file.size > 15 * 1024 * 1024) { setError("Document limité à 15 Mo."); return }
    setBusy(true); setError("")
    try {
      const current = session
      setSession(current); localStorage.setItem(storageKey, current.id)
      const article = await uploadDocument(current.id, file)
      setSession(await getSession(current.id)); setSessions(await listSessions())
      setDraft(`Analyse le document que je viens de déposer : ${article.title}. Explique son problème scientifique, sa méthode, ses résultats et ses limites.`)
    } catch (e) { setError(e instanceof Error ? e.message : "Import impossible.") }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = "" }
  }

  async function approvePlan() {
    if (!session || !documentSummary || busy || running) return
    setBusy(true); setError("")
    try {
      await changeDocument(session.id, {action: "approve_outline", expected_version: documentSummary.outline.version || 0})
      const [updated, document] = await Promise.all([getSession(session.id), getDocument(session.id)])
      setSession(updated); setDocumentSummary(document)
    } catch (e) { setError(e instanceof Error ? e.message : "Validation du plan indisponible.") }
    finally { setBusy(false) }
  }

  async function refreshSession() {
    if (!session) return
    const epoch = requestEpoch.current
    const [updated, document] = await Promise.all([getSession(session.id), getDocument(session.id)])
    if (epoch === requestEpoch.current) { setSession(updated); setDocumentSummary(document) }
  }
  const askInChat = (text: string) => { setDraft(text); textarea.current?.focus() }

  const activity = run?.activity?.filter(e => e.status === "running").at(-1)
  const savedSections = documentSummary?.outline.sections?.filter(section => section.drafts.length).length || 0
  // The workspace opens as soon as a plan exists, so the plan can be edited before drafting.
  const hasPlan = Boolean(documentSummary?.outline.sections?.length)
  return <div className="relative flex h-full min-h-0 w-full flex-col bg-background">
    <ScholarV4StartDialog open={startOpen} onOpenChange={setStartOpen} onCreated={created} />
    <header className="flex h-12 shrink-0 items-center gap-2 border-b px-3 sm:px-4">
      <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0" aria-label="Ouvrir l’historique" aria-expanded={historyOpen} onClick={() => setHistoryOpen(value => !value)}><Menu aria-hidden="true" className="size-4" /></Button>
      <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">EnnoScholar</h1>
      <Button type="button" variant="ghost" size="sm" className="h-8 gap-1.5 px-2" onClick={newConversation} disabled={busy || loading} aria-label="Nouvelle conversation"><Plus aria-hidden="true" className="size-4" /><span className="hidden sm:inline">Nouvelle conversation</span></Button>
      {hasPlan && <Button type="button" variant="ghost" size="icon" className="size-8 shrink-0" aria-label={workspaceOpen ? "Masquer le document" : "Afficher le document"} aria-pressed={workspaceOpen} onClick={() => setWorkspaceOpen(value => !value)}><FileText aria-hidden="true" className="size-4" /></Button>}
    </header>
    {session?.mode === "diagnostic_linked" && session.diagnostic_context && <ScholarV4ProjectContext session={session} disabled={busy || running} onChanged={setSession} />}
    {historyOpen && <div className="absolute inset-0 z-50 flex" role="presentation"><button type="button" className="absolute inset-0 bg-foreground/20" aria-label="Fermer l’historique" onClick={() => setHistoryOpen(false)} /><aside className="relative flex h-full w-[min(85vw,320px)] flex-col border-r bg-background shadow-xl" aria-label="Historique des conversations"><div className="flex h-12 shrink-0 items-center justify-between border-b px-3"><strong className="text-sm">Historique</strong><Button type="button" size="icon" variant="ghost" className="size-8" aria-label="Fermer l’historique" onClick={() => setHistoryOpen(false)}><X aria-hidden="true" className="size-4" /></Button></div><div className="min-h-0 flex-1 space-y-1 overflow-y-auto p-2">{sessions.map(item => <button key={item.id} type="button" aria-current={item.id === session?.id ? "page" : undefined} className={`block w-full truncate rounded-lg px-3 py-2 text-left text-sm hover:bg-muted ${item.id === session?.id ? "bg-muted font-medium" : ""}`} onClick={() => {setHistoryOpen(false);openSession(item.id)}}>{item.title}</button>)}{!sessions.length && <p className="p-3 text-sm text-muted-foreground">Aucune conversation enregistrée.</p>}</div><div className="border-t p-2"><Button type="button" variant="ghost" className="w-full justify-start" onClick={() => {setHistoryOpen(false);onOpenAppMenu()}}>Menu EnnoSmart</Button></div></aside></div>}
    <div className="flex min-h-0 flex-1 flex-col-reverse md:flex-row">
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
    <div ref={viewport} className="min-h-0 flex-1 overflow-y-auto px-3 py-4 sm:px-6 sm:py-5" aria-busy={running || loading}>
      {loading ? <p role="status" className="py-12 text-center text-muted-foreground">Chargement de votre conversation…</p> : session?.messages.length ? <div className="mx-auto max-w-3xl space-y-5 sm:space-y-6">{session.messages.map(message => <Response key={message.id} message={message} sessionId={session.id} onExplore={article => setDraft(`Approfondis la publication « ${article.title} » (identifiant ${article.id}) à partir des sources enregistrées, puis cherche son texte si nécessaire.`)} onChanged={refreshSession} />)}</div> : session?.mode === "diagnostic_linked" && session.diagnostic_context ? <div className="mx-auto max-w-2xl py-10"><BookOpen aria-hidden="true" className="mb-5 size-9 text-primary/70" /><h2 className="text-2xl font-semibold">Partons des difficultés de votre projet</h2><p className="mt-3 leading-7 text-muted-foreground">J’ai retrouvé les verrous sélectionnés pour ce projet. Vous pouvez étudier l’ensemble ou commencer par l’un d’eux.</p><ol className="mt-5 list-decimal space-y-3 pl-5">{session.diagnostic_context.locks.filter(v => session.diagnostic_context!.selected_lock_ids.includes(v.id)).map(lock => <li key={lock.id}><strong>{lock.title}</strong>{lock.decision !== "garde" && <span className="ml-2 text-muted-foreground">À valider</span>}</li>)}</ol><Button className="mt-6" variant="outline" onClick={() => askInChat("Recherche les publications utiles aux verrous actifs de ce projet. Utilise les méthodes, paramètres, résultats et preuves du diagnostic pour expliquer leur pertinence, en distinguant les faits du projet et les résultats publiés.")}>Préparer la recherche sur le périmètre actif</Button><p className="mt-3 text-sm text-muted-foreground">Vous pouvez écrire « Je veux commencer seulement par le premier ». Vérifiez puis envoyez le message pour lancer la recherche.</p></div> : <div className="mx-auto grid max-w-xl place-items-center py-10 text-center"><BookOpen aria-hidden="true" className="mb-5 size-9 text-primary/70" /><h2 className="text-2xl font-semibold">Quel problème scientifique explorez-vous ?</h2><p className="mt-3 leading-7 text-muted-foreground">Décrivez votre sujet, partagez un article ou proposez un plan. Nous avancerons à partir de sources vérifiables.</p><button className="mt-7 rounded-xl border p-4 text-left text-sm leading-6 hover:bg-muted focus-visible:outline-primary" onClick={() => setDraft("Je travaille sur la génération automatique de maillages volumiques 3D à l’aide de modèles entraînés pour la mécanique. Recherche les articles scientifiques les plus pertinents, y compris payants. Explique leurs contributions, leurs limites et ce que tu as réellement pu consulter.")}>Trouver des publications sur la génération de maillages volumiques 3D par apprentissage <span aria-hidden="true">→</span></button></div>}
      {session && !loading && <SelectionPanel session={session} disabled={running || busy} onChanged={refreshSession} ask={askInChat} />}
      {documentSummary?.outline.sections?.length ? <section className="mx-auto mt-5 flex max-w-3xl flex-wrap items-center gap-2 rounded-xl border px-3 py-2 text-sm" aria-label="Plan et rédaction"><FileText aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /><strong className="min-w-0 flex-1 truncate">{documentSummary.outline.title || "État de l’art"}</strong><span className="text-xs text-muted-foreground">{savedSections ? `${savedSections}/${documentSummary.outline.sections.length} sections enregistrées` : "Plan proposé"}</span><Button type="button" size="sm" variant="ghost" onClick={() => setWorkspaceOpen(true)}>{savedSections > 0 ? "Lire et modifier le manuscrit" : "Modifier le plan"}</Button>{documentSummary.outline.status !== "approved" && <Button type="button" size="sm" variant="outline" disabled={busy || running} onClick={approvePlan}>Valider le plan</Button>}{session?.writing_progress && session.writing_progress.status !== "draft_complete" && <Button type="button" variant="ghost" size="sm" onClick={() => setDraft("Continue la rédaction des sections restantes.")}>Reprendre</Button>}</section> : null}
      {running && <p role="status" aria-live="polite" className="mx-auto mt-6 flex max-w-3xl items-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />{run?.cancel_requested ? "Arrêt demandé…" : activityLabels[activity?.tool || ""] || "EnnoScholar prépare sa réponse…"}</p>}
    </div>
    {error && <div role="alert" className="mx-4 mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}{running && <button className="ml-3 underline" onClick={() => setPollRetry(n => n + 1)}>Réessayer la connexion</button>}</div>}
    <form onSubmit={e => { e.preventDefault(); send() }} className="shrink-0 bg-background px-3 pb-3 pt-2 sm:px-5">
      <label htmlFor="scholar-v4-message" className="sr-only">Votre message scientifique</label>
      <div className="mx-auto flex max-w-3xl items-end gap-1 rounded-[26px] border bg-muted/20 px-2 py-1.5 shadow-sm focus-within:border-primary/60">
        <input ref={fileInput} type="file" accept=".pdf,.txt,.md,.docx" className="hidden" aria-label="Importer un document" onChange={e => { const file = e.target.files?.[0]; if (file) upload(file) }} />
        <Button type="button" variant="ghost" size="icon" className="size-9 shrink-0 rounded-full" aria-label="Ajouter un document" disabled={busy || running || loading} onClick={() => fileInput.current?.click()}><Paperclip aria-hidden="true" className="size-4" /></Button>
        <textarea ref={textarea} id="scholar-v4-message" rows={1} maxLength={30000} className="min-h-10 min-w-0 flex-1 resize-none overflow-y-hidden bg-transparent px-2 py-2.5 text-sm leading-5 outline-none placeholder:text-muted-foreground" placeholder="Demandez à EnnoScholar…" value={draft} onChange={e => setDraft(e.target.value)} disabled={busy || loading} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} aria-describedby="scholar-v4-input-hint" />
        {running ? <Button type="button" variant="outline" size="icon" className="size-9 shrink-0 rounded-full" aria-label="Arrêter la réponse" disabled={run?.cancel_requested} onClick={async () => { try { setRun(await cancelRun(run!.id)) } catch (e) { setError(e instanceof Error ? e.message : "Arrêt indisponible.") } }}><Square aria-hidden="true" className="size-3" /></Button> : <Button type="submit" size="icon" aria-label="Envoyer le message" disabled={!draft.trim() || busy || loading} className="size-9 shrink-0 rounded-full">{busy ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <ArrowUp aria-hidden="true" className="size-4" />}</Button>}
      </div><p id="scholar-v4-input-hint" className="sr-only">Entrée pour envoyer ; Maj et Entrée pour un saut de ligne.</p>
    </form>
    </div>
    {workspaceOpen && hasPlan && session && <aside aria-label="Artefact documentaire" className="h-[45%] min-h-0 w-full overflow-y-auto border-b bg-background px-4 py-4 md:h-auto md:w-[42%] md:border-b-0 md:border-l lg:px-6"><ScholarV4Workspace sid={session.id} updatedAt={session.updated_at} running={running || busy} ask={askInChat} onChanged={refreshSession} /></aside>}
    </div>
  </div>
}

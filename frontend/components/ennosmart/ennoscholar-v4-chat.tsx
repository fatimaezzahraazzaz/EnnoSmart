"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { ArrowUp, BookOpen, Check, Copy, Loader2, Paperclip, Plus, Square } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cancelRun, createSession, getArticlePage, getRun, getSession, isRunning, listSessions, safeUrl, sendMessage, uploadDocument, type Article, type Message, type Run, type Session, type SessionSummary } from "@/lib/scholar-v4"

const activityLabels: Record<string, string> = {
  search_scientific: "Recherche scientifique en cours…", web_search: "Recherche sur le Web…",
  get_article: "Vérification des publications…", get_fulltext: "Recherche des textes accessibles…",
  analyze_article: "Analyse des passages consultés…", compare_articles: "Comparaison des travaux…",
  manage_outline: "Mise à jour du plan…", search_for_section: "Recherche pour cette section…",
  write_section: "Préparation de la rédaction…", finalize_response: "Vérification des sources…",
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
  all: { title: "Autres notices conservées", note: "Bibliothèque historique : ces notices ne sont pas toutes des références recommandées ou des textes consultés." },
}

function LibraryPublications({ sessionId, excludeIds, count, category, onExplore }: { sessionId: string; excludeIds: string[]; count: number; category: string; onExplore: (article: Article) => void }) {
  const [articles, setArticles] = useState<Article[]>([])
  const [total, setTotal] = useState(count)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [loaded, setLoaded] = useState(false)
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
      return <li key={a.id} className="border-t pt-3"><p className="font-medium">{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{a.title}</a> : a.title}</p><p className="mt-1 text-xs text-muted-foreground">{a.year || "Année non confirmée"} · {accessLabels[a.access_status] || a.access_status}{a.publisher_access === "paywalled" ? " · Éditeur payant" : ""}{!a.verified ? " · Identité bibliographique à vérifier" : ""}</p><Button type="button" variant="ghost" size="sm" className="mt-1" onClick={() => onExplore(a)}>Approfondir cette publication</Button></li>
    })}</ul>
    {loading && <p role="status" className="mt-3">Chargement de la bibliothèque…</p>}
    {error && <p role="alert" className="mt-3 text-destructive">{error} <button className="underline" onClick={() => load(articles.length)}>Réessayer</button></p>}
    {!loading && loaded && articles.length < total && <Button variant="outline" size="sm" className="mt-3" onClick={() => load(articles.length)}>Voir davantage de publications</Button>}
  </details>
}

function Response({ message, sessionId, onExplore }: { message: Message; sessionId: string; onExplore: (article: Article) => void }) {
  const [copied, setCopied] = useState(false)
  const [copyError, setCopyError] = useState(false)
  if (message.role === "user") return <div className="ml-auto max-w-[90%] rounded-2xl bg-primary/10 px-5 py-3 whitespace-pre-wrap break-words text-sm sm:max-w-[80%]">{message.content}</div>
  return <article className="min-w-0 text-sm sm:text-base" aria-label="Réponse EnnoScholar">
    {message.research_status === "incomplete" && <p className="mb-3 rounded-lg bg-muted px-3 py-2 text-sm">{message.fallback ? "Synthèse de secours" : "Réponse publiée"} · Couverture scientifique à compléter</p>}
    {message.research_status === "sufficient" && <p className="mb-3 text-xs text-muted-foreground">Couverture jugée suffisante par l’assistant pour cette demande ; les limites restent précisées dans la réponse.</p>}
    <Markdown content={message.content} />
    {message.citations?.some(c => c.cited) && <details className="my-3 text-sm"><summary className="cursor-pointer text-muted-foreground">Sources utilisées dans cette réponse ({message.citations.filter(c => c.cited).length})</summary><ul className="mt-2 space-y-2">{message.citations.filter(c => c.cited).map((c, i) => safeUrl(c.url) && <li key={`${c.url}-${i}`}><a href={safeUrl(c.url)} target="_blank" rel="noopener noreferrer" className="text-primary underline break-words">{c.title || c.url}</a></li>)}</ul></details>}
    {message.library_counts ? ["retained_complementary", "unexamined", "examined_pending", "rejected"].map(category => Boolean(message.library_counts![category]) && <LibraryPublications key={category} sessionId={sessionId} excludeIds={message.primary_article_ids || []} count={message.library_counts![category]} category={category} onExplore={onExplore} />) : Boolean(message.complementary_count) && <LibraryPublications sessionId={sessionId} excludeIds={message.primary_article_ids || []} count={message.complementary_count!} category="all" onExplore={onExplore} />}
    {Boolean(message.discovered_sources_count) && <p className="my-2 text-xs text-muted-foreground">Les URL repérées pendant l’exploration restent dans la traçabilité ; elles ne sont pas toutes des sources consultées.</p>}
    <Button variant="ghost" size="sm" aria-label="Copier la réponse" onClick={async () => { try { await navigator.clipboard.writeText(visibleResponse(message.content)); setCopied(true); setCopyError(false); setTimeout(() => setCopied(false), 2000) } catch { setCopyError(true) } }}>{copied ? <Check aria-hidden="true" className="size-4" /> : <Copy aria-hidden="true" className="size-4" />} {copied ? "Copié" : "Copier"}</Button>
    {copyError && <span role="alert" className="text-sm text-destructive">Copie indisponible dans ce navigateur.</span>}
  </article>
}

export default function EnnoScholarV4Chat({ userId }: { userId: number }) {
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [session, setSession] = useState<Session | null>(null)
  const [run, setRun] = useState<Run | null>(null)
  const [draft, setDraft] = useState("")
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [pollRetry, setPollRetry] = useState(0)
  const viewport = useRef<HTMLDivElement>(null)
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

  useEffect(() => { viewport.current?.scrollTo({ top: viewport.current.scrollHeight, behavior: "instant" }) }, [session?.messages.length, running])

  async function newConversation() {
    setBusy(true); setError("")
    try {
      const current = await createSession()
      requestEpoch.current++; setSession(current); setRun(null); setDraft("")
      localStorage.setItem(storageKey, current.id); setSessions(await listSessions())
    } catch (e) { setError(e instanceof Error ? e.message : "Création impossible.") }
    finally { setBusy(false) }
  }

  async function send() {
    if (!draft.trim() || busy || running || loading) return
    const message = draft.trim()
    setBusy(true); setError("")
    try {
      const current = session || await createSession()
      setSession(current); localStorage.setItem(storageKey, current.id)
      const started = await sendMessage(current.id, message)
      setRun(started); setDraft("")
      setSession(await getSession(current.id)); setSessions(await listSessions())
    } catch (e) { setError(e instanceof Error ? e.message : "Envoi impossible. Votre message est conservé dans le champ de saisie.") }
    finally { setBusy(false) }
  }

  async function upload(file: File) {
    if (file.size > 15 * 1024 * 1024) { setError("Document limité à 15 Mo."); return }
    setBusy(true); setError("")
    try {
      const current = session || await createSession()
      setSession(current); localStorage.setItem(storageKey, current.id)
      const article = await uploadDocument(current.id, file)
      setSession(await getSession(current.id)); setSessions(await listSessions())
      setDraft(`Analyse le document que je viens de déposer : ${article.title}. Explique son problème scientifique, sa méthode, ses résultats et ses limites.`)
    } catch (e) { setError(e instanceof Error ? e.message : "Import impossible.") }
    finally { setBusy(false); if (fileInput.current) fileInput.current.value = "" }
  }

  const activity = run?.activity?.filter(e => e.status === "running").at(-1)
  return <div className="mx-auto flex h-[calc(100dvh-10rem)] min-h-[420px] max-w-5xl flex-col rounded-2xl border bg-background shadow-sm">
    <header className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-4 sm:px-6">
      <div className="flex items-center gap-3"><span className="rounded-xl bg-primary/10 p-2 text-primary"><BookOpen aria-hidden="true" className="size-5" /></span><div><h1 className="font-semibold">EnnoScholar V4</h1><p className="text-xs text-muted-foreground">Votre recherche scientifique, en conversation</p></div></div>
      <Button variant="outline" size="sm" onClick={newConversation} disabled={busy || loading}><Plus aria-hidden="true" className="size-4" />Nouvelle conversation</Button>
      <div className="w-full"><label htmlFor="scholar-v4-history" className="sr-only">Historique des conversations</label><select id="scholar-v4-history" className="max-w-full rounded-lg border bg-background px-3 py-2 text-sm focus-visible:outline-primary" value={session?.id || ""} disabled={busy || loading} onChange={e => openSession(e.target.value)}><option value="" disabled>Historique des conversations</option>{sessions.map(s => <option key={s.id} value={s.id}>{s.title}</option>)}</select></div>
    </header>
    <div ref={viewport} className="min-h-0 flex-1 overflow-y-auto px-4 py-6 sm:px-8" aria-busy={running || loading}>
      {loading ? <p role="status" className="py-16 text-center text-muted-foreground">Chargement de votre conversation…</p> : session?.messages.length ? <div className="mx-auto max-w-3xl space-y-8">{session.messages.map(message => <Response key={message.id} message={message} sessionId={session.id} onExplore={article => setDraft(`Approfondis la publication « ${article.title} » (identifiant ${article.id}) à partir des sources enregistrées, puis cherche son texte si nécessaire.`)} />)}</div> : <div className="mx-auto grid max-w-xl place-items-center py-10 text-center sm:py-16"><BookOpen aria-hidden="true" className="mb-5 size-9 text-primary/70" /><h2 className="text-2xl font-semibold">Quel problème scientifique explorez-vous ?</h2><p className="mt-3 leading-7 text-muted-foreground">Décrivez votre sujet, partagez un article ou proposez un plan. Nous avancerons à partir de sources vérifiables.</p><button className="mt-7 rounded-xl border p-4 text-left text-sm leading-6 hover:bg-muted focus-visible:outline-primary" onClick={() => setDraft("Je travaille sur la génération automatique de maillages volumiques 3D à l’aide de modèles entraînés pour la mécanique. Recherche les articles scientifiques les plus pertinents, y compris payants. Explique leurs contributions, leurs limites et ce que tu as réellement pu consulter.")}>Trouver des publications sur la génération de maillages volumiques 3D par apprentissage <span aria-hidden="true">→</span></button></div>}
      {running && <p role="status" aria-live="polite" className="mx-auto mt-6 flex max-w-3xl items-center gap-2 text-sm text-muted-foreground"><Loader2 aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />{run?.cancel_requested ? "Arrêt demandé…" : activityLabels[activity?.tool || ""] || "EnnoScholar prépare sa réponse…"}</p>}
    </div>
    {error && <div role="alert" className="mx-4 mb-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}{running && <button className="ml-3 underline" onClick={() => setPollRetry(n => n + 1)}>Réessayer la connexion</button>}</div>}
    <form onSubmit={e => { e.preventDefault(); send() }} className="border-t p-3 sm:p-5">
      <label htmlFor="scholar-v4-message" className="sr-only">Votre message scientifique</label>
      <div className="rounded-2xl border bg-muted/20 p-2 focus-within:border-primary/60">
        <textarea id="scholar-v4-message" rows={3} maxLength={30000} className="block w-full resize-none bg-transparent px-3 py-2 text-sm outline-none placeholder:text-muted-foreground" placeholder="Décrivez votre recherche, ou poursuivez la conversation…" value={draft} onChange={e => setDraft(e.target.value)} disabled={busy || loading} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send() } }} />
        <div className="flex items-center justify-between px-1"><input ref={fileInput} type="file" accept=".pdf,.txt,.md,.docx" className="hidden" aria-label="Importer un document" onChange={e => { const file = e.target.files?.[0]; if (file) upload(file) }} /><Button type="button" variant="ghost" size="sm" disabled={busy || running || loading} onClick={() => fileInput.current?.click()}><Paperclip aria-hidden="true" className="size-4" />Document</Button>{running ? <Button type="button" variant="outline" size="sm" disabled={run?.cancel_requested} onClick={async () => { try { setRun(await cancelRun(run!.id)) } catch (e) { setError(e instanceof Error ? e.message : "Arrêt indisponible.") } }}><Square aria-hidden="true" className="size-3" />Arrêter</Button> : <Button type="submit" size="icon" aria-label="Envoyer le message" disabled={!draft.trim() || busy || loading} className="size-10 rounded-xl">{busy ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <ArrowUp aria-hidden="true" className="size-5" />}</Button>}</div>
      </div><p className="mt-2 text-center text-xs text-muted-foreground">Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne · PDF, Word et texte</p>
    </form>
  </div>
}

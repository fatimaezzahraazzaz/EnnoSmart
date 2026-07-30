"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  BookOpenText,
  Bot,
  Check,
  ChevronLeft,
  Copy,
  ExternalLink,
  FilePlus2,
  FileText,
  Globe2,
  Library,
  Loader2,
  Menu,
  MessageSquarePlus,
  MessageSquareText,
  PanelRightClose,
  PanelRightOpen,
  RefreshCw,
  Search,
  Send,
  Trash2,
  UploadCloud,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Textarea } from "@/components/ui/textarea"
import {
  createGuidedResearchSession,
  deleteGuidedResearchSession,
  decideGuidedResearchSources,
  getGuidedResearchSession,
  getUploadedScholarArticlePdf,
  listGuidedResearchSessions,
  prepareStateOfArtPhase1And2,
  sendGuidedResearchMessage,
  uploadNewScholarSource,
  updateArticleDecision,
  type ArticleRead,
  type GuidedResearchConversationTurn,
  type GuidedResearchSession,
} from "@/lib/api"

type ResearchCandidate = {
  candidate_id: string
  title?: string
  authors?: string[]
  year?: number | null
  url?: string | null
  doi?: string | null
  abstract?: string | null
  source_providers?: string[]
  candidate_kind?: string
  relevance_score?: number
  selection_priority_score?: number
  open_access?: boolean
  consultant_decision?: string
  relevance_role?:
    | "direct_evidence"
    | "connected_evidence"
    | "official_documentation"
    | "implementation"
  role_reason?: string
  current_research_batch?: boolean
}

type Props = {
  projectId: number
  projectLabel?: string
  selectedArticles?: ArticleRead[]
  onCorpusChanged?: () => Promise<void> | void
  onGenerate: () => Promise<void> | void
  onRefreshDraft?: () => Promise<void> | void
  draftMarkdown?: string
  generating?: boolean
  generationError?: string | null
  immersive?: boolean
  projectOptions?: Array<{ id: number; label: string }>
  onProjectChange?: (projectId: number) => Promise<void> | void
  onBackToArticles?: () => void
}

const storageKey = (projectId: number) =>
  `ennoscholar_guided_session_${projectId}`

function formatSessionDate(value?: string) {
  if (!value) return ""
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ""
  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date)
}

function sessionLabel(session: GuidedResearchSession) {
  const title = String(session.title || "").trim()
  if (title) return title
  return session.message_count ? "Conversation scientifique" : "Nouvelle conversation"
}

function citationText(text: string) {
  return text.split(/(\[A\d+(?:\s*,\s*A\d+)*\])/g).map((part, index) =>
    /^\[A\d+(?:\s*,\s*A\d+)*\]$/.test(part) ? (
      <span
        key={index}
        className="mx-0.5 rounded-md bg-blue-50 px-1.5 py-0.5 text-xs font-semibold text-blue-700"
      >
        {part}
      </span>
    ) : (
      <span key={index}>{part}</span>
    ),
  )
}

function DraftPreview({ markdown }: { markdown: string }) {
  if (!markdown.trim()) {
    return (
      <div className="flex h-full min-h-[430px] flex-col items-center justify-center px-8 text-center">
        <div className="rounded-2xl bg-slate-100 p-4 text-slate-500">
          <FileText className="size-7" />
        </div>
        <p className="mt-4 font-semibold text-slate-900">
          Aucun état de l’art rédigé
        </p>
        <p className="mt-2 max-w-sm text-sm leading-6 text-slate-500">
          Discutez du plan et des sources dans le chat. L’artefact s’ouvrira
          automatiquement dès qu’une rédaction sera disponible.
        </p>
      </div>
    )
  }

  return (
    <article className="mx-auto max-w-3xl px-6 py-8">
      <div className="space-y-2 text-[14px] leading-7 text-slate-700">
        {markdown.split(/\n/).map((raw, index) => {
          const line = raw.trim()
          if (!line) return <div key={index} className="h-2" />
          if (line.startsWith("# ")) {
            return (
              <h1
                key={index}
                className="mb-6 text-2xl font-bold tracking-tight text-slate-950"
              >
                {line.slice(2)}
              </h1>
            )
          }
          if (line.startsWith("## ")) {
            return (
              <h2
                key={index}
                className="mb-3 mt-9 border-b border-slate-200 pb-2 text-xl font-semibold text-slate-950"
              >
                {line.slice(3)}
              </h2>
            )
          }
          if (line.startsWith("### ")) {
            return (
              <h3
                key={index}
                className="mb-2 mt-7 text-base font-semibold text-slate-900"
              >
                {line.slice(4)}
              </h3>
            )
          }
          if (line.startsWith("- ")) {
            return (
              <p key={index} className="ml-4 border-l-2 border-blue-200 pl-3">
                {citationText(line.slice(2))}
              </p>
            )
          }
          return (
            <p key={index} className="text-justify">
              {citationText(line)}
            </p>
          )
        })}
      </div>
    </article>
  )
}

function CandidateCard({
  candidate,
  busy,
  onDecision,
}: {
  candidate: ResearchCandidate
  busy: boolean
  onDecision: (
    candidate: ResearchCandidate,
    decision: "accepted" | "rejected",
  ) => void
}) {
  const decided = String(candidate.consultant_decision || "")
  const documentation = [
    "official_documentation",
    "documentation",
    "software_repository",
    "research_output",
  ].includes(String(candidate.candidate_kind))
  const roleLabel =
    {
      direct_evidence: "Preuve directe",
      connected_evidence: "Source connexe",
      official_documentation: "Documentation officielle",
      implementation: "Implémentation",
    }[String(candidate.relevance_role)] || ""

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge
          variant="outline"
          className={
            documentation
              ? "border-violet-200 bg-violet-50 text-violet-700"
              : "border-blue-200 bg-blue-50 text-blue-700"
          }
        >
          {documentation ? (
            <Globe2 className="mr-1 size-3" />
          ) : (
            <BookOpenText className="mr-1 size-3" />
          )}
          {documentation ? "Documentation" : "Article scientifique"}
        </Badge>
        {roleLabel && <Badge variant="outline">{roleLabel}</Badge>}
        {candidate.open_access && <Badge variant="outline">Accès public</Badge>}
      </div>

      <p className="mt-3 text-sm font-semibold leading-5 text-slate-950">
        {candidate.title || "Source sans titre"}
      </p>
      <p className="mt-1 text-xs text-slate-500">
        {[
          candidate.authors?.slice(0, 2).join(", "),
          candidate.year,
          candidate.source_providers?.join(" · "),
        ]
          .filter(Boolean)
          .join(" — ")}
      </p>
      {candidate.abstract && (
        <p className="mt-2 line-clamp-3 text-xs leading-5 text-slate-600">
          {candidate.abstract}
        </p>
      )}
      {candidate.role_reason && (
        <p className="mt-2 rounded-xl bg-slate-50 p-2.5 text-xs leading-5 text-slate-600">
          {candidate.role_reason}
        </p>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {candidate.url && (
          <a
            href={candidate.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 items-center rounded-lg px-2 text-xs font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
          >
            <ExternalLink className="mr-1 size-3" />
            Consulter
          </a>
        )}
        {!["accepted", "rejected"].includes(decided) ? (
          <>
            <Button
              size="sm"
              className="h-8 rounded-lg"
              disabled={busy}
              onClick={() => onDecision(candidate, "accepted")}
            >
              {busy ? (
                <Loader2 className="mr-1 size-3 animate-spin" />
              ) : (
                <Check className="mr-1 size-3" />
              )}
              Ajouter au corpus
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-8 rounded-lg"
              disabled={busy}
              onClick={() => onDecision(candidate, "rejected")}
            >
              <X className="mr-1 size-3" />
              Écarter
            </Button>
          </>
        ) : (
          <Badge className={decided === "accepted" ? "bg-emerald-600" : "bg-slate-500"}>
            {decided === "accepted" ? "Ajouté au corpus" : "Écarté"}
          </Badge>
        )}
      </div>
    </div>
  )
}

function CorpusDialog({
  open,
  onOpenChange,
  articles,
  busyArticleId,
  preparing,
  onRemove,
  onSearch,
  onUpload,
  onConsult,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  articles: ArticleRead[]
  busyArticleId: number | null
  preparing: boolean
  onRemove: (article: ArticleRead) => void
  onSearch: (query: string) => void
  onUpload: (file: File) => void
  onConsult: (article: ArticleRead) => void
}) {
  const [query, setQuery] = useState("")
  const uploadInputRef = useRef<HTMLInputElement | null>(null)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[88vh] flex-col overflow-hidden p-0 sm:max-w-4xl">
        <DialogHeader className="border-b px-6 py-5">
          <DialogTitle className="flex items-center gap-2">
            <Library className="size-5 text-brand" />
            Corpus de rédaction
            <Badge variant="outline">{articles.length} article(s)</Badge>
          </DialogTitle>
          <DialogDescription>
            Chaque article présent ici est sélectionné pour la rédaction.
            L’ajout prépare le texte intégral puis construit automatiquement son
            Article Card.
          </DialogDescription>
        </DialogHeader>

        <div className="border-b bg-slate-50 px-6 py-4">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Ajouter une nouvelle source
          </p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <div className="flex min-w-0 flex-1 gap-2">
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Titre, DOI, URL ou besoin scientifique…"
                onKeyDown={(event) => {
                  if (event.key === "Enter" && query.trim()) {
                    event.preventDefault()
                    onSearch(query.trim())
                    setQuery("")
                  }
                }}
              />
              <Button
                disabled={!query.trim() || preparing}
                onClick={() => {
                  onSearch(query.trim())
                  setQuery("")
                }}
              >
                <Search className="mr-2 size-4" />
                Rechercher
              </Button>
            </div>
            <input
              ref={uploadInputRef}
              type="file"
              accept="application/pdf,.pdf"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0]
                event.target.value = ""
                if (file) onUpload(file)
              }}
            />
            <Button
              type="button"
              variant="outline"
              disabled={preparing}
              onClick={() => uploadInputRef.current?.click()}
            >
              {preparing ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : (
                <UploadCloud className="mr-2 size-4" />
              )}
              Importer un PDF
            </Button>
          </div>
          <p className="mt-2 text-xs text-slate-500">
            Les candidats apparaîtront dans le chat. Cliquez ensuite sur
            « Ajouter au corpus » pour lancer les phases 1 et 2. Un PDF importé
            depuis votre PC est directement extrait et transformé en Article Card.
          </p>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-6 py-4">
          {articles.length === 0 ? (
            <div className="rounded-2xl border border-dashed p-10 text-center">
              <Library className="mx-auto size-7 text-slate-400" />
              <p className="mt-3 font-medium">Le corpus est vide</p>
              <p className="mt-1 text-sm text-slate-500">
                Recherchez puis validez une première source scientifique.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {articles.map((article, index) => (
                <div
                  key={article.id}
                  className="flex items-start gap-3 rounded-2xl border border-slate-200 p-3"
                >
                  <div className="flex size-8 shrink-0 items-center justify-center rounded-xl bg-blue-50 text-xs font-semibold text-blue-700">
                    {index + 1}
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-semibold leading-5 text-slate-950">
                      {article.title}
                    </p>
                    <div className="mt-1 flex flex-wrap gap-1.5 text-xs text-slate-500">
                      {article.year && <span>{article.year}</span>}
                      {article.tag_article && (
                        <Badge variant="outline" className="h-5 text-[10px]">
                          {article.tag_article}
                        </Badge>
                      )}
                      {article.doi && <span className="truncate">DOI {article.doi}</span>}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {(article.url ||
                      article.source_json?.uploaded_pdf_available) && (
                      <button
                        type="button"
                        aria-label={`Consulter ${article.title}`}
                        className="inline-flex size-9 items-center justify-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                        onClick={() => onConsult(article)}
                      >
                        <ExternalLink className="size-4" />
                      </button>
                    )}
                    <Button
                      variant="ghost"
                      size="icon"
                      className="text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                      disabled={busyArticleId === article.id || preparing}
                      onClick={() => onRemove(article)}
                      aria-label={`Retirer ${article.title} du corpus`}
                    >
                      {busyArticleId === article.id ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Trash2 className="size-4" />
                      )}
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

export function EnnoScholarPlanChat({
  projectId,
  projectLabel = "Projet actif",
  selectedArticles = [],
  onCorpusChanged,
  onGenerate,
  onRefreshDraft,
  draftMarkdown = "",
  generating = false,
  generationError = null,
  immersive = false,
  projectOptions = [],
  onProjectChange,
  onBackToArticles,
}: Props) {
  const [sessions, setSessions] = useState<GuidedResearchSession[]>([])
  const [sessionId, setSessionId] = useState("")
  const [messages, setMessages] = useState<GuidedResearchConversationTurn[]>([])
  const [candidates, setCandidates] = useState<ResearchCandidate[]>([])
  const [input, setInput] = useState("")
  const [loading, setLoading] = useState(false)
  const [initializing, setInitializing] = useState(true)
  const [decidingId, setDecidingId] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [artifactOpen, setArtifactOpen] = useState(Boolean(draftMarkdown.trim()))
  const [corpusOpen, setCorpusOpen] = useState(false)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [preparingCorpus, setPreparingCorpus] = useState(false)
  const [busyArticleId, setBusyArticleId] = useState<number | null>(null)
  const [deletingSessionId, setDeletingSessionId] = useState("")
  const previousHasDraft = useRef(Boolean(draftMarkdown.trim()))
  const messagesViewportRef = useRef<HTMLDivElement | null>(null)

  const hasDraft = Boolean(draftMarkdown.trim())
  const canSend = Boolean(sessionId && input.trim() && !loading && !generating)
  const wordCount = useMemo(
    () => (draftMarkdown.match(/\b[\p{L}\p{N}'’-]+\b/gu) || []).length,
    [draftMarkdown],
  )

  useEffect(() => {
    if (hasDraft && !previousHasDraft.current) setArtifactOpen(true)
    if (!hasDraft) setArtifactOpen(false)
    previousHasDraft.current = hasDraft
  }, [hasDraft])

  useEffect(() => {
    const viewport = messagesViewportRef.current
    if (viewport) viewport.scrollTop = viewport.scrollHeight
  }, [messages, candidates, loading, generating])

  async function refreshSessions() {
    const result = await listGuidedResearchSessions(projectId)
    const rows = Array.isArray(result?.sessions) ? result.sessions : []
    setSessions(rows)
    return rows
  }

  function candidatesFromSession(current: any): ResearchCandidate[] {
    const currentBatch = current?.artifacts?.research_plan?.candidates
    if (Array.isArray(currentBatch)) return currentBatch
    const selected = current?.artifacts?.selected_sources
    return Array.isArray(selected)
      ? selected.filter((row: ResearchCandidate) => row.current_research_batch)
      : []
  }

  async function openSession(id: string) {
    setInitializing(true)
    setError(null)
    try {
      const current = await getGuidedResearchSession(projectId, id)
      setSessionId(id)
      setMessages(current?.session?.messages || [])
      setCandidates(candidatesFromSession(current))
      localStorage.setItem(storageKey(projectId), id)
    } catch (err: any) {
      setError(err?.message || "Impossible d’ouvrir cette conversation.")
      throw err
    } finally {
      setInitializing(false)
    }
  }

  async function createConversation() {
    setInitializing(true)
    setError(null)
    setNotice(null)
    try {
      const created = await createGuidedResearchSession(projectId)
      const id = String(created?.session?.session_id || "")
      if (!id) throw new Error("La conversation n’a pas été créée.")
      setSessionId(id)
      setMessages(created?.session?.messages || [])
      setCandidates([])
      localStorage.setItem(storageKey(projectId), id)
      await refreshSessions()
    } catch (err: any) {
      setError(err?.message || "Impossible de créer la conversation.")
    } finally {
      setInitializing(false)
    }
  }

  async function removeConversation(targetSession: GuidedResearchSession) {
    const confirmed = window.confirm(
      `Supprimer définitivement la conversation « ${sessionLabel(targetSession)} » ?`,
    )
    if (!confirmed) return

    const deletingActive = targetSession.session_id === sessionId
    setDeletingSessionId(targetSession.session_id)
    setError(null)
    setNotice(null)
    try {
      await deleteGuidedResearchSession(projectId, targetSession.session_id)
      const remaining = await refreshSessions()
      if (deletingActive) {
        localStorage.removeItem(storageKey(projectId))
        if (remaining[0]?.session_id) {
          await openSession(remaining[0].session_id)
        } else {
          await createConversation()
        }
      }
      setNotice("Conversation supprimée.")
    } catch (err: any) {
      setError(err?.message || "Impossible de supprimer cette conversation.")
    } finally {
      setDeletingSessionId("")
    }
  }

  async function initialize() {
    setInitializing(true)
    setError(null)
    try {
      const rows = await refreshSessions()
      const stored =
        typeof window !== "undefined"
          ? localStorage.getItem(storageKey(projectId))
          : null
      const preferred =
        rows.find((row) => row.session_id === stored)?.session_id ||
        rows[0]?.session_id
      if (preferred) {
        await openSession(preferred)
      } else {
        await createConversation()
      }
    } catch (err: any) {
      setError(err?.message || "Impossible d’ouvrir les conversations.")
      setInitializing(false)
    }
  }

  useEffect(() => {
    setSessionId("")
    setMessages([])
    setCandidates([])
    void initialize()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  async function submitMessageText(text: string) {
    const clean = text.trim()
    if (!sessionId || !clean || loading || generating) return
    setLoading(true)
    setError(null)
    setNotice(null)
    setMessages((current) => [
      ...current,
      {
        message_id: `local-${Date.now()}`,
        role: "consultant",
        content: clean,
        created_at: new Date().toISOString(),
      },
    ])
    try {
      const result = await sendGuidedResearchMessage(projectId, sessionId, clean)
      const response = result?.response
      setMessages((current) => [
        ...current,
        {
          message_id: `assistant-${Date.now()}`,
          role: "assistant",
          content: String(
            response?.assistant_message || "Demande prise en compte.",
          ),
          created_at: new Date().toISOString(),
          metadata: response?.metadata || {},
        },
      ])
      const found =
        response?.metadata?.candidates ||
        response?.metadata?.research?.candidates
      if (Array.isArray(found)) setCandidates(found)
      if (response?.metadata?.trigger_state_of_art_generation === true) {
        await onGenerate()
      }
      await refreshSessions()
    } catch (err: any) {
      setError(err?.message || "Impossible d’envoyer le message.")
    } finally {
      setLoading(false)
    }
  }

  async function submitMessage() {
    const text = input.trim()
    if (!text || !canSend) return
    setInput("")
    await submitMessageText(text)
  }

  async function decide(
    candidate: ResearchCandidate,
    decision: "accepted" | "rejected",
  ) {
    setDecidingId(candidate.candidate_id)
    setError(null)
    setNotice(null)
    if (decision === "accepted") setPreparingCorpus(true)
    try {
      const result = await decideGuidedResearchSources(
        projectId,
        sessionId,
        [candidate.candidate_id],
        decision,
      )
      setCandidates((current) =>
        current.map((row) =>
          row.candidate_id === candidate.candidate_id
            ? { ...row, consultant_decision: decision }
            : row,
        ),
      )
      const response = result?.response
      if (response?.assistant_message) {
        setMessages((current) => [
          ...current,
          {
            message_id: `assistant-source-${Date.now()}`,
            role: "assistant",
            content: String(response.assistant_message),
            created_at: new Date().toISOString(),
            metadata: response?.metadata || {},
          },
        ])
      }
      if (decision === "accepted") {
        setNotice(
          "Source ajoutée : extraction et Article Card terminées ou signalées dans le message.",
        )
        await onCorpusChanged?.()
      }
      await refreshSessions()
    } catch (err: any) {
      setError(err?.message || "Impossible d’enregistrer cette décision.")
    } finally {
      setDecidingId("")
      setPreparingCorpus(false)
    }
  }

  async function removeArticle(article: ArticleRead) {
    const confirmed = window.confirm(
      `Retirer « ${article.title} » du corpus de rédaction ?`,
    )
    if (!confirmed) return
    setBusyArticleId(article.id)
    setPreparingCorpus(true)
    setError(null)
      setNotice(null)
    try {
      await updateArticleDecision(projectId, article.id, "rejete")
      await prepareStateOfArtPhase1And2(projectId, {
        force: false,
        maxArticles: null,
        articleCardMode: "auto",
      })
      await onCorpusChanged?.()
      setNotice("Article retiré du corpus et artefacts de sélection actualisés.")
    } catch (err: any) {
      setError(err?.message || "Impossible de retirer cet article.")
    } finally {
      setBusyArticleId(null)
      setPreparingCorpus(false)
    }
  }

  async function searchNewArticle(query: string) {
    if (!query.trim()) return
    setCorpusOpen(false)
    await submitMessageText(
      `Recherche une nouvelle source scientifique pour compléter le corpus sur : ${query.trim()}. ` +
        "Présente les candidats pertinents sans les sélectionner automatiquement.",
    )
  }

  async function uploadArticleFromComputer(file: File) {
    setPreparingCorpus(true)
    setError(null)
    setNotice(null)
    try {
      const result = await uploadNewScholarSource(projectId, file)
      await onCorpusChanged?.()
      const cardsCount = Number(result?.phase_2?.cards_count || 0)
      setNotice(
        `« ${result?.article?.title || file.name} » a été importé, extrait et ajouté au corpus. ` +
          `${cardsCount} Article Card(s) sont maintenant prêtes.`,
      )
    } catch (err: any) {
      setError(err?.message || "Impossible d’importer et préparer ce PDF.")
    } finally {
      setPreparingCorpus(false)
    }
  }

  async function consultArticle(article: ArticleRead) {
    if (article.url) {
      window.open(article.url, "_blank", "noopener,noreferrer")
      return
    }
    if (!article.source_json?.uploaded_pdf_available) return

    const viewer = window.open("about:blank", "_blank")
    try {
      const blob = await getUploadedScholarArticlePdf(projectId, article.id)
      const objectUrl = URL.createObjectURL(blob)
      if (viewer) {
        viewer.location.href = objectUrl
      } else {
        window.open(objectUrl, "_blank", "noopener,noreferrer")
      }
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 300_000)
    } catch (err: any) {
      viewer?.close()
      setError(err?.message || "Impossible d’ouvrir le PDF importé.")
    }
  }

  return (
    <>
      <div
        className={
          immersive
            ? "relative flex h-full min-h-0 overflow-hidden bg-white"
            : "relative flex h-[calc(100dvh-205px)] min-h-[520px] max-h-[900px] overflow-hidden rounded-[28px] border border-slate-200 bg-white shadow-sm"
        }
      >
        <aside
          className={`w-[252px] shrink-0 flex-col border-r border-slate-200 bg-[#f7f7f8] ${
            artifactOpen ? "hidden 2xl:flex" : "hidden lg:flex"
          }`}
        >
          <div className="border-b border-slate-200 p-3">
            <Button
              className="w-full justify-start rounded-xl bg-slate-900 text-white hover:bg-slate-800"
              onClick={() => void createConversation()}
              disabled={initializing}
            >
              <MessageSquarePlus className="mr-2 size-4" />
              Nouvelle conversation
            </Button>

            <button
              type="button"
              onClick={() => hasDraft && setArtifactOpen((current) => !current)}
              className={`mt-2 flex w-full items-center justify-between rounded-xl border px-3 py-2.5 text-left transition ${
                hasDraft
                  ? "border-blue-200 bg-blue-50 text-blue-950 hover:bg-blue-100"
                  : "cursor-default border-slate-200 bg-white text-slate-400"
              }`}
            >
              <span className="flex items-center gap-2 text-sm font-medium">
                <FileText className="size-4" />
                État de l’art
              </span>
              <Badge
                variant="outline"
                className={
                  hasDraft
                    ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                    : ""
                }
              >
                {hasDraft ? "Rédigé" : "Fermé"}
              </Badge>
            </button>

            <button
              type="button"
              onClick={() => setCorpusOpen(true)}
              className="mt-2 flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2.5 text-left hover:bg-slate-50"
            >
              <span className="flex items-center gap-2 text-sm font-medium text-slate-800">
                <Library className="size-4 text-blue-600" />
                Corpus
              </span>
              <Badge variant="outline">{selectedArticles.length}</Badge>
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
            <p className="px-2 pb-2 text-[11px] font-semibold uppercase tracking-[0.16em] text-slate-400">
              Conversations
            </p>
            <div className="space-y-1">
              {sessions.map((session) => {
                const active = session.session_id === sessionId
                return (
                  <div
                    key={session.session_id}
                    className={`group flex items-start rounded-xl transition ${
                      active
                        ? "bg-white shadow-sm ring-1 ring-slate-200"
                        : "hover:bg-white/80"
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => void openSession(session.session_id)}
                      className="min-w-0 flex-1 px-3 py-2.5 text-left"
                    >
                      <p className="line-clamp-2 text-sm font-medium leading-5 text-slate-800">
                        {sessionLabel(session)}
                      </p>
                      <div className="mt-1 flex items-center justify-between gap-2 text-[11px] text-slate-400">
                        <span>{formatSessionDate(session.updated_at)}</span>
                        <span>{session.message_count || 0} msg.</span>
                      </div>
                    </button>
                    <button
                      type="button"
                      disabled={deletingSessionId === session.session_id}
                      onClick={() => void removeConversation(session)}
                      className="mr-1 mt-1.5 inline-flex size-8 shrink-0 items-center justify-center rounded-lg text-slate-400 opacity-50 transition hover:bg-rose-50 hover:text-rose-600 focus:opacity-100 group-hover:opacity-100"
                      aria-label={`Supprimer la conversation ${sessionLabel(session)}`}
                    >
                      {deletingSessionId === session.session_id ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Trash2 className="size-3.5" />
                      )}
                    </button>
                  </div>
                )
              })}
            </div>
          </div>

          <div className="border-t border-slate-200 p-3">
            <p className="truncate text-xs font-medium text-slate-700">
              {projectLabel}
            </p>
            <p className="mt-0.5 text-[11px] text-slate-400">
              Les conversations restent attachées à ce projet.
            </p>
          </div>
        </aside>

        <main className="flex min-w-0 flex-1 flex-col bg-white">
          <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 px-4 sm:px-6">
            <div className="flex min-w-0 items-center gap-3">
              <Button
                variant="ghost"
                size="icon"
                className="lg:hidden"
                title="Conversations"
                onClick={() => setMobileSidebarOpen(true)}
              >
                <Menu className="size-4" />
              </Button>
              <div className="min-w-0">
                <p className="truncate text-sm font-semibold text-slate-950">
                  {sessionLabel(
                    sessions.find((session) => session.session_id === sessionId) ||
                      ({
                        session_id: sessionId,
                        project_id: projectId,
                        state: "",
                        ready_to_write: false,
                        messages: [],
                      } as GuidedResearchSession),
                  )}
                </p>
                <p className="text-xs text-slate-500">
                  Conversation scientifique · mémoire du projet active
                </p>
              </div>
            </div>

            <div className="flex items-center gap-2">
              {onBackToArticles && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="hidden rounded-xl text-slate-600 sm:inline-flex"
                  onClick={onBackToArticles}
                >
                  <ChevronLeft className="mr-1 size-4" />
                  Articles
                </Button>
              )}
              {projectOptions.length > 1 && onProjectChange && (
                <select
                  value={projectId}
                  onChange={(event) =>
                    void onProjectChange(Number(event.target.value))
                  }
                  className="hidden h-9 max-w-56 rounded-xl border border-slate-200 bg-white px-3 text-xs text-slate-700 xl:block"
                  aria-label="Changer de projet"
                >
                  {projectOptions.map((option) => (
                    <option key={option.id} value={option.id}>
                      {option.label}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                onClick={() => setCorpusOpen(true)}
                className="flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-medium text-slate-700 shadow-sm hover:bg-slate-50"
              >
                <Library className="size-4 text-blue-600" />
                <span className="hidden sm:inline">Corpus de rédaction</span>
                <Badge variant="outline">{selectedArticles.length}</Badge>
                {preparingCorpus && <Loader2 className="size-3.5 animate-spin" />}
              </button>
              <Button
                variant="outline"
                size="icon"
                onClick={() => hasDraft && setArtifactOpen((current) => !current)}
                disabled={!hasDraft}
                title={hasDraft ? "Afficher l’état de l’art" : "Aucun état de l’art rédigé"}
              >
                {artifactOpen ? (
                  <PanelRightClose className="size-4" />
                ) : (
                  <PanelRightOpen className="size-4" />
                )}
              </Button>
            </div>
          </header>

          <div
            ref={messagesViewportRef}
            className="min-h-0 flex-1 overflow-y-auto"
          >
            <div className="mx-auto max-w-3xl space-y-6 px-4 py-8 sm:px-6">
              {messages.length === 0 && !initializing ? (
                <div className="flex min-h-[360px] flex-col items-center justify-center text-center">
                  <div className="rounded-2xl bg-slate-900 p-3 text-white">
                    <MessageSquareText className="size-6" />
                  </div>
                  <h2 className="mt-4 text-xl font-semibold tracking-tight text-slate-950">
                    Comment voulez-vous construire l’état de l’art ?
                  </h2>
                  <p className="mt-2 max-w-xl text-sm leading-6 text-slate-500">
                    Parlez naturellement du plan, des articles à rechercher, des
                    verrous ou du niveau d’argumentation. La rédaction reste
                    disponible comme artefact latéral.
                  </p>
                  <div className="mt-5 flex flex-wrap justify-center gap-2">
                    {[
                      "Donne-moi le plan actuel",
                      "Vérifie la couverture des deux verrous",
                      "Recherche une preuve directe manquante",
                    ].map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => setInput(suggestion)}
                        className="rounded-xl border border-slate-200 px-3 py-2 text-xs text-slate-600 hover:bg-slate-50"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                messages.map((message, index) => {
                  const consultant = message.role === "consultant"
                  return (
                    <div
                      key={message.message_id || `${message.role}-${index}`}
                      className={`flex gap-3 ${
                        consultant ? "justify-end" : "justify-start"
                      }`}
                    >
                      {!consultant && (
                        <div className="mt-0.5 h-fit rounded-full bg-slate-900 p-2 text-white">
                          <Bot className="size-3.5" />
                        </div>
                      )}
                      <div
                        className={`max-w-[88%] whitespace-pre-wrap text-sm leading-7 ${
                          consultant
                            ? "rounded-3xl rounded-br-md bg-[#f4f4f4] px-4 py-2.5 text-slate-900"
                            : "py-1 text-slate-800"
                        }`}
                      >
                        {citationText(message.content)}
                      </div>
                    </div>
                  )
                })
              )}

              {candidates.length > 0 && (
                <div className="space-y-3 border-t border-slate-200 pt-5">
                  <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">
                    <Search className="size-3.5" />
                    Sources proposées
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    {candidates.slice(0, 12).map((candidate) => (
                      <CandidateCard
                        key={candidate.candidate_id}
                        candidate={candidate}
                        busy={decidingId === candidate.candidate_id}
                        onDecision={(row, value) => void decide(row, value)}
                      />
                    ))}
                  </div>
                </div>
              )}

              {(initializing || loading || generating) && (
                <div className="flex items-center gap-2 py-2 text-sm text-slate-500">
                  <Loader2 className="size-4 animate-spin" />
                  {generating
                    ? "Rédaction argumentée en cours…"
                    : "Analyse de votre demande…"}
                </div>
              )}

              {notice && (
                <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                  {notice}
                </div>
              )}
              {(error || generationError) && (
                <div className="rounded-xl border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">
                  {error || generationError}
                </div>
              )}
            </div>
          </div>

          <div className="shrink-0 border-t border-slate-100 bg-white px-4 pb-4 pt-3 sm:px-6">
            <div className="mx-auto max-w-3xl">
              <div className="rounded-[24px] border border-slate-300 bg-white p-2 shadow-[0_8px_28px_rgba(15,23,42,0.08)] focus-within:border-slate-400">
                <Textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  disabled={initializing || loading || generating}
                  placeholder="Écrivez votre demande sur le plan, la recherche ou la rédaction…"
                  rows={3}
                  className="min-h-[70px] resize-none border-0 bg-transparent shadow-none focus-visible:ring-0"
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault()
                      void submitMessage()
                    }
                  }}
                />
                <div className="flex items-center justify-between px-1 pb-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="rounded-xl text-xs text-slate-500"
                    onClick={() => setCorpusOpen(true)}
                  >
                    <FilePlus2 className="mr-1.5 size-4" />
                    Ajouter une source
                  </Button>
                  <Button
                    type="button"
                    size="icon"
                    className="rounded-full bg-slate-900 hover:bg-slate-800"
                    disabled={!canSend}
                    onClick={() => void submitMessage()}
                    aria-label="Envoyer le message"
                  >
                    {loading ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Send className="size-4" />
                    )}
                  </Button>
                </div>
              </div>
              <p className="mt-2 text-center text-[11px] text-slate-400">
                Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne
              </p>
            </div>
          </div>
        </main>

        {artifactOpen && (
          <aside className="absolute inset-y-0 right-0 z-30 flex w-[min(94vw,620px)] shrink-0 flex-col border-l border-slate-200 bg-[#fafafa] shadow-2xl lg:w-[min(70%,620px)] 2xl:static 2xl:z-auto 2xl:w-[min(40vw,620px)] 2xl:shadow-none">
            <header className="flex h-16 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-4">
              <div>
                <p className="flex items-center gap-2 text-sm font-semibold text-slate-950">
                  <FileText className="size-4 text-blue-600" />
                  État de l’art
                </p>
                <p className="mt-0.5 text-xs text-slate-500">
                  Artefact rédigé · {wordCount.toLocaleString("fr-FR")} mots
                </p>
              </div>
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => navigator.clipboard?.writeText(draftMarkdown)}
                  title="Copier le document"
                >
                  <Copy className="size-4" />
                </Button>
                {onRefreshDraft && (
                  <Button
                    variant="ghost"
                    size="icon"
                    disabled={generating}
                    onClick={() => void onRefreshDraft()}
                    title="Actualiser le document"
                  >
                    <RefreshCw
                      className={`size-4 ${generating ? "animate-spin" : ""}`}
                    />
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={() => setArtifactOpen(false)}
                  title="Fermer l’artefact"
                >
                  <PanelRightClose className="size-4" />
                </Button>
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-x-hidden overflow-y-auto [overflow-wrap:anywhere]">
              <DraftPreview markdown={draftMarkdown} />
            </div>
          </aside>
        )}

        {!artifactOpen && (
          <button
            type="button"
            onClick={() => hasDraft && setArtifactOpen(true)}
            disabled={!hasDraft}
            className={`hidden w-14 shrink-0 flex-col items-center justify-center gap-3 border-l border-slate-200 2xl:flex ${
              hasDraft
                ? "bg-slate-50 text-slate-600 hover:bg-blue-50 hover:text-blue-700"
                : "cursor-not-allowed bg-slate-50 text-slate-300"
            }`}
            title={hasDraft ? "Ouvrir l’état de l’art" : "Aucun état de l’art rédigé"}
          >
            <FileText className="size-5" />
            <span className="[writing-mode:vertical-rl] text-xs font-semibold uppercase tracking-[0.16em]">
              État de l’art
            </span>
          </button>
        )}
      </div>

      <CorpusDialog
        open={corpusOpen}
        onOpenChange={setCorpusOpen}
        articles={selectedArticles}
        busyArticleId={busyArticleId}
        preparing={preparingCorpus}
        onRemove={(article) => void removeArticle(article)}
        onSearch={(query) => void searchNewArticle(query)}
        onUpload={(file) => void uploadArticleFromComputer(file)}
        onConsult={(article) => void consultArticle(article)}
      />

      <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
        <SheetContent side="left" className="w-[88vw] max-w-sm gap-0 p-0">
          <SheetHeader className="border-b pr-14">
            <SheetTitle>Conversations du projet</SheetTitle>
            <SheetDescription>
              Chaque conversation conserve son propre historique scientifique.
            </SheetDescription>
          </SheetHeader>
          <div className="border-b p-3">
            <Button
              className="w-full justify-start rounded-xl bg-slate-900 text-white"
              onClick={() => {
                setMobileSidebarOpen(false)
                void createConversation()
              }}
              disabled={initializing}
            >
              <MessageSquarePlus className="mr-2 size-4" />
              Nouvelle conversation
            </Button>
            <Button
              variant="outline"
              className="mt-2 w-full justify-between rounded-xl"
              onClick={() => {
                setMobileSidebarOpen(false)
                setCorpusOpen(true)
              }}
            >
              <span className="flex items-center gap-2">
                <Library className="size-4 text-blue-600" />
                Corpus de rédaction
              </span>
              <Badge variant="outline">{selectedArticles.length}</Badge>
            </Button>
            {onBackToArticles && (
              <Button
                variant="ghost"
                className="mt-2 w-full justify-start rounded-xl"
                onClick={() => {
                  setMobileSidebarOpen(false)
                  onBackToArticles()
                }}
              >
                <ChevronLeft className="mr-2 size-4" />
                Revenir aux articles
              </Button>
            )}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            {sessions.map((session) => (
              <div
                key={session.session_id}
                className={`mb-1 flex items-start rounded-xl ${
                  session.session_id === sessionId
                    ? "bg-slate-100 ring-1 ring-slate-200"
                    : "hover:bg-slate-50"
                }`}
              >
                <button
                  type="button"
                  onClick={() => {
                    setMobileSidebarOpen(false)
                    void openSession(session.session_id)
                  }}
                  className="min-w-0 flex-1 px-3 py-2.5 text-left"
                >
                  <p className="line-clamp-2 text-sm font-medium text-slate-800">
                    {sessionLabel(session)}
                  </p>
                  <p className="mt-1 text-[11px] text-slate-400">
                    {formatSessionDate(session.updated_at)} ·{" "}
                    {session.message_count || 0} message(s)
                  </p>
                </button>
                <button
                  type="button"
                  disabled={deletingSessionId === session.session_id}
                  onClick={() => void removeConversation(session)}
                  className="mr-1 mt-1.5 inline-flex size-9 items-center justify-center rounded-lg text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                  aria-label={`Supprimer la conversation ${sessionLabel(session)}`}
                >
                  {deletingSessionId === session.session_id ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Trash2 className="size-4" />
                  )}
                </button>
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </>
  )
}

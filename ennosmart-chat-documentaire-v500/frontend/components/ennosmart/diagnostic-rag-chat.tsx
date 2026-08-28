"use client"

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react"
import {
  AlertCircle,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  ExternalLink,
  FileSearch,
  FileText,
  Filter,
  Loader2,
  Maximize2,
  MessageCircle,
  Minimize2,
  PanelTopClose,
  PanelTopOpen,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  User,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { getAccessToken } from "@/lib/api"
import {
  useProjectSourceDocuments,
  type SourceEvidence,
} from "@/components/ennosmart/source-documents-dialog"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

type ChatRole = "user" | "assistant"

type ChatSource = {
  evidence_id: string
  rag_chunk_id?: string | null
  passage_id?: string | null
  document_id?: string | number | null
  source_path?: string | null
  source_kind?: string
  role?: string
  final_role?: string
  document: string
  filename?: string | null
  section_title?: string
  year?: string | number | null
  page_number?: number | null
  paragraph_index?: number | null
  char_start?: number | null
  char_end?: number | null
  evidence_nature?: string | null
  support_score?: number | null
  excerpt: string
}

type ChatMessage = {
  id: string
  role: ChatRole
  content: string
  sources?: ChatSource[]
  scopeLabel?: string | null
}

type ChatStatus = {
  ready: boolean
  reason?: string
  chunks_count?: number
  latest_run_id?: number | null
}

type PreviewState = {
  loading: boolean
  error: string
  objectUrl: string
  mediaType: string
  page: number | null
  exact: boolean | null
}

type EvidenceFilter = "all" | "client" | "diagnostic"

interface DiagnosticRagChatProps {
  projectId: number
  refreshToken?: string
}

const EMPTY_PREVIEW: PreviewState = {
  loading: false,
  error: "",
  objectUrl: "",
  mediaType: "",
  page: null,
  exact: null,
}

const suggestions = [
  "De quoi parlent les documents clients ?",
  "Quel est l'objectif exact du projet ?",
  "Quels sont les verrous R&D candidats ?",
  "Quels sont les points forts et les points faibles du dossier ?",
]

function newId() {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID()
  }

  return `${Date.now()}-${Math.random()}`
}

function normalizeDocumentName(value?: string | null) {
  return String(value || "")
    .replace(/\\/g, "/")
    .split("/")
    .pop()!
    .replace(/_[a-f0-9]{10,64}(?=\.[^.]+$)/i, "")
    .replace(/\.(pdf|docx?|docm|xlsx?|pptx?|msg|txt|json)$/i, "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function filenameOf(source: ChatSource) {
  return (
    source.filename ||
    source.document ||
    source.source_path?.replace(/\\/g, "/").split("/").pop() ||
    "Document source"
  )
}

function shortText(value: unknown, max = 260) {
  const text = String(value || "").replace(/\s+/g, " ").trim()
  if (text.length <= max) return text
  return `${text.slice(0, max - 1)}…`
}

function pageLabel(source: ChatSource) {
  if (
    typeof source.page_number === "number" &&
    Number.isFinite(source.page_number)
  ) {
    return `Page ${source.page_number <= 0 ? 1 : source.page_number}`
  }

  if (
    typeof source.paragraph_index === "number" &&
    Number.isFinite(source.paragraph_index)
  ) {
    return `Paragraphe ${source.paragraph_index + 1}`
  }

  return "Passage"
}

function locationLabel(source: ChatSource) {
  const values: string[] = []

  if (
    typeof source.page_number === "number" &&
    Number.isFinite(source.page_number) &&
    source.page_number >= 0
  ) {
    values.push(`p.${source.page_number === 0 ? 1 : source.page_number}`)
  }

  if (
    typeof source.paragraph_index === "number" &&
    Number.isFinite(source.paragraph_index) &&
    source.paragraph_index >= 0
  ) {
    values.push(`§${source.paragraph_index + 1}`)
  }

  return values.join(" · ")
}

function evidenceNatureLabel(value?: string | null) {
  switch (value) {
    case "objectif":
      return "Objectif"
    case "resultat_mesure":
      return "Résultat mesuré"
    case "hypothese_ou_piste":
      return "Hypothèse / piste"
    case "methode_ou_parametre":
      return "Méthode / paramètre"
    case "limite_ou_incertitude":
      return "Limite / incertitude"
    case "analyse_agent_secondaire":
      return "Analyse secondaire"
    default:
      return "Preuve documentaire"
  }
}

function toSourceEvidence(source: ChatSource): SourceEvidence {
  return {
    evidence_id: source.evidence_id,
    rag_chunk_id: source.rag_chunk_id,
    passage_id: source.passage_id,
    document_id: source.document_id,
    document: source.document,
    filename: source.filename || source.document,
    source_path: source.source_path,
    year: source.year,
    page_number: source.page_number,
    paragraph_index: source.paragraph_index,
    char_start: source.char_start,
    char_end: source.char_end,
    section_title: source.section_title,
    role: source.role,
    excerpt: source.excerpt,
    text: source.excerpt,
    metadata: {
      source_kind: source.source_kind || "nlp_rag",
      final_role: source.final_role || "",
    },
  }
}

function sourceHighlightPayload(source: ChatSource) {
  return {
    excerpt: String(source.excerpt || "").slice(0, 8000),
    document_id:
      source.document_id === null || source.document_id === undefined
        ? null
        : Number(source.document_id),
    source_path: source.source_path || null,
    source_name: source.filename || source.document || null,
    document_name: source.document || source.filename || null,
    title: source.section_title || null,
    role: source.role || source.final_role || null,
    passage_id: source.passage_id || source.rag_chunk_id || source.evidence_id,
    page_number: source.page_number ?? null,
    paragraph_index: source.paragraph_index ?? null,
    char_start: source.char_start ?? null,
    char_end: source.char_end ?? null,
    year:
      source.year === null || source.year === undefined
        ? null
        : String(source.year),
    return_json: false,
  }
}

function sourceKey(source: ChatSource, index = 0) {
  return String(
    source.passage_id ||
      source.rag_chunk_id ||
      source.evidence_id ||
      `${filenameOf(source)}:${source.excerpt.slice(0, 80)}:${index}`
  )
}

function uniqueSources(rows: ChatSource[]) {
  const seen = new Set<string>()
  const result: ChatSource[] = []

  rows.forEach((source, index) => {
    const key = sourceKey(source, index)
    if (seen.has(key)) return
    seen.add(key)
    result.push(source)
  })

  return result
}

function sourceMatchesFilter(
  source: ChatSource,
  filter: EvidenceFilter
) {
  if (filter === "all") return true
  if (filter === "client") return source.source_kind === "client_raw"
  return source.source_kind === "diagnostic_output"
}

export function DiagnosticRagChat({
  projectId,
  refreshToken = "",
}: DiagnosticRagChatProps) {
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(true)
  const [input, setInput] = useState("")
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null)
  const [selectedDocumentId, setSelectedDocumentId] = useState("")
  const [selectedSource, setSelectedSource] = useState<ChatSource | null>(null)
  const [preview, setPreview] = useState<PreviewState>(EMPTY_PREVIEW)
  const [evidenceOpen, setEvidenceOpen] = useState(true)
  const [evidenceFilter, setEvidenceFilter] =
    useState<EvidenceFilter>("all")

  const bottomRef = useRef<HTMLDivElement>(null)
  const previewObjectUrlRef = useRef("")

  const sourceDocuments = useProjectSourceDocuments(projectId)

  const documentOptions = useMemo(
    () =>
      [...sourceDocuments].sort((left, right) =>
        String(left.filename || left.stored_filename || "").localeCompare(
          String(right.filename || right.stored_filename || ""),
          "fr"
        )
      ),
    [sourceDocuments]
  )

  const selectedDocument = useMemo(
    () =>
      documentOptions.find(
        (document) => String(document.id) === selectedDocumentId
      ) || null,
    [documentOptions, selectedDocumentId]
  )

  const selectedDocumentName = selectedDocument
    ? String(
        selectedDocument.filename ||
          selectedDocument.stored_filename ||
          `Document ${selectedDocument.id}`
      )
    : ""

  const latestAssistantMessage = useMemo(
    () =>
      [...messages]
        .reverse()
        .find(
          (message) =>
            message.role === "assistant" &&
            Array.isArray(message.sources) &&
            message.sources.length > 0
        ) || null,
    [messages]
  )

  const latestSources = useMemo(
    () => uniqueSources(latestAssistantMessage?.sources || []),
    [latestAssistantMessage]
  )

  const visibleSources = useMemo(
    () =>
      latestSources.filter((source) =>
        sourceMatchesFilter(source, evidenceFilter)
      ),
    [latestSources, evidenceFilter]
  )

  const clientSourcesCount = useMemo(
    () =>
      latestSources.filter((source) => source.source_kind === "client_raw")
        .length,
    [latestSources]
  )

  const diagnosticSourcesCount = useMemo(
    () =>
      latestSources.filter(
        (source) => source.source_kind === "diagnostic_output"
      ).length,
    [latestSources]
  )

  const history = useMemo(
    () =>
      messages.slice(-10).map((message) => ({
        role: message.role,
        content: message.content,
      })),
    [messages]
  )

  const loadStatus = async () => {
    const token = getAccessToken()

    if (!token || !projectId) {
      setStatus(null)
      return
    }

    try {
      const response = await fetch(
        `${API_BASE_URL}/projects/${projectId}/diagnostic-chat/status`,
        {
          headers: {
            Authorization: `Bearer ${token}`,
          },
          cache: "no-store",
        }
      )

      if (!response.ok) {
        setStatus(null)
        return
      }

      const data = await response.json()

      setStatus({
        ready: Boolean(data?.ready),
        reason: data?.reason,
        chunks_count: data?.chunks_count,
        latest_run_id: data?.latest_run_id,
      })
    } catch {
      setStatus(null)
    }
  }

  const revokePreviewUrl = () => {
    if (!previewObjectUrlRef.current) return

    URL.revokeObjectURL(previewObjectUrlRef.current)
    previewObjectUrlRef.current = ""
  }

  const loadSourcePreview = async (source: ChatSource) => {
    setSelectedSource(source)
    setPreview({
      ...EMPTY_PREVIEW,
      loading: true,
    })

    try {
      const token = getAccessToken()

      if (!token) {
        throw new Error("Utilisateur non authentifié.")
      }

      const response = await fetch(
        `${API_BASE_URL}/projects/${projectId}/source-highlight/preview`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify(sourceHighlightPayload(source)),
          cache: "no-store",
        }
      )

      if (!response.ok) {
        let detail = ""

        try {
          const payload = await response.json()
          detail = String(payload?.detail || "")
        } catch {
          detail = await response.text().catch(() => "")
        }

        throw new Error(
          detail || `Aperçu indisponible (HTTP ${response.status}).`
        )
      }

      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)
      const pageRaw = Number(
        response.headers.get("X-EnnoSmart-Highlight-Page")
      )
      const exactRaw = response.headers.get(
        "X-EnnoSmart-Highlight-Exact"
      )

      revokePreviewUrl()
      previewObjectUrlRef.current = objectUrl

      setPreview({
        loading: false,
        error: "",
        objectUrl,
        mediaType:
          response.headers.get("Content-Type") ||
          blob.type ||
          "application/octet-stream",
        page:
          Number.isFinite(pageRaw) && pageRaw > 0
            ? pageRaw
            : source.page_number || null,
        exact:
          exactRaw === "true"
            ? true
            : exactRaw === "false"
              ? false
              : null,
      })
    } catch (previewError) {
      setPreview({
        ...EMPTY_PREVIEW,
        error:
          previewError instanceof Error
            ? previewError.message
            : "Aperçu documentaire indisponible.",
      })
    }
  }

  const selectSource = (source: ChatSource) => {
    void loadSourcePreview(source)
  }

  const useSourceAsScope = (source: ChatSource) => {
    const sourceId = String(source.document_id || "")

    const byId = sourceId
      ? documentOptions.find(
          (document) => String(document.id) === sourceId
        )
      : undefined

    const sourceNameKey = normalizeDocumentName(
      source.filename || source.document
    )

    const byName = documentOptions.find(
      (document) =>
        normalizeDocumentName(
          document.filename || document.stored_filename
        ) === sourceNameKey
    )

    const matched = byId || byName

    if (matched) {
      setSelectedDocumentId(String(matched.id))
    }
  }

  const sendMessage = async (suggestedText?: string) => {
    const content = (suggestedText ?? input).trim()

    if (!content || sending || !status?.ready) return

    const userMessage: ChatMessage = {
      id: newId(),
      role: "user",
      content,
      scopeLabel: selectedDocumentName || "Tous les documents",
    }

    setMessages((current) => [...current, userMessage])
    setInput("")
    setError("")
    setSending(true)

    try {
      const token = getAccessToken()

      if (!token) {
        throw new Error("Utilisateur non authentifié.")
      }

      const response = await fetch(
        `${API_BASE_URL}/projects/${projectId}/diagnostic-chat/messages`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            message: content,
            history,
            document_scope: selectedDocument
              ? {
                  document_id: selectedDocument.id,
                  document_name: selectedDocumentName,
                }
              : null,
          }),
        }
      )

      let payload: any = null

      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      if (!response.ok) {
        throw new Error(
          typeof payload?.detail === "string"
            ? payload.detail
            : "Impossible d'interroger le chat RAG."
        )
      }

      const sources = Array.isArray(payload?.sources)
        ? (payload.sources as ChatSource[])
        : []

      const assistantMessage: ChatMessage = {
        id: newId(),
        role: "assistant",
        content:
          String(payload?.answer || "").trim() ||
          "Aucune réponse exploitable n'a été produite.",
        sources,
        scopeLabel:
          String(payload?.document_scope?.document_name || "").trim() ||
          selectedDocumentName ||
          "Tous les documents",
      }

      setMessages((current) => [...current, assistantMessage])

      if (sources.length > 0) {
        setEvidenceOpen(true)
        setEvidenceFilter("all")
        void loadSourcePreview(sources[0])
      }
    } catch (requestError) {
      const message =
        requestError instanceof Error
          ? requestError.message
          : "Erreur inconnue pendant la requête RAG."

      setError(message)

      setMessages((current) => [
        ...current,
        {
          id: newId(),
          role: "assistant",
          content: `Je n'ai pas pu répondre : ${message}`,
          sources: [],
        },
      ])
    } finally {
      setSending(false)
    }
  }

  const handleKeyDown = (
    event: KeyboardEvent<HTMLTextAreaElement>
  ) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      void sendMessage()
    }
  }

  const copyMessage = async (message: ChatMessage) => {
    await navigator.clipboard.writeText(message.content)
    setCopiedMessageId(message.id)

    window.setTimeout(() => {
      setCopiedMessageId(null)
    }, 1600)
  }

  const resetConversation = () => {
    setMessages([])
    setInput("")
    setError("")
    setSelectedSource(null)
    setEvidenceFilter("all")
    revokePreviewUrl()
    setPreview(EMPTY_PREVIEW)
  }

  const openAssistant = () => {
    setOpen(true)
    setExpanded(true)
  }

  useEffect(() => {
    setOpen(false)
    setExpanded(true)
    setMessages([])
    setInput("")
    setError("")
    setSelectedDocumentId("")
    setSelectedSource(null)
    setEvidenceFilter("all")
    setEvidenceOpen(true)
    revokePreviewUrl()
    setPreview(EMPTY_PREVIEW)
    void loadStatus()

    return () => {
      revokePreviewUrl()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, refreshToken])

  useEffect(() => {
    if (status?.ready) return

    const interval = window.setInterval(() => {
      void loadStatus()
    }, 12000)

    return () => window.clearInterval(interval)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status?.ready, projectId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({
      behavior: "smooth",
    })
  }, [messages, sending])

  useEffect(() => {
    if (latestSources.length === 0) return
    if (selectedSource) {
      const stillVisible = latestSources.some(
        (source, index) =>
          sourceKey(source, index) === sourceKey(selectedSource)
      )
      if (stillVisible) return
    }

    void loadSourcePreview(latestSources[0])
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestAssistantMessage?.id])

  if (!status?.ready) {
    return null
  }

  const previewIsPdf = preview.mediaType
    .toLowerCase()
    .includes("pdf")

  const viewerUrl =
    preview.objectUrl && previewIsPdf && preview.page
      ? `${preview.objectUrl}#page=${preview.page}&zoom=page-width`
      : preview.objectUrl

  const windowClass = expanded
    ? "fixed inset-2 z-[91] flex flex-col overflow-hidden rounded-[22px] border border-border/80 bg-background shadow-[0_24px_80px_rgba(31,23,58,0.22)] sm:inset-3 lg:left-[270px]"
    : "fixed bottom-24 right-4 z-[91] flex h-[min(720px,calc(100vh-7rem))] w-[min(520px,calc(100vw-2rem))] flex-col overflow-hidden rounded-[22px] border border-border bg-background shadow-2xl"

  const evidenceHeightClass =
    expanded && evidenceOpen
      ? "max-h-[210px]"
      : evidenceOpen
        ? "max-h-[160px]"
        : "max-h-[58px]"

  return (
    <>
      {open && expanded ? (
        <button
          type="button"
          className="fixed inset-0 z-[90] bg-slate-950/10 backdrop-blur-[1px]"
          aria-label="Fermer l'assistant documentaire"
          onClick={() => setOpen(false)}
        />
      ) : null}

      <Button
        type="button"
        onClick={() => {
          if (open) {
            setOpen(false)
          } else {
            openAssistant()
          }
        }}
        className="fixed bottom-6 right-6 z-[93] size-14 rounded-full bg-brand p-0 shadow-[0_12px_32px_rgba(93,63,211,0.34)] hover:bg-brand/90"
        aria-label={
          open
            ? "Fermer le chat documentaire"
            : "Ouvrir le chat documentaire"
        }
        title="Assistant documentaire du dossier"
      >
        {open ? (
          <X className="size-5" />
        ) : (
          <MessageCircle className="size-5" />
        )}
      </Button>

      {open ? (
        <section
          className={windowClass}
          role="dialog"
          aria-modal="true"
          aria-label="Assistant documentaire EnnoDiagnostic"
        >
          {/* HEADER */}
          <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border/70 bg-background px-4 sm:px-5">
            <div className="grid size-10 shrink-0 place-items-center rounded-xl bg-brand shadow-sm">
              <Sparkles className="size-4.5 text-brand-foreground" />
            </div>

            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold text-foreground sm:text-[15px]">
                Assistant documentaire
              </p>
              <p className="truncate text-[11px] font-medium text-brand">
                EnnoDiagnostic
              </p>
            </div>

            <div className="hidden items-center gap-2 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-1 md:flex">
              <span className="size-2 rounded-full bg-emerald-500" />
              <span className="text-[10px] font-semibold text-emerald-700">
                Prêt
              </span>
            </div>

            {preview.objectUrl ? (
              <Button
                type="button"
                variant="ghost"
                size="icon"
                className="size-9 rounded-xl border border-transparent hover:border-border"
                onClick={() => {
                  if (!preview.objectUrl) return
                  const anchor = document.createElement("a")
                  anchor.href = preview.objectUrl
                  anchor.download = `${filenameOf(
                    selectedSource ||
                      ({
                        document: "document",
                      } as ChatSource)
                  )}-extrait`
                  anchor.click()
                }}
                title="Télécharger l'aperçu"
              >
                <Download className="size-4" />
              </Button>
            ) : null}

            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-9 rounded-xl border border-transparent hover:border-border"
              onClick={() => setExpanded((current) => !current)}
              title={
                expanded
                  ? "Réduire la fenêtre"
                  : "Agrandir la fenêtre"
              }
            >
              {expanded ? (
                <Minimize2 className="size-4" />
              ) : (
                <Maximize2 className="size-4" />
              )}
            </Button>

            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-9 rounded-xl border border-transparent hover:border-border"
              onClick={() => setOpen(false)}
              title="Fermer"
            >
              <X className="size-4" />
            </Button>
          </header>

          {/* PASSAGES / PREUVES AU-DESSUS */}
          <section
            className={`shrink-0 overflow-hidden border-b border-violet-100/80 bg-[linear-gradient(180deg,rgba(246,243,255,0.75),rgba(255,255,255,0.96))] transition-[max-height] duration-200 ${evidenceHeightClass}`}
          >
            <div className="flex items-center gap-3 px-4 py-2.5 sm:px-5">
              <div className="grid size-7 shrink-0 place-items-center rounded-lg bg-brand/10 text-brand">
                <Sparkles className="size-3.5" />
              </div>

              <div className="min-w-0">
                <p className="text-xs font-semibold text-foreground">
                  Passages et preuves
                </p>
                <p className="hidden text-[10px] text-muted-foreground sm:block">
                  Les passages utilisés pour répondre apparaissent ici, avant le document et le chat.
                </p>
              </div>

              <div className="ml-auto flex min-w-0 items-center gap-2">
                <div className="relative hidden sm:block">
                  <Filter className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-brand" />
                  <select
                    value={selectedDocumentId}
                    onChange={(event) => {
                      setSelectedDocumentId(event.target.value)
                    }}
                    className="h-8 max-w-[230px] rounded-lg border border-border bg-background pl-8 pr-7 text-[10px] font-medium text-foreground outline-none transition focus:border-brand"
                    title="Portée de la recherche"
                  >
                    <option value="">
                      Tous les documents
                    </option>
                    {documentOptions.map((document) => (
                      <option
                        key={document.id}
                        value={String(document.id)}
                      >
                        {document.filename ||
                          document.stored_filename ||
                          `Document ${document.id}`}
                      </option>
                    ))}
                  </select>
                </div>

                {latestSources.length > 0 ? (
                  <div className="hidden rounded-lg border bg-background p-0.5 lg:flex">
                    <button
                      type="button"
                      onClick={() => setEvidenceFilter("all")}
                      className={`rounded-md px-2.5 py-1 text-[10px] font-semibold transition ${
                        evidenceFilter === "all"
                          ? "bg-brand/10 text-brand"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      Tous {latestSources.length}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setEvidenceFilter("client")
                      }
                      className={`rounded-md px-2.5 py-1 text-[10px] font-semibold transition ${
                        evidenceFilter === "client"
                          ? "bg-brand/10 text-brand"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      Clients {clientSourcesCount}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        setEvidenceFilter("diagnostic")
                      }
                      className={`rounded-md px-2.5 py-1 text-[10px] font-semibold transition ${
                        evidenceFilter === "diagnostic"
                          ? "bg-brand/10 text-brand"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      Diagnostic {diagnosticSourcesCount}
                    </button>
                  </div>
                ) : null}

                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="h-8 gap-1 rounded-lg px-2 text-[10px]"
                  onClick={() =>
                    setEvidenceOpen((current) => !current)
                  }
                >
                  {evidenceOpen ? "Masquer" : "Afficher"}
                  {evidenceOpen ? (
                    <PanelTopClose className="size-3.5" />
                  ) : (
                    <PanelTopOpen className="size-3.5" />
                  )}
                </Button>
              </div>
            </div>

            {evidenceOpen ? (
              <div className="overflow-x-auto px-4 pb-3 sm:px-5">
                {latestSources.length === 0 ? (
                  <div className="flex min-h-[78px] items-center justify-center rounded-xl border border-dashed border-violet-200/80 bg-background/70 px-4">
                    <div className="text-center">
                      <p className="text-[11px] font-semibold text-foreground">
                        Les passages apparaîtront ici après votre première question.
                      </p>
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        Cliquez ensuite sur un passage pour ouvrir le document directement à l'endroit surligné.
                      </p>
                    </div>
                  </div>
                ) : visibleSources.length === 0 ? (
                  <div className="flex min-h-[78px] items-center justify-center rounded-xl border border-dashed bg-background/70 px-4 text-[11px] text-muted-foreground">
                    Aucun passage pour ce filtre.
                  </div>
                ) : (
                  <div className="flex min-w-max gap-2.5">
                    {visibleSources.map((source, index) => {
                      const key = sourceKey(source, index)
                      const selected =
                        selectedSource &&
                        sourceKey(selectedSource) === key

                      return (
                        <button
                          key={key}
                          type="button"
                          onClick={() => selectSource(source)}
                          className={`group flex w-[300px] shrink-0 flex-col rounded-xl border bg-background p-3 text-left shadow-sm transition hover:-translate-y-[1px] hover:shadow-md xl:w-[340px] ${
                            selected
                              ? "border-brand/60 ring-2 ring-brand/10"
                              : "border-border/80 hover:border-brand/30"
                          }`}
                        >
                          <div className="flex items-center gap-2">
                            <Badge
                              variant="outline"
                              className={`h-5 px-1.5 text-[9px] ${
                                selected
                                  ? "border-brand/30 bg-brand/10 text-brand"
                                  : ""
                              }`}
                            >
                              [P{index + 1}]
                            </Badge>

                            <span className="text-[10px] font-semibold text-muted-foreground">
                              {pageLabel(source)}
                            </span>

                            <span className="ml-auto text-[9px] text-muted-foreground">
                              {evidenceNatureLabel(
                                source.evidence_nature
                              )}
                            </span>
                          </div>

                          <p className="mt-2 line-clamp-3 min-h-[48px] text-[11px] leading-4.5 text-foreground">
                            {shortText(source.excerpt, 250)}
                          </p>

                          <div className="mt-2.5 flex items-center gap-2 border-t border-border/60 pt-2">
                            <FileText className="size-3.5 shrink-0 text-brand" />
                            <span className="min-w-0 flex-1 truncate text-[9px] font-medium text-muted-foreground">
                              {filenameOf(source)}
                            </span>
                            <span className="text-[9px] text-muted-foreground">
                              {locationLabel(source)}
                            </span>
                            <span className="inline-flex items-center gap-1 text-[9px] font-semibold text-brand">
                              Voir
                              <ExternalLink className="size-3" />
                            </span>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            ) : null}
          </section>

          {/* MAIN */}
          <div
            className={`min-h-0 flex-1 ${
              expanded
                ? "grid lg:grid-cols-[minmax(0,1.2fr)_minmax(420px,0.95fr)]"
                : "flex"
            }`}
          >
            {/* DOCUMENT VIEWER */}
            {expanded ? (
              <section className="flex min-h-0 min-w-0 flex-col border-r border-border/70 bg-slate-50/50">
                <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/70 bg-background px-3">
                  <FileSearch className="size-4 text-brand" />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[11px] font-semibold text-foreground">
                      {selectedSource
                        ? filenameOf(selectedSource)
                        : "Document source"}
                    </p>
                  </div>

                  {preview.page ? (
                    <Badge
                      variant="outline"
                      className="h-6 bg-background text-[9px]"
                    >
                      Page {preview.page}
                    </Badge>
                  ) : null}

                  {preview.objectUrl ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-7 rounded-lg"
                      onClick={() => {
                        window.open(
                          viewerUrl,
                          "_blank",
                          "noopener,noreferrer"
                        )
                      }}
                      title="Ouvrir dans un nouvel onglet"
                    >
                      <ExternalLink className="size-3.5" />
                    </Button>
                  ) : null}
                </div>

                <div className="relative min-h-0 flex-1 overflow-hidden bg-[#f7f7fa]">
                  {preview.loading ? (
                    <div className="absolute inset-0 z-10 grid place-items-center bg-background/75 backdrop-blur-[1px]">
                      <div className="flex items-center gap-2 rounded-xl border bg-background px-4 py-3 text-xs text-muted-foreground shadow-sm">
                        <Loader2 className="size-4 animate-spin text-brand" />
                        Ouverture du document et surlignage…
                      </div>
                    </div>
                  ) : null}

                  {preview.error ? (
                    <div className="grid h-full place-items-center p-6">
                      <div className="max-w-md rounded-2xl border border-destructive/20 bg-destructive/5 p-5 text-center">
                        <AlertCircle className="mx-auto size-7 text-destructive" />
                        <p className="mt-2 text-sm font-semibold text-destructive">
                          Aperçu indisponible
                        </p>
                        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                          {preview.error}
                        </p>
                      </div>
                    </div>
                  ) : viewerUrl ? (
                    <iframe
                      key={viewerUrl}
                      src={viewerUrl}
                      title={
                        selectedSource
                          ? filenameOf(selectedSource)
                          : "Document source"
                      }
                      className="h-full w-full border-0 bg-white"
                    />
                  ) : (
                    <div className="grid h-full place-items-center p-6">
                      <div className="max-w-sm text-center">
                        <div className="mx-auto grid size-12 place-items-center rounded-2xl border border-violet-100 bg-violet-50 text-brand">
                          <Search className="size-5" />
                        </div>
                        <p className="mt-3 text-sm font-semibold text-foreground">
                          Sélectionnez un passage
                        </p>
                        <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                          Le document complet s'affichera ici directement au passage surligné.
                        </p>
                      </div>
                    </div>
                  )}
                </div>
              </section>
            ) : null}

            {/* CHAT */}
            <section className="flex min-h-0 min-w-0 flex-1 flex-col bg-background">
              <div className="flex h-11 shrink-0 items-center gap-2 border-b border-border/70 px-3.5">
                <div className="grid size-7 place-items-center rounded-lg bg-brand text-brand-foreground">
                  <Sparkles className="size-3.5" />
                </div>
                <p className="text-[11px] font-semibold text-foreground">
                  Assistant EnnoDiagnostic
                </p>

                <div className="ml-auto">
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="h-7 gap-1.5 rounded-lg px-2 text-[9px] text-muted-foreground"
                    onClick={resetConversation}
                    disabled={
                      messages.length === 0 &&
                      !input &&
                      !selectedSource
                    }
                  >
                    <RotateCcw className="size-3" />
                    Nouvelle conversation
                  </Button>
                </div>
              </div>

              <div className="min-h-0 flex-1 overflow-y-auto">
                <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-4 px-4 py-4">
                  {messages.length === 0 ? (
                    <div className="my-auto">
                      <div className="rounded-2xl border border-violet-100 bg-[linear-gradient(135deg,rgba(244,240,255,0.92),rgba(255,255,255,0.98))] p-4 shadow-sm">
                        <div className="flex gap-3">
                          <div className="grid size-8 shrink-0 place-items-center rounded-xl bg-brand text-brand-foreground">
                            <Bot className="size-4" />
                          </div>

                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-foreground">
                              Bonjour !
                            </p>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">
                              Je suis votre assistant documentaire. Posez-moi une question sur les documents du projet : je m'appuie sur les passages retrouvés pour répondre précisément et je les affiche au-dessus pour vérification.
                            </p>
                          </div>
                        </div>

                        <div className="mt-4 grid gap-2 sm:grid-cols-2">
                          {suggestions.map((suggestion) => (
                            <button
                              key={suggestion}
                              type="button"
                              onClick={() => void sendMessage(suggestion)}
                              className="rounded-xl border border-border bg-background px-3 py-2.5 text-left text-[10px] font-medium leading-4 text-foreground transition hover:border-brand/30 hover:bg-brand/[0.025]"
                            >
                              {suggestion}
                            </button>
                          ))}
                        </div>
                      </div>
                    </div>
                  ) : (
                    messages.map((message) => {
                      const isUser = message.role === "user"
                      const sources = uniqueSources(
                        message.sources || []
                      )
                      const primarySource = sources[0]

                      return (
                        <div
                          key={message.id}
                          className={`flex gap-2.5 ${
                            isUser
                              ? "justify-end"
                              : "justify-start"
                          }`}
                        >
                          {!isUser ? (
                            <div className="mt-1 grid size-7 shrink-0 place-items-center rounded-lg bg-brand text-brand-foreground">
                              <Sparkles className="size-3.5" />
                            </div>
                          ) : null}

                          <div
                            className={`group max-w-[88%] ${
                              isUser
                                ? "items-end"
                                : "items-start"
                            }`}
                          >
                            <div
                              className={`rounded-2xl px-3.5 py-3 text-xs leading-5 shadow-sm ${
                                isUser
                                  ? "rounded-tr-md border border-brand/15 bg-brand/[0.055] text-foreground"
                                  : "rounded-tl-md border border-border/70 bg-muted/25 text-foreground"
                              }`}
                            >
                              <p className="whitespace-pre-wrap">
                                {message.content}
                              </p>

                              {!isUser &&
                              primarySource ? (
                                <div className="mt-3">
                                  <p className="mb-1.5 text-[9px] font-semibold uppercase tracking-wide text-muted-foreground">
                                    Source principale
                                  </p>

                                  <button
                                    type="button"
                                    onClick={() =>
                                      selectSource(
                                        primarySource
                                      )
                                    }
                                    className="flex w-full items-center gap-2 rounded-xl border border-violet-100 bg-background px-3 py-2 text-left transition hover:border-brand/30 hover:bg-brand/[0.02]"
                                  >
                                    <FileText className="size-3.5 shrink-0 text-brand" />
                                    <Badge
                                      variant="outline"
                                      className="h-5 px-1.5 text-[8px]"
                                    >
                                      [P1]
                                    </Badge>
                                    <span className="min-w-0 flex-1 truncate text-[9px] font-medium text-foreground">
                                      {filenameOf(
                                        primarySource
                                      )}
                                    </span>
                                    <span className="text-[8px] text-muted-foreground">
                                      {locationLabel(
                                        primarySource
                                      )}
                                    </span>
                                    <span className="text-[9px] font-semibold text-brand">
                                      Voir le passage
                                    </span>
                                    <ExternalLink className="size-3 text-brand" />
                                  </button>

                                  {sources.length > 1 ? (
                                    <p className="mt-1.5 text-[9px] text-muted-foreground">
                                      + {sources.length - 1} autre
                                      {sources.length > 2
                                        ? "s"
                                        : ""}{" "}
                                      source
                                      {sources.length > 2
                                        ? "s"
                                        : ""}{" "}
                                      disponible
                                      {sources.length > 2
                                        ? "s"
                                        : ""}{" "}
                                      dans « Passages et preuves » ci-dessus.
                                    </p>
                                  ) : null}
                                </div>
                              ) : null}
                            </div>

                            <div
                              className={`mt-1 flex items-center gap-1 ${
                                isUser
                                  ? "justify-end"
                                  : "justify-start"
                              }`}
                            >
                              {message.scopeLabel ? (
                                <span className="max-w-[190px] truncate text-[8px] text-muted-foreground">
                                  {message.scopeLabel}
                                </span>
                              ) : null}

                              {!isUser ? (
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon"
                                  className="size-6 rounded-md text-muted-foreground opacity-0 transition group-hover:opacity-100"
                                  onClick={() =>
                                    void copyMessage(
                                      message
                                    )
                                  }
                                  title="Copier"
                                >
                                  {copiedMessageId ===
                                  message.id ? (
                                    <Check className="size-3" />
                                  ) : (
                                    <Copy className="size-3" />
                                  )}
                                </Button>
                              ) : null}
                            </div>
                          </div>

                          {isUser ? (
                            <div className="mt-1 grid size-7 shrink-0 place-items-center rounded-lg border bg-background text-muted-foreground">
                              <User className="size-3.5" />
                            </div>
                          ) : null}
                        </div>
                      )
                    })
                  )}

                  {sending ? (
                    <div className="flex items-center gap-2.5">
                      <div className="grid size-7 place-items-center rounded-lg bg-brand text-brand-foreground">
                        <Sparkles className="size-3.5" />
                      </div>
                      <div className="flex items-center gap-2 rounded-2xl rounded-tl-md border bg-muted/25 px-3.5 py-3 text-[10px] text-muted-foreground">
                        <Loader2 className="size-3.5 animate-spin text-brand" />
                        Analyse des passages pertinents…
                      </div>
                    </div>
                  ) : null}

                  {error ? (
                    <div className="flex items-start gap-2 rounded-xl border border-destructive/20 bg-destructive/5 px-3 py-2.5 text-[10px] text-destructive">
                      <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
                      <span>{error}</span>
                    </div>
                  ) : null}

                  <div ref={bottomRef} />
                </div>
              </div>

              {/* COMPOSER */}
              <footer className="shrink-0 border-t border-border/70 bg-background p-3">
                <div className="mx-auto max-w-3xl rounded-2xl border border-border bg-background shadow-[0_8px_28px_rgba(33,25,66,0.06)] transition focus-within:border-brand/40 focus-within:ring-2 focus-within:ring-brand/5">
                  <Textarea
                    value={input}
                    onChange={(event) =>
                      setInput(event.target.value)
                    }
                    onKeyDown={handleKeyDown}
                    placeholder="Posez une question précise sur ce dossier..."
                    className="min-h-[58px] resize-none border-0 bg-transparent px-3.5 py-3 text-xs shadow-none focus-visible:ring-0"
                  />

                  <div className="flex items-center gap-2 border-t border-border/50 px-2.5 py-2">
                    <div className="relative min-w-0 flex-1">
                      <Filter className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-brand" />
                      <select
                        value={selectedDocumentId}
                        onChange={(event) =>
                          setSelectedDocumentId(
                            event.target.value
                          )
                        }
                        className="h-8 max-w-full rounded-lg border-0 bg-brand/[0.035] pl-8 pr-7 text-[9px] font-medium text-foreground outline-none"
                      >
                        <option value="">
                          Tous les documents du projet
                        </option>
                        {documentOptions.map((document) => (
                          <option
                            key={document.id}
                            value={String(document.id)}
                          >
                            {document.filename ||
                              document.stored_filename ||
                              `Document ${document.id}`}
                          </option>
                        ))}
                      </select>
                    </div>

                    {selectedSource ? (
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        className="hidden h-8 rounded-lg px-2 text-[9px] text-brand lg:inline-flex"
                        onClick={() =>
                          useSourceAsScope(
                            selectedSource
                          )
                        }
                      >
                        Continuer sur ce document
                      </Button>
                    ) : null}

                    <Button
                      type="button"
                      size="sm"
                      className="h-8 gap-1.5 rounded-lg px-3 text-[10px]"
                      onClick={() => void sendMessage()}
                      disabled={
                        !input.trim() ||
                        sending ||
                        !status.ready
                      }
                    >
                      {sending ? (
                        <Loader2 className="size-3.5 animate-spin" />
                      ) : (
                        <Send className="size-3.5" />
                      )}
                      Envoyer
                    </Button>
                  </div>
                </div>

                <p className="mt-2 text-center text-[8px] leading-3 text-muted-foreground">
                  Cliquez sur un passage pour ouvrir le document au bon endroit. Vérification humaine requise.
                </p>
              </footer>
            </section>
          </div>
        </section>
      ) : null}
    </>
  )
}

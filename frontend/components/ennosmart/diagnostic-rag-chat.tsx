"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import {
  AlertCircle,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  Copy,
  FileText,
  Filter,
  Loader2,
  Maximize2,
  MessageCircle,
  Minimize2,
  RotateCcw,
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
  SourceTextWithDocuments,
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

interface DiagnosticRagChatProps {
  projectId: number
  refreshToken?: string
}

const suggestions = [
  "De quoi parlent les documents clients ?",
  "Quel est l'objectif exact du projet ?",
  "Quels sont les verrous R&D candidats ?",
  "Quels sont les points forts et les points faibles du dossier ?",
  "Quelles preuves manquent pour consolider le diagnostic ?",
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

function sourceKindLabel(value?: string) {
  switch (value) {
    case "client_raw":
      return "Document client"
    case "diagnostic_output":
      return "Analyse EnnoDiagnostic"
    default:
      return "Preuve RAG"
  }
}

function sourceKindClass(value?: string) {
  switch (value) {
    case "client_raw":
      return "border-success/30 bg-success/10 text-success"
    case "diagnostic_output":
      return "border-brand/30 bg-brand/10 text-brand"
    default:
      return "border-border bg-muted text-muted-foreground"
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

function locationLabel(source: ChatSource) {
  const values: string[] = []

  if (
    typeof source.page_number === "number" &&
    Number.isFinite(source.page_number) &&
    source.page_number >= 0
  ) {
    values.push(`page ${source.page_number === 0 ? 1 : source.page_number}`)
  }

  if (
    typeof source.paragraph_index === "number" &&
    Number.isFinite(source.paragraph_index) &&
    source.paragraph_index >= 0
  ) {
    values.push(`paragraphe ${source.paragraph_index + 1}`)
  }

  return values.join(" · ")
}

export function DiagnosticRagChat({
  projectId,
  refreshToken = "",
}: DiagnosticRagChatProps) {
  const [status, setStatus] = useState<ChatStatus | null>(null)
  const [open, setOpen] = useState(false)
  const [expanded, setExpanded] = useState(false)
  const [input, setInput] = useState("")
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [expandedSourceMessages, setExpandedSourceMessages] =
    useState<string[]>([])
  const [copiedMessageId, setCopiedMessageId] = useState<string | null>(null)
  const [selectedDocumentId, setSelectedDocumentId] = useState("")

  const bottomRef = useRef<HTMLDivElement>(null)
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

  useEffect(() => {
    setOpen(false)
    setExpanded(false)
    setMessages([])
    setInput("")
    setError("")
    setSelectedDocumentId("")
    loadStatus()
  }, [projectId, refreshToken])

  useEffect(() => {
    if (status?.ready) return

    const interval = window.setInterval(loadStatus, 12000)
    return () => window.clearInterval(interval)
  }, [status?.ready, projectId])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages, sending, expanded])

  const history = useMemo(
    () =>
      messages.slice(-10).map((message) => ({
        role: message.role,
        content: message.content,
      })),
    [messages]
  )

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

      const assistantMessage: ChatMessage = {
        id: newId(),
        role: "assistant",
        content:
          String(payload?.answer || "").trim() ||
          "Aucune réponse exploitable n'a été produite.",
        sources: Array.isArray(payload?.sources) ? payload.sources : [],
        scopeLabel:
          String(payload?.document_scope?.document_name || "").trim() ||
          selectedDocumentName ||
          "Tous les documents",
      }

      setMessages((current) => [...current, assistantMessage])
    } catch (err) {
      const message =
        err instanceof Error
          ? err.message
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

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault()
      sendMessage()
    }
  }

  const toggleSources = (messageId: string) => {
    setExpandedSourceMessages((current) =>
      current.includes(messageId)
        ? current.filter((value) => value !== messageId)
        : [...current, messageId]
    )
  }

  const copyMessage = async (message: ChatMessage) => {
    await navigator.clipboard.writeText(message.content)
    setCopiedMessageId(message.id)
    window.setTimeout(() => setCopiedMessageId(null), 1600)
  }

  const useSourceAsScope = (source: ChatSource) => {
    const sourceId = String(source.document_id || "")
    const byId = sourceId
      ? documentOptions.find((document) => String(document.id) === sourceId)
      : undefined
    const sourceKey = normalizeDocumentName(source.document)
    const byName = documentOptions.find(
      (document) =>
        normalizeDocumentName(
          document.filename || document.stored_filename
        ) === sourceKey
    )
    const matched = byId || byName

    if (matched) {
      setSelectedDocumentId(String(matched.id))
    }
  }

  // Aucun bouton tant que préparation des sources + diagnostic ne sont pas prêts.
  if (!status?.ready) {
    return null
  }

  const windowClass = expanded
    ? "fixed inset-3 z-[79] flex flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl sm:inset-6 lg:left-[270px]"
    : "fixed inset-x-3 bottom-24 z-[79] flex h-[min(680px,calc(100vh-7rem))] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl sm:left-auto sm:right-6 sm:w-[440px]"

  return (
    <>
      <Button
        type="button"
        onClick={() => setOpen((current) => !current)}
        className="fixed bottom-6 right-6 z-[80] size-14 rounded-full bg-brand p-0 shadow-xl hover:bg-brand/90"
        aria-label={open ? "Fermer le chat RAG" : "Ouvrir le chat RAG"}
        title="Assistant documentaire du dossier"
      >
        {open ? (
          <X className="size-5" />
        ) : (
          <MessageCircle className="size-5" />
        )}
      </Button>

      {open && (
        <section
          className={windowClass}
          role="dialog"
          aria-modal="true"
          aria-label="Assistant documentaire EnnoDiagnostic"
        >
          <header className="flex items-center gap-3 border-b border-border bg-card px-4 py-3">
            <div className="flex size-9 items-center justify-center rounded-xl bg-brand">
              <Sparkles className="size-4 text-brand-foreground" />
            </div>

            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-foreground">
                Assistant documentaire EnnoDiagnostic
              </p>
              <p className="truncate text-[11px] text-muted-foreground">
                {selectedDocumentName
                  ? `Document précis : ${selectedDocumentName}`
                  : `Dossier complet · Chroma actif · ${
                      status.chunks_count ?? 0
                    } passages`}
              </p>
            </div>

            <div className="hidden items-center gap-1.5 sm:flex">
              <span className="size-2 rounded-full bg-success" />
              <span className="text-[11px] text-muted-foreground">
                Prêt
              </span>
            </div>

            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="size-8 p-0"
              onClick={() => setExpanded((current) => !current)}
              title={expanded ? "Réduire la fenêtre" : "Agrandir la fenêtre"}
            >
              {expanded ? (
                <Minimize2 className="size-4" />
              ) : (
                <Maximize2 className="size-4" />
              )}
              <span className="sr-only">
                {expanded ? "Réduire" : "Agrandir"}
              </span>
            </Button>

            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="size-8 p-0"
              onClick={() => setOpen(false)}
              title="Fermer le chat"
            >
              <X className="size-4" />
              <span className="sr-only">Fermer</span>
            </Button>
          </header>

          <div className="border-b border-border bg-background px-3 py-2.5 sm:px-4">
            <div
              className={`mx-auto flex flex-col gap-2 ${
                expanded ? "max-w-5xl sm:flex-row sm:items-center" : ""
              }`}
            >
              <div className="flex min-w-0 items-center gap-2">
                <Filter className="size-3.5 flex-shrink-0 text-brand" />
                <span className="text-[11px] font-medium text-foreground">
                  Portée de la recherche
                </span>
              </div>

              <select
                value={selectedDocumentId}
                onChange={(event) => setSelectedDocumentId(event.target.value)}
                disabled={sending}
                className="h-9 min-w-0 flex-1 rounded-md border border-input bg-background px-2.5 text-xs text-foreground outline-none focus:border-brand"
                aria-label="Choisir le document utilisé par le chat"
              >
                <option value="">Tous les documents du projet</option>
                {documentOptions.map((document) => (
                  <option key={document.id} value={String(document.id)}>
                    {document.filename ||
                      document.stored_filename ||
                      `Document ${document.id}`}
                  </option>
                ))}
              </select>

              {selectedDocumentId ? (
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-8 gap-1.5 px-2 text-[11px]"
                  onClick={() => setSelectedDocumentId("")}
                  disabled={sending}
                  title="Revenir à tous les documents"
                >
                  <RotateCcw className="size-3" />
                  Dossier complet
                </Button>
              ) : null}
            </div>

            <p
              className={`mx-auto mt-1 text-[10px] text-muted-foreground ${
                expanded ? "max-w-5xl" : ""
              }`}
            >
              {selectedDocumentName
                ? "Mode strict : la réponse et toutes les sources doivent provenir uniquement de ce document."
                : "Choisissez un document pour mener une suite de questions précises sans mélanger les autres fichiers."}
            </p>
          </div>

          <div className="flex-1 overflow-y-auto bg-muted/10 px-3 py-4 sm:px-4">
            <div
              className={`mx-auto space-y-4 ${
                expanded ? "max-w-5xl" : "max-w-full"
              }`}
            >
              {messages.length === 0 && (
                <div className="rounded-xl border border-brand/20 bg-brand/5 p-4">
                  <div className="flex items-start gap-3">
                    <Bot className="mt-0.5 size-5 flex-shrink-0 text-brand" />

                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-foreground">
                        Bonjour !
                      </p>
                      <p className="mt-1 text-sm leading-6 text-foreground">
                        Utilisez-moi pour discuter avec les documents que vous
                        avez importés. Pour une analyse précise, choisissez un
                        document dans « Portée de la recherche », puis posez une
                        suite de questions. Je conserverai ce document comme
                        contexte et j’ouvrirai chaque preuve au passage surligné.
                      </p>
                      <p className="mt-2 text-xs leading-5 text-muted-foreground">
                        Les documents clients sont prioritaires. Les sorties
                        EnnoDiagnostic ne sont utilisées que pour les questions
                        portant explicitement sur le diagnostic, le score ou le
                        risque. Les hypothèses ne sont pas présentées comme des
                        résultats mesurés.
                      </p>
                    </div>
                  </div>

                  <div
                    className={`mt-4 grid gap-2 ${
                      expanded ? "md:grid-cols-2" : ""
                    }`}
                  >
                    {suggestions.map((suggestion) => (
                      <button
                        key={suggestion}
                        type="button"
                        onClick={() => sendMessage(suggestion)}
                        className="rounded-lg border border-border bg-background px-3 py-2 text-left text-xs text-muted-foreground transition-colors hover:border-brand/40 hover:text-foreground"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {messages.map((message) => {
                const isUser = message.role === "user"
                const hasSources =
                  !isUser &&
                  Array.isArray(message.sources) &&
                  message.sources.length > 0
                const sourcesExpanded =
                  expandedSourceMessages.includes(message.id)

                return (
                  <div
                    key={message.id}
                    className={`flex gap-2.5 ${
                      isUser ? "flex-row-reverse" : ""
                    }`}
                  >
                    <div
                      className={`flex size-8 flex-shrink-0 items-center justify-center rounded-full ${
                        isUser
                          ? "border border-brand/25 bg-brand/10"
                          : "bg-brand"
                      }`}
                    >
                      {isUser ? (
                        <User className="size-4 text-brand" />
                      ) : (
                        <Sparkles className="size-4 text-brand-foreground" />
                      )}
                    </div>

                    <div
                      className={`flex flex-col gap-1 ${
                        isUser ? "items-end" : "items-start"
                      } ${expanded ? "max-w-[92%]" : "max-w-[86%]"}`}
                    >
                      <div
                        className={`rounded-2xl px-3.5 py-2.5 ${
                          isUser
                            ? "rounded-tr-sm bg-brand text-brand-foreground"
                            : "rounded-tl-sm border border-border bg-card"
                        }`}
                      >
                        <p className="whitespace-pre-line text-sm leading-6">
                          {message.content}
                        </p>
                      </div>

                      {message.scopeLabel ? (
                        <span className="px-1 text-[10px] text-muted-foreground">
                          Portée : {message.scopeLabel}
                        </span>
                      ) : null}

                      {!isUser && (
                        <div className="flex flex-wrap items-center gap-3 px-1">
                          {hasSources && (
                            <button
                              type="button"
                              onClick={() => toggleSources(message.id)}
                              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                            >
                              <BookOpen className="size-3" />
                              {message.sources?.length} source(s) utilisée(s)
                              {sourcesExpanded ? (
                                <ChevronUp className="size-3" />
                              ) : (
                                <ChevronDown className="size-3" />
                              )}
                            </button>
                          )}

                          <button
                            type="button"
                            onClick={() => copyMessage(message)}
                            className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground"
                          >
                            {copiedMessageId === message.id ? (
                              <>
                                <Check className="size-3 text-success" />
                                Copié
                              </>
                            ) : (
                              <>
                                <Copy className="size-3" />
                                Copier
                              </>
                            )}
                          </button>
                        </div>
                      )}

                      {hasSources && sourcesExpanded && (
                        <div
                          className={`w-full pt-1 ${
                            expanded
                              ? "grid gap-3 md:grid-cols-2"
                              : "space-y-2"
                          }`}
                        >
                          {message.sources?.map((source) => {
                            const clickable =
                              source.source_kind !== "diagnostic_output"

                            return (
                              <div
                                key={`${message.id}-${source.evidence_id}`}
                                className="rounded-lg border border-border bg-background p-3"
                              >
                                <div className="flex items-start gap-2">
                                  <FileText className="mt-0.5 size-3.5 flex-shrink-0 text-muted-foreground" />

                                  <div className="min-w-0 flex-1">
                                    <div className="flex flex-wrap items-center gap-1.5">
                                      <Badge
                                        variant="secondary"
                                        className="h-5 px-1.5 text-[10px] font-semibold"
                                      >
                                        [{source.evidence_id}]
                                      </Badge>

                                      <p className="max-w-[280px] truncate text-xs font-semibold text-foreground">
                                        {source.document}
                                      </p>

                                      <Badge
                                        variant="outline"
                                        className={`h-5 px-1.5 text-[9px] ${sourceKindClass(
                                          source.source_kind
                                        )}`}
                                      >
                                        {sourceKindLabel(source.source_kind)}
                                      </Badge>
                                    </div>

                                    <p className="mt-1 text-[10px] text-muted-foreground">
                                      {evidenceNatureLabel(source.evidence_nature)}
                                      {source.section_title || source.role
                                        ? ` · ${
                                            source.section_title || source.role
                                          }`
                                        : ""}
                                      {locationLabel(source)
                                        ? ` · ${locationLabel(source)}`
                                        : ""}
                                    </p>

                                    <p className="mt-2 border-l-2 border-brand/30 pl-2 text-[11px] italic leading-5 text-muted-foreground">
                                      {source.excerpt}
                                    </p>

                                    {clickable ? (
                                      <div className="mt-3">
                                        <p className="mb-2 text-[10px] font-medium text-brand">
                                          Ouvrir le document et surligner ce passage
                                        </p>

                                        <SourceTextWithDocuments
                                          projectId={projectId}
                                          text={source.document}
                                          documents={sourceDocuments}
                                          evidence={[
                                            toSourceEvidence(source),
                                          ]}
                                          compact
                                          hideTextWhenMatched
                                        />

                                        <Button
                                          type="button"
                                          size="sm"
                                          variant="ghost"
                                          className="mt-2 h-7 px-2 text-[10px]"
                                          onClick={() => useSourceAsScope(source)}
                                        >
                                          Continuer les questions uniquement sur ce document
                                        </Button>
                                      </div>
                                    ) : (
                                      <p className="mt-3 text-[10px] text-muted-foreground">
                                        Cette source est une synthèse de
                                        l’agent. Les preuves documentaires
                                        associées sont affichées dans les autres
                                        cartes.
                                      </p>
                                    )}
                                  </div>
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}

              {sending && (
                <div className="flex items-center gap-2.5">
                  <div className="flex size-8 items-center justify-center rounded-full bg-brand">
                    <Sparkles className="size-4 text-brand-foreground" />
                  </div>

                  <div className="flex items-center gap-2 rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-3 text-xs text-muted-foreground">
                    <Loader2 className="size-4 animate-spin text-brand" />
                    {selectedDocumentName
                      ? "Recherche stricte dans le document sélectionné…"
                      : "Recherche des passages exacts dans le dossier…"}
                  </div>
                </div>
              )}

              <div ref={bottomRef} />
            </div>
          </div>

          <footer className="border-t border-border bg-card p-3">
            <div className={expanded ? "mx-auto max-w-5xl" : ""}>
              {error && (
                <div className="mb-2 flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                  <AlertCircle className="mt-0.5 size-3.5 flex-shrink-0" />
                  {error}
                </div>
              )}

              <div className="flex items-end gap-2">
                <Textarea
                  value={input}
                  onChange={(event) => setInput(event.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={
                    selectedDocumentName
                      ? "Posez une question précise sur ce document…"
                      : "Posez une question précise sur ce dossier…"
                  }
                  rows={1}
                  disabled={sending}
                  className="max-h-32 min-h-10 flex-1 resize-none text-sm"
                />

                <Button
                  type="button"
                  onClick={() => sendMessage()}
                  disabled={!input.trim() || sending}
                  className="size-10 flex-shrink-0 bg-brand p-0 hover:bg-brand/90"
                >
                  {sending ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Send className="size-4" />
                  )}
                  <span className="sr-only">Envoyer</span>
                </Button>
              </div>

              <p className="mt-2 text-center text-[10px] text-muted-foreground">
                Cliquez sur une source pour ouvrir le document au passage
                surligné. Vérification humaine requise.
              </p>
            </div>
          </footer>
        </section>
      )}
    </>
  )
}

"use client"

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  Download,
  ExternalLink,
  FileText,
  Highlighter,
  Loader2,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { getAccessToken } from "@/lib/api"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

export type DbSourceDocument = {
  id: number
  project_id: number
  filename: string
  stored_filename?: string | null
  content_type?: string | null
  file_size?: number | null
  document_type?: string | null
  upload_status?: string | null
  storage_mode?: string | null
  has_file_data?: boolean
  open_url?: string
}

/**
 * Contrat de traçabilité attendu depuis EnnoDiagnostic.
 * Tous les champs sont optionnels pour rester compatible avec les anciens rapports.
 */
export type SourceEvidence = {
  evidence_id?: string | null
  rag_chunk_id?: string | null
  passage_id?: string | null
  document_id?: string | number | null
  document?: string | null
  filename?: string | null
  source_path?: string | null
  year?: string | number | null
  previous_year?: string | number | null
  page_number?: number | null
  paragraph_index?: number | null
  char_start?: number | null
  char_end?: number | null
  section_title?: string | null
  role?: string | null
  excerpt?: string | null
  text?: string | null
  source_text?: string | null
  content?: string | null
  metadata?: Record<string, unknown> | null
}

type ResolvedDocument = {
  document: DbSourceDocument
  evidence: SourceEvidence[]
}

type PreviewState = {
  loading: boolean
  error: string
  objectUrl: string
  text: string
  mediaType: string
  highlighted: boolean
  highlightedPage: number | null
}

const EMPTY_PREVIEW: PreviewState = {
  loading: false,
  error: "",
  objectUrl: "",
  text: "",
  mediaType: "",
  highlighted: false,
  highlightedPage: null,
}

function getAccessTokenForSourceDocument(): string | null {
  // Utiliser la même source d’authentification que tout le frontend EnnoSmart.
  // La clé réelle est `ennosmart_access_token` dans lib/api.
  return getAccessToken()
}

function sourceDocumentUrl(
  projectId: number | string,
  documentId: number,
): string {
  return `${API_BASE_URL}/projects/${projectId}/source-documents/${documentId}/open`
}

function sourceHighlightUrl(projectId: number | string): string {
  return `${API_BASE_URL}/projects/${projectId}/source-highlight/preview`
}

function normalizeDocText(value?: string | null): string {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/œ/g, "oe")
    .replace(/\\/g, "/")
    .split("/")
    .pop()!
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .replace(/_[a-f0-9]{10,64}$/i, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function cleanCandidateName(value: string): string {
  return String(value || "")
    .replace(
      /^\s*(documents?\s+concernés?\s*:|source\s*\d+\s*[–\-:]?)\s*/i,
      "",
    )
    .replace(/^[\s\-–—•]+|[\s,.;:]+$/g, "")
    .trim()
}

function cleanDisplayDocumentName(value?: string | null): string {
  const raw = String(value || "").replace(/\\/g, "/")
  const filename = raw.split("/").pop() || raw

  return filename
    .replace(/_[a-f0-9]{10,64}(?=\.[^.]+$)/i, "")
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function evidenceText(evidence?: SourceEvidence | null): string {
  if (!evidence) return ""

  return String(
    evidence.excerpt ||
      evidence.text ||
      evidence.source_text ||
      evidence.content ||
      "",
  ).trim()
}

function evidenceDocumentName(evidence?: SourceEvidence | null): string {
  if (!evidence) return ""

  const metadata =
    evidence.metadata && typeof evidence.metadata === "object"
      ? evidence.metadata
      : {}

  return String(
    evidence.document ||
      evidence.filename ||
      evidence.source_path ||
      metadata.document ||
      metadata.filename ||
      metadata.source_path ||
      "",
  ).trim()
}

function evidenceDocumentId(evidence?: SourceEvidence | null): number | null {
  if (!evidence) return null

  const metadata =
    evidence.metadata && typeof evidence.metadata === "object"
      ? evidence.metadata
      : {}

  const value = evidence.document_id ?? metadata.document_id
  const parsed = Number(value)
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null
}

function evidenceIdentity(evidence: SourceEvidence, index: number): string {
  return String(
    evidence.passage_id ||
      evidence.rag_chunk_id ||
      evidence.evidence_id ||
      `${normalizeDocText(evidenceDocumentName(evidence))}:${index}:${evidenceText(evidence).slice(0, 120)}`,
  )
}

function dedupeEvidence(evidence: SourceEvidence[]): SourceEvidence[] {
  const seen = new Set<string>()
  const output: SourceEvidence[] = []

  evidence.forEach((item, index) => {
    if (!item || typeof item !== "object") return

    const key = evidenceIdentity(item, index)
    if (!key || seen.has(key)) return

    seen.add(key)
    output.push(item)
  })

  return output
}

function reCutAfterLongProse(name: string): string {
  return String(name || "")
    .split(/\s{2,}|\s+\|\s+/)[0]
    .replace(/\s+(Les|Le|La|Ce|Cette|Ces|Afin|Avec|Pour)\s.+$/i, "")
    .trim()
}

function extractDocumentNamesFromText(text: string): string[] {
  const value = text || ""
  const names: string[] = []

  const exact =
    /([^\n\r;,:]+?\.(?:pdf|docx|doc|docm|xlsx|xls|pptx|ppt|png|jpg|jpeg|msg|txt))/gi
  let match: RegExpExecArray | null

  while ((match = exact.exec(value)) !== null) {
    const name = cleanCandidateName(match[1] || "")
    if (name.length > 3) names.push(name)
  }

  const source = /Source\s*\d+\s*[–\-:]\s*([^\n\r|]+)/gi
  while ((match = source.exec(value)) !== null) {
    let name = cleanCandidateName(match[1] || "")
    name = reCutAfterLongProse(name)
    if (name.length > 3) names.push(name)
  }

  const docsBlock =
    /Documents?\s+concernés?\s*:\s*([\s\S]*?)(?:\.\s*Indices?\s+sources?\s*:|\n\n|$)/gi
  while ((match = docsBlock.exec(value)) !== null) {
    const block = match[1] || ""
    block.split(/,|;|\n/).forEach((part) => {
      const name = cleanCandidateName(part)
      if (name.length > 3) names.push(name)
    })
  }

  const seen = new Set<string>()
  return names.filter((name) => {
    const key = normalizeDocText(name)
    if (!key || seen.has(key)) return false
    seen.add(key)
    return true
  })
}

function scoreDocMatch(query: string, doc: DbSourceDocument): number {
  const normalizedQuery = normalizeDocText(query)
  if (!normalizedQuery) return 0

  const candidates = [
    normalizeDocText(doc.filename),
    normalizeDocText(doc.stored_filename),
  ]

  let best = 0

  for (const candidate of candidates) {
    if (!candidate) continue

    if (normalizedQuery === candidate) {
      best = Math.max(best, 100)
      continue
    }

    if (
      normalizedQuery.includes(candidate) ||
      candidate.includes(normalizedQuery)
    ) {
      best = Math.max(best, 92)
      continue
    }

    const queryWords = new Set(normalizedQuery.split(" ").filter(Boolean))
    const candidateWords = new Set(candidate.split(" ").filter(Boolean))
    let common = 0

    queryWords.forEach((word) => {
      if (candidateWords.has(word)) common += 1
    })

    const ratio = common / Math.max(1, Math.min(queryWords.size, candidateWords.size))
    if (ratio >= 0.7 && common >= 2) {
      best = Math.max(best, Math.round(70 + ratio * 20))
    }
  }

  return best
}

function stableNegativeId(value: string): number {
  let hash = 2166136261
  const text = String(value || "virtual-source-document")

  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }

  const positive = Math.abs(hash || 1)
  return -positive
}

function inferVirtualContentType(value: string): string {
  const lower = String(value || "").toLowerCase()
  if (lower.endsWith(".pdf")) return "application/pdf"
  if (/\.(png|jpe?g|gif|webp|bmp)$/i.test(lower)) return "image/*"
  if (/\.(txt|md|csv|json|xml|log)$/i.test(lower)) return "text/plain"
  if (/\.(docx?|docm)$/i.test(lower)) {
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
  }
  return "application/octet-stream"
}

function virtualDocumentFromEvidence(
  evidence: SourceEvidence,
): DbSourceDocument | null {
  const sourcePath = String(evidence.source_path || "").trim()
  const rawName = evidenceDocumentName(evidence) || sourcePath
  const filename = cleanDisplayDocumentName(rawName)

  if (!sourcePath && !filename) return null

  const identity = sourcePath || filename
  return {
    id: stableNegativeId(identity),
    project_id: 0,
    filename: filename || "Document source externe",
    stored_filename: filename || null,
    content_type: inferVirtualContentType(sourcePath || filename),
    document_type: "source_historique",
    upload_status: "memory_v2",
    storage_mode: "source_path",
    has_file_data: false,
  }
}

function findBestDocument(
  rawName: string,
  documents: DbSourceDocument[],
): DbSourceDocument | null {
  let best: DbSourceDocument | null = null
  let bestScore = 0

  for (const document of documents || []) {
    const score = scoreDocMatch(rawName, document)
    if (score > bestScore) {
      bestScore = score
      best = document
    }
  }

  return best && bestScore >= 70 ? best : null
}

export function resolveLocalDocuments(
  text: string,
  documents: DbSourceDocument[],
): DbSourceDocument[] {
  const names = extractDocumentNamesFromText(text)
  const seen = new Set<number>()
  const output: DbSourceDocument[] = []

  for (const rawName of names) {
    const best = findBestDocument(rawName, documents)
    if (best && !seen.has(best.id)) {
      seen.add(best.id)
      output.push(best)
    }
  }

  return output
}

export function resolveEvidenceDocuments(
  evidence: SourceEvidence[],
  documents: DbSourceDocument[],
): ResolvedDocument[] {
  const resolved = new Map<number, ResolvedDocument>()

  dedupeEvidence(evidence || []).forEach((item) => {
    const directId = evidenceDocumentId(item)
    const byId = directId
      ? documents.find((document) => Number(document.id) === directId)
      : undefined
    const matched =
      byId ||
      findBestDocument(evidenceDocumentName(item), documents) ||
      virtualDocumentFromEvidence(item)

    if (!matched) return

    const current = resolved.get(matched.id) || {
      document: matched,
      evidence: [],
    }

    current.evidence.push(item)
    resolved.set(matched.id, current)
  })

  return Array.from(resolved.values()).map((item) => ({
    ...item,
    evidence: dedupeEvidence(item.evidence),
  }))
}

export function useProjectSourceDocuments(
  projectId?: number | string | null,
) {
  const [documents, setDocuments] = useState<DbSourceDocument[]>([])

  useEffect(() => {
    let cancelled = false

    if (!projectId) {
      setDocuments([])
      return () => {
        cancelled = true
      }
    }

    const token = getAccessTokenForSourceDocument()

    fetch(`${API_BASE_URL}/projects/${projectId}/source-documents`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then(async (response) => {
        if (!response.ok) {
          const detail = await response.text().catch(() => "")
          throw new Error(detail || `HTTP ${response.status}`)
        }
        return response.json()
      })
      .then((data) => {
        if (!cancelled) {
          setDocuments(Array.isArray(data?.documents) ? data.documents : [])
        }
      })
      .catch(() => {
        if (!cancelled) setDocuments([])
      })

    return () => {
      cancelled = true
    }
  }, [projectId])

  return documents
}

function contentKind(document: DbSourceDocument): "pdf" | "image" | "text" | "other" {
  const contentType = String(document.content_type || "").toLowerCase()
  const lowerName = String(
    document.filename || document.stored_filename || "",
  ).toLowerCase()

  if (contentType.includes("pdf") || lowerName.endsWith(".pdf")) return "pdf"
  if (
    contentType.startsWith("image/") ||
    /\.(png|jpe?g|gif|webp|bmp)$/i.test(lowerName)
  ) {
    return "image"
  }
  if (
    contentType.startsWith("text/") ||
    /\.(txt|md|csv|json|xml|log)$/i.test(lowerName)
  ) {
    return "text"
  }
  return "other"
}

function normalizedPdfPage(value?: number | null): number | null {
  const page = Number(value)
  if (!Number.isFinite(page) || page < 0) return null
  return page === 0 ? 1 : Math.floor(page)
}

function previewUrlForEvidence(
  objectUrl: string,
  document: DbSourceDocument,
  evidence?: SourceEvidence | null,
  highlightedPage?: number | null,
): string {
  if (!objectUrl) return ""

  const page =
    highlightedPage ||
    normalizedPdfPage(evidence?.page_number)

  const isPdf =
    contentKind(document) === "pdf" ||
    objectUrl.toLowerCase().includes("application/pdf")

  return isPdf && page
    ? `${objectUrl}#page=${page}&zoom=page-width`
    : objectUrl
}

function formatEvidenceLocation(evidence: SourceEvidence): string {
  const parts: string[] = []
  const page = normalizedPdfPage(evidence.page_number)

  if (page) parts.push(`Page ${page}`)
  if (
    Number.isFinite(Number(evidence.paragraph_index)) &&
    Number(evidence.paragraph_index) >= 0
  ) {
    parts.push(`Paragraphe ${Number(evidence.paragraph_index) + 1}`)
  }
  if (evidence.section_title) parts.push(String(evidence.section_title))

  return parts.join(" · ") || "Localisation documentaire disponible partiellement"
}

function escapeRegex(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")
}

function findFlexibleTextRange(
  fullText: string,
  excerpt: string,
): { start: number; end: number } | null {
  if (!fullText || !excerpt) return null

  const exactIndex = fullText.toLocaleLowerCase().indexOf(excerpt.toLocaleLowerCase())
  if (exactIndex >= 0) {
    return { start: exactIndex, end: exactIndex + excerpt.length }
  }

  const tokens = excerpt
    .replace(/\s+/g, " ")
    .trim()
    .split(" ")
    .filter((token) => token.length > 1)
    .slice(0, 35)

  if (tokens.length < 4) return null

  try {
    const pattern = tokens.map(escapeRegex).join("\\s+")
    const match = new RegExp(pattern, "i").exec(fullText)
    if (!match || match.index < 0) return null
    return { start: match.index, end: match.index + match[0].length }
  } catch {
    return null
  }
}

function HighlightedTextPreview({
  text,
  evidence,
}: {
  text: string
  evidence?: SourceEvidence | null
}) {
  const excerpt = evidenceText(evidence)
  const range = findFlexibleTextRange(text, excerpt)

  if (!range) {
    return (
      <pre className="max-h-[66vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-5 text-sm leading-7 text-foreground">
        {text || "Aucun contenu texte disponible."}
      </pre>
    )
  }

  const contextStart = Math.max(0, range.start - 2500)
  const contextEnd = Math.min(text.length, range.end + 2500)
  const before = text.slice(contextStart, range.start)
  const selected = text.slice(range.start, range.end)
  const after = text.slice(range.end, contextEnd)

  return (
    <pre className="max-h-[66vh] overflow-auto whitespace-pre-wrap break-words rounded-lg bg-white p-5 text-sm leading-7 text-foreground">
      {contextStart > 0 ? "…\n" : ""}
      {before}
      <mark className="rounded bg-yellow-200 px-0.5 text-foreground">
        {selected}
      </mark>
      {after}
      {contextEnd < text.length ? "\n…" : ""}
    </pre>
  )
}

function SourceDocumentButtons({
  projectId,
  resolvedDocuments,
  compact = false,
}: {
  projectId: number | string
  resolvedDocuments: ResolvedDocument[]
  compact?: boolean
}) {
  const [selected, setSelected] = useState<ResolvedDocument | null>(null)

  if (!resolvedDocuments.length) return null

  return (
    <>
      <div className={compact ? "flex flex-col gap-2" : "grid gap-2 sm:grid-cols-2"}>
        {resolvedDocuments.map(({ document, evidence }) => (
          <Button
            key={document.id}
            type="button"
            size="sm"
            variant="outline"
            className="h-auto min-h-10 max-w-full justify-between gap-3 whitespace-normal px-3 py-2 text-left text-xs"
            onClick={() => setSelected({ document, evidence })}
            title="Ouvrir le document source et ses passages associés"
          >
            <span className="flex min-w-0 items-center gap-2">
              <FileText className="size-4 shrink-0 text-brand" />
              <span className="truncate">
                {cleanDisplayDocumentName(
                  document.filename ||
                    document.stored_filename ||
                    `Document ${document.id}`,
                )}
              </span>
            </span>

            {evidence.length > 0 ? (
              <Badge variant="secondary" className="shrink-0 text-[10px]">
                {evidence.length} passage{evidence.length > 1 ? "s" : ""}
              </Badge>
            ) : null}
          </Button>
        ))}
      </div>

      <SourceDocumentDialog
        projectId={projectId}
        document={selected?.document || null}
        evidence={selected?.evidence || []}
        open={Boolean(selected)}
        onOpenChange={(open) => {
          if (!open) setSelected(null)
        }}
      />
    </>
  )
}

export function SourceTextWithDocuments({
  projectId,
  text,
  documents,
  evidence = [],
  compact = false,
  hideTextWhenMatched = false,
}: {
  projectId: number | string
  text: string
  documents: DbSourceDocument[]
  evidence?: SourceEvidence[]
  compact?: boolean
  hideTextWhenMatched?: boolean
}) {
  const resolvedDocuments = useMemo(() => {
    const fromEvidence = resolveEvidenceDocuments(evidence || [], documents || [])
    const byId = new Map<number, ResolvedDocument>()

    fromEvidence.forEach((item) => byId.set(item.document.id, item))

    resolveLocalDocuments(text || "", documents || []).forEach((document) => {
      if (!byId.has(document.id)) {
        byId.set(document.id, { document, evidence: [] })
      }
    })

    return Array.from(byId.values())
  }, [text, documents, evidence])

  if (!resolvedDocuments.length) {
    return (
      <span className="whitespace-pre-wrap text-sm leading-7 text-foreground">
        {text || "—"}
      </span>
    )
  }

  return (
    <div className={compact ? "space-y-2" : "space-y-3"}>
      {!hideTextWhenMatched ? (
        <p className="whitespace-pre-wrap text-sm leading-7 text-foreground">
          {text || "—"}
        </p>
      ) : null}

      <SourceDocumentButtons
        projectId={projectId}
        resolvedDocuments={resolvedDocuments}
        compact={compact}
      />
    </div>
  )
}

export function SourceDocumentsInline({
  projectId,
  text,
  documents,
  evidence = [],
}: {
  projectId: number | string
  text: string
  documents: DbSourceDocument[]
  evidence?: SourceEvidence[]
}) {
  return (
    <SourceTextWithDocuments
      projectId={projectId}
      text={text}
      documents={documents}
      evidence={evidence}
      hideTextWhenMatched
    />
  )
}

export function SourceDocumentDialog({
  projectId,
  document,
  evidence = [],
  open,
  onOpenChange,
}: {
  projectId: number | string
  document: DbSourceDocument | null
  evidence?: SourceEvidence[]
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [selectedEvidenceIndex, setSelectedEvidenceIndex] = useState(0)
  const [preview, setPreview] = useState<PreviewState>(EMPTY_PREVIEW)

  const cleanEvidence = useMemo(
    () => dedupeEvidence(evidence || []),
    [evidence],
  )
  const selectedEvidence = cleanEvidence[selectedEvidenceIndex] || null
  const selectedEvidenceKey = selectedEvidence
    ? evidenceIdentity(selectedEvidence, selectedEvidenceIndex)
    : ""
  const kind = document ? contentKind(document) : "other"

  // Une seule clé primitive stabilise la taille du tableau de dépendances,
  // y compris pendant le Fast Refresh de Next.js/Turbopack.
  const previewLoadKey = [
    open ? "1" : "0",
    String(projectId ?? ""),
    String(document?.id ?? ""),
    kind,
    selectedEvidenceKey,
  ].join("|")

  useEffect(() => {
    setSelectedEvidenceIndex(0)
  }, [document?.id, open])

  useEffect(() => {
    let cancelled = false
    let createdObjectUrl = ""

    if (!open || !document) {
      setPreview(EMPTY_PREVIEW)
      return () => {
        cancelled = true
      }
    }

    const loadOriginalDocument = async (token: string | null) => {
      if (Number(document.id) <= 0) {
        throw new Error(
          "Le fichier historique ne peut être ouvert que par sa source_path avec un extrait à surligner.",
        )
      }

      const response = await fetch(sourceDocumentUrl(projectId, document.id), {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })

      if (!response.ok) {
        const detail = await response.text().catch(() => "")
        throw new Error(
          detail || `Impossible d'ouvrir le document (HTTP ${response.status}).`,
        )
      }

      return response
    }

    const load = async () => {
      setPreview({ ...EMPTY_PREVIEW, loading: true })

      try {
        const token = getAccessTokenForSourceDocument()
        const excerpt = evidenceText(selectedEvidence)
        let response: Response
        let highlighted = false

        if (selectedEvidence && excerpt) {
          response = await fetch(sourceHighlightUrl(projectId), {
            method: "POST",
            headers: {
              ...(token ? { Authorization: `Bearer ${token}` } : {}),
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              document_id: Number(document.id) > 0 ? document.id : null,
              excerpt,
              source_path: selectedEvidence.source_path || null,
              source_name:
                evidenceDocumentName(selectedEvidence) ||
                document.filename ||
                document.stored_filename,
              document_name:
                document.filename ||
                document.stored_filename ||
                null,
              passage_id:
                selectedEvidence.passage_id ||
                selectedEvidence.rag_chunk_id ||
                selectedEvidence.evidence_id ||
                null,
              page_number: selectedEvidence.page_number ?? null,
              paragraph_index: selectedEvidence.paragraph_index ?? null,
              char_start: selectedEvidence.char_start ?? null,
              char_end: selectedEvidence.char_end ?? null,
              title: document.filename || document.stored_filename,
              year:
                selectedEvidence.year ||
                selectedEvidence.previous_year ||
                (selectedEvidence.metadata && typeof selectedEvidence.metadata === "object"
                  ? selectedEvidence.metadata.year || selectedEvidence.metadata.previous_year
                  : null) ||
                null,
              return_json: false,
            }),
          })

          if (response.ok) {
            highlighted = true
          } else {
            const highlightErrorPayload = await response
              .clone()
              .json()
              .catch(() => null)
            const highlightErrorText =
              typeof highlightErrorPayload?.detail === "string"
                ? highlightErrorPayload.detail
                : `Prévisualisation surlignée indisponible (HTTP ${response.status}).`

            console.warn("[SourceDocumentDialog]", highlightErrorText)
            response = await loadOriginalDocument(token)
          }
        } else {
          response = await loadOriginalDocument(token)
        }

        const blob = await response.blob()
        createdObjectUrl = URL.createObjectURL(blob)
        const mediaType = String(
          response.headers.get("content-type") || blob.type || "",
        ).toLowerCase()
        const highlightedPageHeader = Number(
          response.headers.get("x-ennosmart-highlight-page"),
        )
        const highlightedPage = Number.isFinite(highlightedPageHeader) && highlightedPageHeader > 0
          ? highlightedPageHeader
          : null
        const text =
          !highlighted && kind === "text"
            ? await blob.text()
            : ""

        if (!cancelled) {
          setPreview({
            loading: false,
            error: "",
            objectUrl: createdObjectUrl,
            text,
            mediaType,
            highlighted,
            highlightedPage,
          })
        }
      } catch (error) {
        if (!cancelled) {
          setPreview({
            ...EMPTY_PREVIEW,
            error:
              error instanceof Error
                ? error.message
                : "Impossible de charger le document source.",
          })
        }
      }
    }

    void load()

    return () => {
      cancelled = true
      if (createdObjectUrl) URL.revokeObjectURL(createdObjectUrl)
    }
  }, [previewLoadKey])

  const goToPreviousEvidence = useCallback(() => {
    setSelectedEvidenceIndex((index) =>
      cleanEvidence.length
        ? (index - 1 + cleanEvidence.length) % cleanEvidence.length
        : 0,
    )
  }, [cleanEvidence.length])

  const goToNextEvidence = useCallback(() => {
    setSelectedEvidenceIndex((index) =>
      cleanEvidence.length ? (index + 1) % cleanEvidence.length : 0,
    )
  }, [cleanEvidence.length])

  const openBlobInNewTab = useCallback(() => {
    if (!preview.objectUrl) return
    window.open(preview.objectUrl, "_blank", "noopener,noreferrer")
  }, [preview.objectUrl])

  const downloadBlob = useCallback(() => {
    if (!preview.objectUrl || !document) return

    const anchor = window.document.createElement("a")
    anchor.href = preview.objectUrl
    anchor.download =
      document.filename || document.stored_filename || `document-${document.id}`
    window.document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
  }, [document, preview.objectUrl])

  if (!document) return null

  const previewUrl = previewUrlForEvidence(
    preview.objectUrl,
    document,
    selectedEvidence,
    preview.highlightedPage,
  )

  // Fast Refresh peut temporairement restaurer un ancien état incomplet.
  // Toujours normaliser avant d'appeler includes()/startsWith().
  const previewMediaType = String(preview?.mediaType || "").toLowerCase()
  const previewIsPdf = previewMediaType.includes("application/pdf")
  const previewIsHtml = previewMediaType.includes("text/html")
  const previewIsImage = previewMediaType.startsWith("image/")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[94vh] max-w-[96vw] overflow-hidden p-0 xl:max-w-7xl">
        <DialogHeader className="border-b px-6 py-4">
          <div className="flex flex-col gap-3 pr-8 sm:flex-row sm:items-start sm:justify-between">
            <div className="min-w-0 space-y-1">
              <DialogTitle className="line-clamp-2 text-base">
                {cleanDisplayDocumentName(
                  document.filename ||
                    document.stored_filename ||
                    `Document ${document.id}`,
                )}
              </DialogTitle>
              <DialogDescription>
                Document source complet avec le passage sélectionné automatiquement surligné.
              </DialogDescription>
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {cleanEvidence.length > 0 ? (
                <Badge variant="secondary">
                  {cleanEvidence.length} passage
                  {cleanEvidence.length > 1 ? "s" : ""}
                </Badge>
              ) : null}
              <Badge variant="outline">
                {kind === "pdf"
                  ? "PDF"
                  : kind === "image"
                    ? "Image"
                    : kind === "text"
                      ? "Texte"
                      : document.document_type || "Document"}
              </Badge>
            </div>
          </div>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden lg:grid-cols-[minmax(0,1fr)_360px]">
          <section className="min-h-0 overflow-hidden bg-muted/20 p-4">
            {preview.loading ? (
              <div className="flex h-[68vh] items-center justify-center rounded-xl border bg-white">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Chargement du document source…
                </div>
              </div>
            ) : preview.error ? (
              <div className="flex h-[68vh] items-center justify-center rounded-xl border border-destructive/20 bg-destructive/5 p-6">
                <div className="max-w-md space-y-2 text-center">
                  <AlertCircle className="mx-auto size-6 text-destructive" />
                  <p className="text-sm font-semibold text-foreground">
                    Document indisponible
                  </p>
                  <p className="text-sm leading-6 text-muted-foreground">
                    {preview.error}
                  </p>
                </div>
              </div>
            ) : (previewIsPdf || previewIsHtml) && previewUrl ? (
              <iframe
                key={previewUrl}
                title={document.filename || `Document ${document.id}`}
                src={previewUrl}
                className="h-[68vh] w-full rounded-xl border bg-white"
              />
            ) : (previewIsImage || kind === "image") && previewUrl ? (
              <div className="flex h-[68vh] items-center justify-center overflow-auto rounded-xl border bg-white p-4">
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={previewUrl}
                  alt={document.filename || `Document ${document.id}`}
                  className="max-h-full max-w-full object-contain"
                />
              </div>
            ) : kind === "text" && !preview.highlighted ? (
              <div className="h-[68vh] overflow-hidden rounded-xl border bg-white">
                <HighlightedTextPreview
                  text={preview.text}
                  evidence={selectedEvidence}
                />
              </div>
            ) : (
              <div className="flex h-[68vh] items-center justify-center rounded-xl border bg-white p-6">
                <div className="max-w-lg space-y-4 text-center">
                  <FileText className="mx-auto size-9 text-brand" />
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-foreground">
                      Prévisualisation native non disponible
                    </p>
                    <p className="text-sm leading-6 text-muted-foreground">
                      Le navigateur ne rend pas directement ce format. Les passages
                      associés restent consultables dans le panneau de droite.
                    </p>
                  </div>
                  <div className="flex justify-center gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!preview.objectUrl}
                      onClick={openBlobInNewTab}
                    >
                      <ExternalLink className="mr-1 size-4" />
                      Ouvrir
                    </Button>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={!preview.objectUrl}
                      onClick={downloadBlob}
                    >
                      <Download className="mr-1 size-4" />
                      Télécharger
                    </Button>
                  </div>
                </div>
              </div>
            )}
          </section>

          <aside className="min-h-0 overflow-y-auto border-l bg-white p-4">
            <div className="space-y-4">
              <div className="flex items-center justify-between gap-2">
                <div>
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Preuves du verrou
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Sélectionnez un passage pour naviguer dans le document.
                  </p>
                </div>

                {cleanEvidence.length > 1 ? (
                  <div className="flex items-center gap-1">
                    <Button
                      type="button"
                      size="icon"
                      variant="outline"
                      className="size-8"
                      onClick={goToPreviousEvidence}
                      aria-label="Passage précédent"
                    >
                      <ChevronLeft className="size-4" />
                    </Button>
                    <Button
                      type="button"
                      size="icon"
                      variant="outline"
                      className="size-8"
                      onClick={goToNextEvidence}
                      aria-label="Passage suivant"
                    >
                      <ChevronRight className="size-4" />
                    </Button>
                  </div>
                ) : null}
              </div>

              {cleanEvidence.length === 0 ? (
                <div className="rounded-xl border border-dashed p-4 text-sm leading-6 text-muted-foreground">
                  Aucun passage structuré n’est attaché à ce document. Le document a
                  été retrouvé par son nom uniquement.
                </div>
              ) : (
                <div className="space-y-3">
                  {cleanEvidence.map((item, index) => {
                    const active = index === selectedEvidenceIndex
                    const excerpt = evidenceText(item)

                    return (
                      <button
                        key={evidenceIdentity(item, index)}
                        type="button"
                        className={`w-full rounded-xl border p-3 text-left transition-colors ${
                          active
                            ? "border-brand/40 bg-brand/5"
                            : "bg-white hover:bg-muted/40"
                        }`}
                        onClick={() => setSelectedEvidenceIndex(index)}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <span className="inline-flex items-center gap-1 text-xs font-semibold text-brand">
                            <Highlighter className="size-3.5" />
                            Passage {index + 1}
                          </span>
                          {active ? (
                            <Badge variant="outline" className="text-[10px]">
                              Sélectionné
                            </Badge>
                          ) : null}
                        </div>

                        <p className="mt-2 text-xs leading-5 text-muted-foreground">
                          {formatEvidenceLocation(item)}
                        </p>

                        {excerpt ? (
                          <p className="mt-2 line-clamp-6 text-sm leading-6 text-foreground">
                            {excerpt}
                          </p>
                        ) : (
                          <p className="mt-2 text-sm text-muted-foreground">
                            Passage localisé, mais extrait textuel indisponible.
                          </p>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
            </div>
          </aside>
        </div>

        <div className="flex flex-col gap-2 border-t bg-white px-6 py-3 sm:flex-row sm:items-center sm:justify-between">
          <p className="text-xs text-muted-foreground">
            {preview.highlighted
              ? previewIsPdf
                ? "Le backend a généré une copie PDF avec le passage surligné."
                : "Le backend a généré un aperçu HTML avec le passage surligné."
              : kind === "pdf" && selectedEvidence?.page_number != null
                ? `La visionneuse est positionnée sur ${formatEvidenceLocation(selectedEvidence)}.`
                : kind === "text" && selectedEvidence
                  ? "Le passage sélectionné est surligné dans le texte."
                  : "La preuve sélectionnée reste visible dans le panneau latéral."}
          </p>

          <div className="flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!preview.objectUrl}
              onClick={openBlobInNewTab}
            >
              <ExternalLink className="mr-1 size-4" />
              Nouvel onglet
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!preview.objectUrl}
              onClick={downloadBlob}
            >
              <Download className="mr-1 size-4" />
              Télécharger
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

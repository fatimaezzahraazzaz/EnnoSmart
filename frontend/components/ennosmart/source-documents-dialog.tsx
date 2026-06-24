// Frontend helper V99: liens documents sources intégrés dans le tableau Source
// et dans le bloc "Extrait source utile".

import { useEffect, useMemo, useState } from "react"
import { ExternalLink, FileText } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

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

function normalizeDocText(value?: string | null): string {
  return (value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/œ/g, "oe")
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .replace(/_[a-f0-9]{10,16}$/i, "")
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function cleanCandidateName(value: string): string {
  return String(value || "")
    .replace(/^\s*(documents?\s+concernés?\s*:|source\s*\d+\s*[–\-:]?)\s*/i, "")
    .replace(/^[\s\-–—•]+|[\s,.;:]+$/g, "")
    .trim()
}

function extractDocumentNamesFromText(text: string): string[] {
  const value = text || ""
  const names: string[] = []

  // Noms complets avec extension : A.pdf, B.docx, etc.
  const exact = /([^\n\r;,:]+?\.(?:pdf|docx|doc|xlsx|xls|pptx|ppt|png|jpg|jpeg|msg|txt))/gi
  let m: RegExpExecArray | null
  while ((m = exact.exec(value)) !== null) {
    const name = cleanCandidateName(m[1] || "")
    if (name.length > 3) names.push(name)
  }

  // Source 1 – Analyse des segments...
  const source = /Source\s*\d+\s*[–\-:]\s*([^\n\r|]+)/gi
  while ((m = source.exec(value)) !== null) {
    let name = cleanCandidateName(m[1] || "")
    name = reCutAfterLongProse(name)
    if (name.length > 3) names.push(name)
  }

  // Documents concernés : A.pdf, B.docx. Indices sources : ...
  const docsBlock = /Documents?\s+concernés?\s*:\s*([\s\S]*?)(?:\.\s*Indices?\s+sources?\s*:|\n\n|$)/gi
  while ((m = docsBlock.exec(value)) !== null) {
    const block = m[1] || ""
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

function reCutAfterLongProse(name: string): string {
  return String(name || "")
    .split(/\s{2,}|\s+\|\s+/)[0]
    .replace(/\s+(Les|Le|La|Ce|Cette|Ces|Afin|Avec|Pour)\s.+$/i, "")
    .trim()
}

function scoreDocMatch(query: string, doc: DbSourceDocument): number {
  const q = normalizeDocText(query)
  if (!q) return 0

  const candidates = [normalizeDocText(doc.filename), normalizeDocText(doc.stored_filename)]
  let best = 0

  for (const c of candidates) {
    if (!c) continue
    if (q === c) best = Math.max(best, 100)
    else if (q.includes(c) || c.includes(q)) best = Math.max(best, 92)
    else {
      const qw = new Set(q.split(" ").filter(Boolean))
      const cw = new Set(c.split(" ").filter(Boolean))
      let common = 0
      qw.forEach((w) => {
        if (cw.has(w)) common += 1
      })
      const ratio = common / Math.max(1, Math.min(qw.size, cw.size))
      if (ratio >= 0.7 && common >= 2) best = Math.max(best, Math.round(70 + ratio * 20))
    }
  }

  return best
}

export function resolveLocalDocuments(text: string, documents: DbSourceDocument[]): DbSourceDocument[] {
  const names = extractDocumentNamesFromText(text)
  const seen = new Set<number>()
  const out: DbSourceDocument[] = []

  for (const rawName of names) {
    let best: DbSourceDocument | null = null
    let bestScore = 0

    for (const doc of documents || []) {
      const score = scoreDocMatch(rawName, doc)
      if (score > bestScore) {
        bestScore = score
        best = doc
      }
    }

    if (best && bestScore >= 70 && !seen.has(best.id)) {
      seen.add(best.id)
      out.push(best)
    }
  }

  return out
}

export function useProjectSourceDocuments(projectId?: number | string | null) {
  const [documents, setDocuments] = useState<DbSourceDocument[]>([])

  useEffect(() => {
    if (!projectId) return

    const token = typeof window !== "undefined" ? localStorage.getItem("token") : null

    fetch(`${API_BASE_URL}/projects/${projectId}/source-documents`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
      .then((res) => (res.ok ? res.json() : Promise.reject(res)))
      .then((data) => setDocuments(Array.isArray(data?.documents) ? data.documents : []))
      .catch(() => setDocuments([]))
  }, [projectId])

  return documents
}

function SourceDocumentButtons({
  projectId,
  documents,
  compact = false,
}: {
  projectId: number | string
  documents: DbSourceDocument[]
  compact?: boolean
}) {
  const [selected, setSelected] = useState<DbSourceDocument | null>(null)

  if (!documents.length) return null

  return (
    <>
      <div className={compact ? "flex flex-col gap-1" : "flex flex-wrap gap-2"}>
        {documents.map((doc) => (
          <Button
            key={doc.id}
            type="button"
            size="sm"
            variant="outline"
            className="h-auto max-w-full justify-start whitespace-normal text-left text-xs"
            onClick={() => setSelected(doc)}
            title="Ouvrir le vrai document complet depuis PostgreSQL"
          >
            <FileText className="mr-1 size-3 shrink-0" />
            {doc.filename || doc.stored_filename || `Document ${doc.id}`}
          </Button>
        ))}
      </div>

      <SourceDocumentDialog
        projectId={projectId}
        document={selected}
        open={!!selected}
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
  compact = false,
  hideTextWhenMatched = false,
}: {
  projectId: number | string
  text: string
  documents: DbSourceDocument[]
  compact?: boolean
  hideTextWhenMatched?: boolean
}) {
  const matchedDocs = useMemo(
    () => resolveLocalDocuments(text || "", documents || []),
    [text, documents],
  )

  if (!matchedDocs.length) {
    return (
      <span className="text-sm leading-7 text-foreground whitespace-pre-wrap">
        {text || "—"}
      </span>
    )
  }

  return (
    <div className={compact ? "space-y-1" : "space-y-2"}>
      {!hideTextWhenMatched ? (
        <p className="text-sm leading-7 text-foreground whitespace-pre-wrap">
          {text || "—"}
        </p>
      ) : null}

      <SourceDocumentButtons
        projectId={projectId}
        documents={matchedDocs}
        compact={compact}
      />
    </div>
  )
}

// Garde ce composant pour compatibilité avec le patch V98 si une ancienne zone l'appelle encore.
export function SourceDocumentsInline({
  projectId,
  text,
  documents,
}: {
  projectId: number | string
  text: string
  documents: DbSourceDocument[]
}) {
  return (
    <SourceTextWithDocuments
      projectId={projectId}
      text={text}
      documents={documents}
      hideTextWhenMatched
    />
  )
}

export function SourceDocumentDialog({
  projectId,
  document,
  open,
  onOpenChange,
}: {
  projectId: number | string
  document: DbSourceDocument | null
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  if (!document) return null

  const url = `${API_BASE_URL}/projects/${projectId}/source-documents/${document.id}/open`
  const contentType = document.content_type || ""
  const lowerName = (document.filename || document.stored_filename || "").toLowerCase()
  const canPreview =
    contentType.includes("pdf") ||
    contentType.startsWith("image/") ||
    contentType.startsWith("text/") ||
    lowerName.endsWith(".pdf") ||
    lowerName.endsWith(".png") ||
    lowerName.endsWith(".jpg") ||
    lowerName.endsWith(".jpeg") ||
    lowerName.endsWith(".txt")

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[92vh] max-w-6xl overflow-hidden">
        <DialogHeader>
          <DialogTitle className="line-clamp-2 text-base">
            {document.filename || document.stored_filename || `Document ${document.id}`}
          </DialogTitle>
          <DialogDescription>
            Document complet ouvert depuis PostgreSQL, pas depuis Chroma.
          </DialogDescription>
        </DialogHeader>

        {canPreview ? (
          <iframe
            title={document.filename || `Document ${document.id}`}
            src={url}
            className="h-[75vh] w-full rounded-md border"
          />
        ) : (
          <div className="rounded-md border p-4 text-sm text-muted-foreground">
            Ce type de fichier ne peut pas toujours être prévisualisé par le navigateur.
            Le document est bien récupéré depuis PostgreSQL. Ouvre-le dans un nouvel onglet.
          </div>
        )}

        <div className="flex justify-end gap-2">
          <Button asChild variant="outline" size="sm">
            <a href={url} target="_blank" rel="noreferrer">
              <ExternalLink className="mr-1 size-4" />
              Ouvrir dans un nouvel onglet
            </a>
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}

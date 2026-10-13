"use client"

import { useRef, useState } from "react"
import { Check, Download, FileUp, Loader2, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  normalizeSourceDecision,
  researchSourceArticleId,
  researchSourcePdfLink,
  researchSourceReady,
  type ImprovementResearchSource,
} from "@/lib/improvement-research-sources"

export function ImprovementSourceActions({ source, busy, onDecision, onUploadPdf }: {
  source: ImprovementResearchSource
  busy: boolean
  onDecision: (candidateId: string, decision: "accepted" | "rejected", guidedSessionId?: string) => void
  onUploadPdf: (source: ImprovementResearchSource, file: File) => Promise<void>
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState("")
  const decision = normalizeSourceDecision(source.consultant_decision)
  const candidateId = String(source.candidate_id || "").trim()
  const kept = decision === "accepted"
  const ready = researchSourceReady(source)
  const extractionApplies = source.fulltext_status !== "not_applicable_technical_or_context_source"
  const articleId = researchSourceArticleId(source)
  const pdf = researchSourcePdfLink(source)

  return (
    <div className="mt-3 space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {candidateId && (
          <Button
            type="button" size="sm"
            className={kept
              ? "min-h-9 rounded-lg bg-emerald-700 text-white disabled:opacity-100 dark:bg-emerald-800"
              : "min-h-9 rounded-lg"}
            disabled={busy || uploading || kept}
            onClick={() => onDecision(candidateId, "accepted", source.guided_session_id)}
          >
            <Check className="size-3.5" aria-hidden="true" /> {kept ? "Gardé" : "Garder"}
          </Button>
        )}
        {candidateId && decision !== "rejected" && (
          <Button type="button" size="sm" variant="outline" className="min-h-9 rounded-lg"
            disabled={busy || uploading} onClick={() => onDecision(candidateId, "rejected", source.guided_session_id)}>
            <X className="size-3.5" aria-hidden="true" /> Écarter
          </Button>
        )}
        {kept && ready && <span className="text-xs text-emerald-700 dark:text-emerald-300">Preuve prête pour la rédaction</span>}
      </div>
      {kept && !ready && extractionApplies && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200" aria-busy={uploading}>
          <p className="font-semibold" role="status">{uploading ? "Import et extraction en cours…" : "Extraction interrompue"}</p>
          <p className="mt-1 leading-5">L’article est gardé, mais son texte n’est pas encore exploitable pour la rédaction. Téléchargez une copie PDF que vous êtes autorisé à utiliser, puis importez-la ici.</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {pdf.url ? (
              <a href={pdf.url} target="_blank" rel="noopener noreferrer"
                className="inline-flex min-h-9 items-center gap-1.5 rounded-lg border border-border bg-card px-3 text-xs font-semibold text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
                title={pdf.direct ? "Ouvrir le PDF dans un nouvel onglet pour le télécharger" : "Ouvrir la publication pour récupérer le PDF"}>
                <Download className="size-3.5" aria-hidden="true" />
                {pdf.direct ? "Télécharger le PDF" : "Télécharger depuis la publication"}
              </a>
            ) : <Button type="button" size="sm" variant="outline" className="min-h-9" disabled>Télécharger le PDF — lien indisponible</Button>}
            <Button type="button" size="sm" variant="outline" className="min-h-9 rounded-lg"
              disabled={busy || uploading || !articleId || !candidateId} onClick={() => inputRef.current?.click()}>
              {uploading ? <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" /> : <FileUp className="size-3.5" aria-hidden="true" />}
              {uploading ? "Import en cours…" : "Importer le PDF"}
            </Button>
            <input ref={inputRef} type="file" accept="application/pdf,.pdf" className="hidden"
              aria-label={`Importer le PDF de ${source.title || "cet article"}`} disabled={busy || uploading || !articleId || !candidateId}
              onChange={async (event) => {
                const file = event.target.files?.[0]
                event.currentTarget.value = ""
                if (!file) return
                setUploadError("")
                if (file.type !== "application/pdf" && !/\.pdf$/i.test(file.name)) {
                  setUploadError("Choisissez un fichier PDF.")
                  return
                }
                setUploading(true)
                try {
                  await onUploadPdf(source, file)
                } catch (error) {
                  setUploadError(error instanceof Error ? error.message : "L’import a échoué. Réessayez avec le PDF de cet article.")
                } finally {
                  setUploading(false)
                }
              }} />
          </div>
          {!articleId && <p className="mt-2">L’import sera disponible lorsque la source sera associée à un article.</p>}
        </div>
      )}
      {uploadError && <p className="text-xs text-destructive" role="alert">{uploadError}</p>}
    </div>
  )
}

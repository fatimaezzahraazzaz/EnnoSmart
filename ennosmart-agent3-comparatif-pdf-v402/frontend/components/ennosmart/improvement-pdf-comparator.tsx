"use client"

import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react"
import {
  FileDiff,
  FileText,
  GripVertical,
  Loader2,
  Minus,
  Plus,
  RefreshCw,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { getAccessToken } from "@/lib/api"
import { cn } from "@/lib/utils"

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

type ImprovementChange = Record<string, any>

type Props = {
  projectId: number
  sessionId: string
  versionId: string
  changes: ImprovementChange[]
  sourceFilename?: string | null
}

type PreviewState = {
  loading: boolean
  error: string
  objectUrl: string
  page: number | null
  match: boolean | null
  mode: string
}

const EMPTY_PREVIEW: PreviewState = {
  loading: false,
  error: "",
  objectUrl: "",
  page: null,
  match: null,
  mode: "",
}

type NormalizedChange = {
  originalIndex: number
  id: string
  operation: string
  label: string
  section: string
  before: string
  after: string
  reason: string
}

function clean(value: unknown) {
  return String(value || "")
    .replace(/\r\n/g, "\n")
    .replace(/\r/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

function compact(value: unknown, max = 120) {
  const text = clean(value).replace(/\s+/g, " ")
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`
}

function operationLabel(operation: string, before: string, after: string) {
  const op = String(operation || "").toLowerCase()

  if (!before && after) return "Ajout"
  if (before && !after) return "Suppression"

  if (["insert", "add", "added", "addition"].includes(op)) return "Ajout"
  if (["delete", "remove", "removed", "deletion"].includes(op)) return "Suppression"
  return "Modification"
}

function operationTone(operation: string) {
  if (operation === "Ajout") {
    return "border-emerald-200 bg-emerald-50 text-emerald-700"
  }
  if (operation === "Suppression") {
    return "border-rose-200 bg-rose-50 text-rose-700"
  }
  return "border-violet-200 bg-violet-50 text-violet-700"
}

function normalizedChanges(changes: ImprovementChange[]): NormalizedChange[] {
  return (changes || [])
    .map((change, index) => {
      const before = clean(change?.before)
      const after = clean(change?.after)
      const section = clean(
        [change?.section_ref, change?.section_title]
          .filter(Boolean)
          .join(" · "),
      )
      const operation = operationLabel(
        String(change?.operation || ""),
        before,
        after,
      )

      return {
        originalIndex: index,
        id: String(change?.change_id || `${index}-${operation}`),
        operation,
        label:
          section ||
          clean(change?.label) ||
          `${operation} ${index + 1}`,
        section,
        before,
        after,
        reason: clean(change?.reason),
      }
    })
    .filter((change) => {
      if (!change.before && !change.after) return false
      return change.before !== change.after
    })
}

function previewUrl(
  projectId: number,
  sessionId: string,
  versionId: string,
  side: "original" | "proposed",
  changeIndex: number,
) {
  return (
    `${API_BASE_URL}/api/projects/${projectId}/improvements/` +
    `sessions/${encodeURIComponent(sessionId)}/versions/` +
    `${encodeURIComponent(versionId)}/comparison-preview` +
    `?side=${side}&change_index=${changeIndex}`
  )
}

function PdfPane({
  title,
  subtitle,
  accent,
  preview,
  reloadToken,
}: {
  title: string
  subtitle: string
  accent: "red" | "green"
  preview: PreviewState
  reloadToken: string
}) {
  const viewerUrl =
    preview.objectUrl && preview.page
      ? `${preview.objectUrl}#page=${preview.page}&zoom=page-width`
      : preview.objectUrl
        ? `${preview.objectUrl}#zoom=page-width`
        : ""

  return (
    <section className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-white">
      <div
        className={cn(
          "flex min-h-12 shrink-0 items-center gap-3 border-b px-3 py-2",
          accent === "red" ? "bg-rose-50/70" : "bg-emerald-50/70",
        )}
      >
        <div
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-lg border bg-white",
            accent === "red"
              ? "border-rose-200 text-rose-600"
              : "border-emerald-200 text-emerald-600",
          )}
        >
          <FileText className="size-4" />
        </div>

        <div className="min-w-0 flex-1">
          <p
            className={cn(
              "text-[11px] font-semibold uppercase tracking-wide",
              accent === "red" ? "text-rose-700" : "text-emerald-700",
            )}
          >
            {title}
          </p>
          <p className="truncate text-[11px] text-muted-foreground">
            {subtitle}
          </p>
        </div>

        {preview.page ? (
          <Badge variant="outline" className="shrink-0 bg-white text-[10px]">
            Page {preview.page}
          </Badge>
        ) : null}
      </div>

      <div className="relative min-h-0 flex-1 bg-muted/20">
        {preview.loading ? (
          <div className="absolute inset-0 z-10 grid place-items-center bg-background/80 backdrop-blur-[1px]">
            <div className="flex items-center gap-2 rounded-xl border bg-card px-4 py-3 text-xs text-muted-foreground shadow-sm">
              <Loader2 className="size-4 animate-spin" />
              Préparation du document…
            </div>
          </div>
        ) : null}

        {preview.error ? (
          <div className="grid h-full place-items-center p-5">
            <div className="max-w-sm rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-center">
              <p className="text-sm font-semibold text-destructive">
                Aperçu indisponible
              </p>
              <p className="mt-2 text-xs leading-5 text-muted-foreground">
                {preview.error}
              </p>
            </div>
          </div>
        ) : viewerUrl ? (
          <iframe
            key={`${reloadToken}:${viewerUrl}`}
            title={title}
            src={viewerUrl}
            className="h-full min-h-[560px] w-full border-0 bg-white"
          />
        ) : (
          <div className="grid h-full place-items-center p-5">
            <p className="text-xs text-muted-foreground">
              Sélectionnez une modification.
            </p>
          </div>
        )}
      </div>
    </section>
  )
}

export function ImprovementPdfComparator({
  projectId,
  sessionId,
  versionId,
  changes,
  sourceFilename,
}: Props) {
  const rows = useMemo(() => normalizedChanges(changes), [changes])
  const [selectedPosition, setSelectedPosition] = useState(0)
  const selected = rows[selectedPosition] || rows[0] || null

  const [originalPreview, setOriginalPreview] =
    useState<PreviewState>(EMPTY_PREVIEW)
  const [proposedPreview, setProposedPreview] =
    useState<PreviewState>(EMPTY_PREVIEW)

  const [splitPercent, setSplitPercent] = useState(50)
  const splitRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (selectedPosition >= rows.length) {
      setSelectedPosition(0)
    }
  }, [rows.length, selectedPosition])

  useEffect(() => {
    let cancelled = false
    let originalObjectUrl = ""
    let proposedObjectUrl = ""

    if (!projectId || !sessionId || !versionId || !selected) {
      setOriginalPreview(EMPTY_PREVIEW)
      setProposedPreview(EMPTY_PREVIEW)
      return () => {
        cancelled = true
      }
    }

    const token = getAccessToken()
    const headers = token ? { Authorization: `Bearer ${token}` } : {}

    const loadSide = async (
      side: "original" | "proposed",
    ): Promise<PreviewState> => {
      const response = await fetch(
        previewUrl(
          projectId,
          sessionId,
          versionId,
          side,
          selected.originalIndex,
        ),
        {
          headers,
          cache: "no-store",
        },
      )

      if (!response.ok) {
        let detail = ""
        try {
          const payload = await response.json()
          detail = String(payload?.detail || "")
        } catch {
          detail = await response.text().catch(() => "")
        }
        throw new Error(detail || `HTTP ${response.status}`)
      }

      const pageRaw = Number(
        response.headers.get("X-EnnoSmart-Comparison-Page"),
      )
      const matchRaw = response.headers.get(
        "X-EnnoSmart-Comparison-Match",
      )
      const mode = String(
        response.headers.get("X-EnnoSmart-Comparison-Mode") || "",
      )

      const blob = await response.blob()
      const objectUrl = URL.createObjectURL(blob)

      if (side === "original") originalObjectUrl = objectUrl
      else proposedObjectUrl = objectUrl

      return {
        loading: false,
        error: "",
        objectUrl,
        page:
          Number.isFinite(pageRaw) && pageRaw > 0 ? pageRaw : null,
        match:
          matchRaw === "true"
            ? true
            : matchRaw === "false"
              ? false
              : null,
        mode,
      }
    }

    setOriginalPreview({ ...EMPTY_PREVIEW, loading: true })
    setProposedPreview({ ...EMPTY_PREVIEW, loading: true })

    // V4.01 : ne jamais lancer les deux conversions Office en parallèle.
    // L'original est préparé en premier et placé dans le cache backend.
    // La proposition réutilise ensuite ce PDF source avant de convertir
    // uniquement la copie DOCX modifiée.
    void (async () => {
      try {
        const original = await loadSide("original")
        if (cancelled) return
        setOriginalPreview(original)
      } catch (originalError) {
        if (cancelled) return
        setOriginalPreview({
          ...EMPTY_PREVIEW,
          error:
            originalError instanceof Error
              ? originalError.message
              : "Aperçu original indisponible.",
        })
        // Même si l'original échoue, on tente la proposition afin d'afficher
        // une erreur précise sur chaque panneau.
      }

      try {
        const proposed = await loadSide("proposed")
        if (cancelled) return
        setProposedPreview(proposed)
      } catch (proposedError) {
        if (cancelled) return
        setProposedPreview({
          ...EMPTY_PREVIEW,
          error:
            proposedError instanceof Error
              ? proposedError.message
              : "Aperçu proposé indisponible.",
        })
      }
    })()

    return () => {
      cancelled = true
      if (originalObjectUrl) URL.revokeObjectURL(originalObjectUrl)
      if (proposedObjectUrl) URL.revokeObjectURL(proposedObjectUrl)
    }
  }, [
    projectId,
    sessionId,
    versionId,
    selected?.id,
    selected?.originalIndex,
  ])

  const startResize = (
    event: ReactPointerEvent<HTMLButtonElement>,
  ) => {
    event.preventDefault()
    const root = splitRef.current
    if (!root) return

    const move = (pointerEvent: PointerEvent) => {
      const rect = root.getBoundingClientRect()
      if (rect.width <= 0) return

      const percent =
        ((pointerEvent.clientX - rect.left) / rect.width) * 100

      setSplitPercent(Math.max(28, Math.min(72, percent)))
    }

    const stop = () => {
      window.removeEventListener("pointermove", move)
      window.removeEventListener("pointerup", stop)
      window.removeEventListener("pointercancel", stop)
    }

    window.addEventListener("pointermove", move)
    window.addEventListener("pointerup", stop)
    window.addEventListener("pointercancel", stop)
  }

  if (!versionId) {
    return (
      <div className="grid h-full place-items-center p-6">
        <p className="text-sm text-muted-foreground">
          Aucune proposition à comparer.
        </p>
      </div>
    )
  }

  if (rows.length === 0) {
    return (
      <div className="grid h-full place-items-center p-6">
        <div className="max-w-md rounded-xl border bg-card p-5 text-center">
          <FileDiff className="mx-auto size-8 text-muted-foreground" />
          <p className="mt-3 text-sm font-semibold">
            Aucun changement ciblé
          </p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            La proposition ne contient pas encore de modification
            exploitable pour le comparatif PDF.
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col overflow-hidden">
      <div className="shrink-0 border-b bg-card px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className="border-rose-200 bg-rose-50 text-[10px] text-rose-700"
          >
            Rouge = supprimé / remplacé dans l’original
          </Badge>
          <Badge
            variant="outline"
            className="border-emerald-200 bg-emerald-50 text-[10px] text-emerald-700"
          >
            Vert = ajouté / proposé dans la nouvelle version
          </Badge>
          <span className="ml-auto text-[10px] text-muted-foreground">
            Cliquez sur une modification : les deux PDF se positionnent automatiquement.
          </span>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 xl:grid-cols-[238px_minmax(0,1fr)]">
        <aside className="min-h-0 overflow-y-auto border-r bg-muted/15 p-2.5">
          <div className="mb-2 flex items-center justify-between gap-2 px-1">
            <p className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
              Modifications
            </p>
            <Badge variant="outline" className="h-5 px-1.5 text-[10px]">
              {rows.length}
            </Badge>
          </div>

          <div className="space-y-1.5 pb-3">
            {rows.map((change, position) => (
              <button
                key={change.id}
                type="button"
                onClick={() => setSelectedPosition(position)}
                className={cn(
                  "w-full rounded-xl border px-3 py-2.5 text-left transition",
                  position === selectedPosition
                    ? "border-brand/40 bg-brand/5 shadow-sm"
                    : "border-border bg-card hover:bg-muted/40",
                )}
              >
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border bg-background text-[9px] font-semibold">
                    {position + 1}
                  </span>
                  <div className="min-w-0 flex-1">
                    <Badge
                      variant="outline"
                      className={cn(
                        "h-5 px-1.5 text-[9px]",
                        operationTone(change.operation),
                      )}
                    >
                      {change.operation === "Ajout" ? (
                        <Plus className="mr-1 size-2.5" />
                      ) : change.operation === "Suppression" ? (
                        <Minus className="mr-1 size-2.5" />
                      ) : (
                        <RefreshCw className="mr-1 size-2.5" />
                      )}
                      {change.operation}
                    </Badge>

                    <p
                      className="mt-1.5 line-clamp-2 text-[11px] font-semibold leading-4 text-foreground"
                      title={change.label}
                    >
                      {change.label}
                    </p>

                    <p className="mt-1 line-clamp-2 text-[10px] leading-4 text-muted-foreground">
                      {compact(change.after || change.before, 100)}
                    </p>
                  </div>
                </div>
              </button>
            ))}
          </div>
        </aside>

        <main className="flex min-h-0 min-w-0 flex-col overflow-hidden">
          {selected ? (
            <div className="shrink-0 border-b bg-background px-3 py-2">
              <div className="flex min-w-0 items-center gap-2">
                <Badge
                  variant="outline"
                  className={cn(
                    "shrink-0 text-[10px]",
                    operationTone(selected.operation),
                  )}
                >
                  {selected.operation}
                </Badge>
                <p className="min-w-0 flex-1 truncate text-xs font-semibold">
                  {selected.label}
                </p>
                {selected.reason ? (
                  <p
                    className="hidden max-w-[42%] truncate text-[10px] text-muted-foreground 2xl:block"
                    title={selected.reason}
                  >
                    {selected.reason}
                  </p>
                ) : null}
              </div>
            </div>
          ) : null}

          <div
            ref={splitRef}
            className="relative flex min-h-0 min-w-0 flex-1 overflow-hidden"
          >
            <div
              className="flex min-h-0 min-w-0"
              style={{ width: `calc(${splitPercent}% - 5px)` }}
            >
              <PdfPane
                title="Document original"
                subtitle={sourceFilename || "Document source"}
                accent="red"
                preview={originalPreview}
                reloadToken={`${selected?.id || "none"}:original`}
              />
            </div>

            <button
              type="button"
              onPointerDown={startResize}
              className="group relative z-10 grid w-[10px] shrink-0 cursor-col-resize place-items-center border-x bg-muted/50 hover:bg-brand/10"
              title="Ajuster la largeur des deux documents"
              aria-label="Ajuster la largeur des deux documents"
            >
              <GripVertical className="size-3.5 text-muted-foreground group-hover:text-brand" />
            </button>

            <div
              className="flex min-h-0 min-w-0"
              style={{ width: `calc(${100 - splitPercent}% - 5px)` }}
            >
              <PdfPane
                title="Nouvelle version"
                subtitle={`Proposition ${versionId.slice(0, 8)}`}
                accent="green"
                preview={proposedPreview}
                reloadToken={`${selected?.id || "none"}:proposed`}
              />
            </div>
          </div>
        </main>
      </div>
    </div>
  )
}

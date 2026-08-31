"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  ArrowRight,
  BookOpen,
  BrainCircuit,
  CalendarDays,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Database,
  FilePenLine,
  FileText,
  FolderKanban,
  Loader2,
  RefreshCw,
  Search,
  Trash2,
  Upload,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"

import {
  deleteDocument,
  getDocuments,
  getProjectOverviews,
  importExistingDiagnostic,
  importExistingScholar,
  importExistingDocuments,
  type DocumentRead,
  type ProjectOverview,
  type ProjectRead,
} from "@/lib/api"

import {
  getCurrentProjectId,
  setCurrentProjectId,
} from "@/lib/project-session"

interface ProjectDetailPageProps {
  navigateTo: (page: AppPage) => void
}

type StatusState = "ok" | "warning" | "empty"

const PAGE_SIZE_OPTIONS = [10, 20, 50]

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function formatDate(value?: string | null) {
  if (!value) return "—"

  try {
    return new Intl.DateTimeFormat("fr-FR", {
      day: "2-digit",
      month: "2-digit",
      year: "numeric",
    }).format(new Date(value))
  } catch {
    return value
  }
}

function formatSize(size?: number | null) {
  if (!size) return "—"

  if (size < 1024) return `${size} o`
  if (size < 1024 * 1024) return `${Math.round(size / 1024)} Ko`

  return `${(size / (1024 * 1024)).toFixed(1)} Mo`
}

function statusBadge(status: string) {
  if (
    status.includes("terminé") ||
    status.includes("completed") ||
    status.includes("Validé")
  ) {
    return "border-success/25 bg-success/10 text-success"
  }

  if (
    status.includes("Créé") ||
    status.includes("created")
  ) {
    return "border-border bg-muted text-muted-foreground"
  }

  return "border-brand/25 bg-brand/10 text-brand"
}

function statusCardClass(state: StatusState) {
  switch (state) {
    case "ok":
      return "border-success/20 bg-[linear-gradient(135deg,rgba(22,163,74,0.035),rgba(255,255,255,0.94))]"
    case "warning":
      return "border-warning/20 bg-[linear-gradient(135deg,rgba(245,158,11,0.035),rgba(255,255,255,0.94))]"
    default:
      return "border-border/80 bg-card"
  }
}

function sourceFileName(doc: DocumentRead) {
  return (
    doc.original_filename ||
    doc.filename ||
    doc.file_path ||
    doc.storage_path ||
    `Document #${doc.id}`
  )
}

function extensionFromName(name: string) {
  const clean = name.split("?")[0].split("#")[0]
  const dot = clean.lastIndexOf(".")

  if (dot === -1) return ""
  return clean.slice(dot + 1).toLowerCase()
}

function documentTypeLabel(doc: DocumentRead) {
  const name = sourceFileName(doc)
  const extension = extensionFromName(name)
  const mime = String(doc.mime_type || "").toLowerCase()
  const declared = String(doc.document_type || "").trim()

  if (extension === "pdf" || mime.includes("pdf")) return "PDF"
  if (["doc", "docx"].includes(extension) || mime.includes("word")) return "Word"
  if (["xls", "xlsx"].includes(extension) || mime.includes("excel") || mime.includes("spreadsheet")) {
    return "Excel"
  }
  if (["ppt", "pptx"].includes(extension) || mime.includes("presentation")) {
    return "PowerPoint"
  }
  if (["png", "jpg", "jpeg", "webp", "gif"].includes(extension) || mime.startsWith("image/")) {
    return "Image"
  }
  if (["mp3", "wav", "m4a", "aac", "flac", "ogg"].includes(extension) || mime.startsWith("audio/")) {
    return "Audio"
  }
  if (["mp4", "mov", "avi", "mkv", "webm"].includes(extension) || mime.startsWith("video/")) {
    return "Vidéo"
  }
  if (extension === "msg" || mime.includes("message")) return "Email"
  if (["txt", "md"].includes(extension) || mime.startsWith("text/")) return "Texte"

  return declared || "Fichier"
}

function documentTypeClass(type: string) {
  switch (type) {
    case "PDF":
      return "border-red-200 bg-red-50 text-red-700"
    case "Word":
      return "border-blue-200 bg-blue-50 text-blue-700"
    case "Excel":
      return "border-emerald-200 bg-emerald-50 text-emerald-700"
    case "PowerPoint":
      return "border-orange-200 bg-orange-50 text-orange-700"
    case "Image":
      return "border-violet-200 bg-violet-50 text-violet-700"
    case "Audio":
    case "Vidéo":
      return "border-fuchsia-200 bg-fuchsia-50 text-fuchsia-700"
    default:
      return "border-border bg-muted text-muted-foreground"
  }
}

function pageNumbers(current: number, total: number) {
  if (total <= 5) {
    return Array.from({ length: total }, (_, index) => index + 1)
  }

  if (current <= 3) return [1, 2, 3, 4, 5]
  if (current >= total - 2) {
    return [total - 4, total - 3, total - 2, total - 1, total]
  }

  return [current - 2, current - 1, current, current + 1, current + 2]
}

/* -------------------------------------------------------------------------- */
/* Page                                                                       */
/* -------------------------------------------------------------------------- */

export default function ProjectDetailPage({
  navigateTo,
}: ProjectDetailPageProps) {
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [overviews, setOverviews] = useState<ProjectOverview[]>([])
  const [overview, setOverview] = useState<ProjectOverview | null>(null)
  const [documents, setDocuments] = useState<DocumentRead[]>([])

  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<
    "diagnostic" | "scholar" | "documents" | null
  >(null)
  const [error, setError] = useState("")

  const [documentSearch, setDocumentSearch] = useState("")
  const [documentTypeFilter, setDocumentTypeFilter] = useState("all")
  const [documentPage, setDocumentPage] = useState(1)
  const [documentPageSize, setDocumentPageSize] = useState(10)
  const [deletingDocumentId, setDeletingDocumentId] = useState<number | null>(null)
  const [documentDeleteError, setDocumentDeleteError] = useState("")
  const [documentDeleteNotice, setDocumentDeleteNotice] = useState("")

  const selectedProjectId = project?.id

  const stats = useMemo(() => {
    const verrouStats = overview?.diagnostic.verrous
    const articleStats = overview?.scholar.articles

    return {
      pertinent: verrouStats?.pertinent || 0,
      moyen: verrouStats?.moyen || 0,
      direct: articleStats?.direct || 0,
      fondamental: articleStats?.fondamental || 0,
      connexe: articleStats?.connexe || 0,
      horsSujet: articleStats?.hors_sujet || 0,
      usefulArticles: articleStats?.useful || 0,
    }
  }, [overview])

  const diagnosticState: StatusState =
    (overview?.diagnostic.verrous.count || 0) > 0
      ? "ok"
      : overview?.diagnostic.available
        ? "warning"
        : "empty"

  const scholarState: StatusState =
    (overview?.scholar.articles.count || 0) > 0
      ? "ok"
      : overview?.scholar.available
        ? "warning"
        : "empty"

  /* ---------------------------------------------------------------------- */
  /* Documents : filtres + pagination                                       */
  /* ---------------------------------------------------------------------- */

  const documentTypes = useMemo(
    () =>
      Array.from(
        new Set(documents.map((doc) => documentTypeLabel(doc))),
      ).sort((a, b) => a.localeCompare(b, "fr")),
    [documents],
  )

  const filteredDocuments = useMemo(() => {
    const query = documentSearch.trim().toLowerCase()

    return documents.filter((doc) => {
      const name = sourceFileName(doc)
      const type = documentTypeLabel(doc)

      const matchesSearch =
        !query ||
        name.toLowerCase().includes(query) ||
        type.toLowerCase().includes(query) ||
        String(doc.id).includes(query)

      const matchesType =
        documentTypeFilter === "all" ||
        type === documentTypeFilter

      return matchesSearch && matchesType
    })
  }, [documents, documentSearch, documentTypeFilter])

  const totalDocumentPages = Math.max(
    1,
    Math.ceil(filteredDocuments.length / documentPageSize),
  )

  const currentDocumentPage = Math.min(
    documentPage,
    totalDocumentPages,
  )

  const documentStartIndex =
    (currentDocumentPage - 1) * documentPageSize

  const paginatedDocuments = filteredDocuments.slice(
    documentStartIndex,
    documentStartIndex + documentPageSize,
  )

  const visibleDocumentPages = pageNumbers(
    currentDocumentPage,
    totalDocumentPages,
  )

  useEffect(() => {
    setDocumentPage(1)
  }, [documentSearch, documentTypeFilter, documentPageSize])

  useEffect(() => {
    setDocumentPage((page) => Math.min(page, totalDocumentPages))
  }, [totalDocumentPages])

  /* ---------------------------------------------------------------------- */
  /* Chargement                                                             */
  /* ---------------------------------------------------------------------- */

  const loadData = async () => {
    setLoading(true)
    setError("")

    try {
      const overviewList = await getProjectOverviews()
      const projectList = overviewList.map((item) => item.project)

      setOverviews(overviewList)
      setProjects(projectList)

      if (projectList.length === 0) {
        setProject(null)
        setOverview(null)
        setDocuments([])
        return
      }

      const storedProjectId = getCurrentProjectId()

      const selected =
        projectList.find((item) => item.id === storedProjectId) ||
        projectList[0]

      setCurrentProjectId(selected.id)
      setProject(selected)
      setOverview(
        overviewList.find(
          (item) => item.project.id === selected.id,
        ) || null,
      )

      setDocuments(
        await getDocuments(selected.id).catch(() => []),
      )
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le détail du projet.",
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadData()
  }, [])

  const changeProject = async (projectId: number) => {
    setCurrentProjectId(projectId)
    setLoading(true)
    setError("")
    setDocumentDeleteError("")
    setDocumentDeleteNotice("")

    try {
      const selected =
        projects.find((item) => item.id === projectId) || null

      setProject(selected)
      setOverview(
        overviews.find((item) => item.project.id === projectId) || null,
      )

      setDocuments(
        await getDocuments(projectId).catch(() => []),
      )

      setDocumentSearch("")
      setDocumentTypeFilter("all")
      setDocumentPage(1)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le projet sélectionné.",
      )
    } finally {
      setLoading(false)
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Navigation                                                             */
  /* ---------------------------------------------------------------------- */

  const openDiagnosis = () => {
    if (!selectedProjectId) return
    setCurrentProjectId(selectedProjectId)
    navigateTo("diagnosis")
  }

  const openScholar = () => {
    if (!selectedProjectId) return
    setCurrentProjectId(selectedProjectId)
    navigateTo("scholar")
  }

  const openUpload = () => {
    if (!selectedProjectId) return
    setCurrentProjectId(selectedProjectId)
    navigateTo("upload")
  }

  const openImprovement = () => {
    if (!selectedProjectId) return
    setCurrentProjectId(selectedProjectId)
    navigateTo("improvement")
  }

  /* ---------------------------------------------------------------------- */
  /* Import existant                                                        */
  /* ---------------------------------------------------------------------- */

  const importDiagnostic = async () => {
    if (!selectedProjectId) return

    setActionLoading("diagnostic")
    setError("")

    try {
      await importExistingDiagnostic(selectedProjectId)
      await loadData()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’importer le diagnostic existant.",
      )
    } finally {
      setActionLoading(null)
    }
  }

  const importScholar = async () => {
    if (!selectedProjectId) return

    setActionLoading("scholar")
    setError("")

    try {
      await importExistingScholar(selectedProjectId)
      await loadData()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’importer EnnoScholar.",
      )
    } finally {
      setActionLoading(null)
    }
  }

  const importDocuments = async () => {
    if (!selectedProjectId) return

    setActionLoading("documents")
    setError("")

    try {
      await importExistingDocuments(selectedProjectId)
      await loadData()
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible d’importer les documents existants.",
      )
    } finally {
      setActionLoading(null)
    }
  }

  const removeDocument = async (document: DocumentRead) => {
    if (!selectedProjectId || deletingDocumentId !== null) return
    const name = sourceFileName(document)
    if (!window.confirm(
      `Supprimer « ${name} » du projet ?\n\nLe fichier ne sera plus disponible dans EnnoSmart. Les conversations et versions existantes seront conservées. Le fichier original sur disque ou OneDrive ne sera pas supprimé.`,
    )) return

    setDeletingDocumentId(document.id)
    setDocumentDeleteError("")
    setDocumentDeleteNotice("")
    try {
      await deleteDocument(selectedProjectId, document.id)
      setDocuments((current) => current.filter((item) => item.id !== document.id))
      setDocumentDeleteNotice(`« ${name} » a été supprimé du projet.`)
    } catch (err) {
      setDocumentDeleteError(err instanceof Error ? err.message : "Impossible de supprimer le document. Réessayez.")
    } finally {
      setDeletingDocumentId(null)
    }
  }

  /* ---------------------------------------------------------------------- */
  /* Loading / error / vide                                                 */
  /* ---------------------------------------------------------------------- */

  if (loading) {
    return (
      <div className="mx-auto max-w-[1600px] p-6">
        <Card className="rounded-2xl">
          <CardContent className="flex items-center justify-center gap-3 p-10 text-sm text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement du détail projet…
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error) {
    return (
      <div className="mx-auto max-w-[1600px] space-y-4 p-6">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigateTo("projects")}
          className="rounded-xl"
        >
          <ArrowLeft className="size-4" />
          Retour aux projets
        </Button>

        <Card className="rounded-2xl border-destructive/30 bg-destructive/10">
          <CardContent className="flex items-start gap-3 p-5 text-destructive">
            <AlertCircle className="mt-0.5 size-5" />

            <div className="space-y-3">
              <div>
                <p className="text-sm font-semibold">
                  Erreur détail projet
                </p>
                <p className="mt-1 text-xs">{error}</p>
              </div>

              <Button
                size="sm"
                variant="outline"
                onClick={loadData}
                className="rounded-xl"
              >
                Réessayer
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div className="mx-auto max-w-[1600px] p-6">
        <Card className="rounded-2xl">
          <CardContent className="p-10 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet disponible.
            </p>

            <Button
              className="mt-4 rounded-xl"
              variant="outline"
              onClick={() => navigateTo("projects")}
            >
              Retour aux projets
            </Button>
          </CardContent>
        </Card>
      </div>
    )
  }

  const workflow = [
    {
      number: 1,
      label: "Sources",
      detail: `${documents.length} document(s)`,
      complete: documents.length > 0,
      current: documents.length === 0,
      onClick: openUpload,
    },
    {
      number: 2,
      label: "Diagnostic",
      detail: "Verrous",
      complete: diagnosticState === "ok",
      current:
        diagnosticState !== "ok" &&
        documents.length > 0,
      onClick: openDiagnosis,
    },
    {
      number: 3,
      label: "Recherche",
      detail: "Preuves",
      complete: scholarState === "ok",
      current:
        scholarState !== "ok" &&
        diagnosticState === "ok",
      onClick: openScholar,
    },
    {
      number: 4,
      label: "Amélioration",
      detail: "Livrable",
      complete: false,
      current: scholarState === "ok",
      onClick: openImprovement,
    },
  ]

  return (
    <div className="workspace-page-wide pb-10">
      <div className="mx-auto w-full max-w-[1600px] space-y-4">

        {/* ================================================================= */}
        {/* Breadcrumb                                                       */}
        {/* ================================================================= */}

        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <button
            type="button"
            onClick={() => navigateTo("projects")}
            className="transition hover:text-foreground"
          >
            Projets
          </button>

          <span>/</span>

          <span className="font-medium text-foreground">
            Détail du projet
          </span>
        </div>

        {/* ================================================================= */}
        {/* Hero projet                                                      */}
        {/* ================================================================= */}

        <section className="relative overflow-hidden rounded-[20px] border border-brand/15 bg-card shadow-sm">
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_88%_8%,rgba(109,70,178,.075),transparent_30%)]"
          />

          <div className="absolute inset-y-0 left-0 w-1 bg-brand" />

          <div className="relative flex flex-col gap-5 px-5 py-5 sm:px-6 lg:flex-row lg:items-start lg:justify-between">
            <div className="min-w-0">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigateTo("projects")}
                className="-ml-2 mb-2 h-8 rounded-xl px-2 text-muted-foreground"
              >
                <ArrowLeft className="size-4" />
                Retour aux projets
              </Button>

              <div className="flex items-start gap-3">
                <div className="grid size-11 shrink-0 place-items-center rounded-2xl bg-brand text-brand-foreground shadow-sm">
                  <FolderKanban className="size-5" />
                </div>

                <div className="min-w-0">
                  <h1 className="truncate text-2xl font-semibold tracking-[-0.035em] text-foreground sm:text-[28px]">
                    {project.project_name}
                  </h1>

                  <p className="mt-1 text-sm text-muted-foreground">
                    {project.organisme} — {project.year}
                  </p>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Badge
                      variant="outline"
                      className={`rounded-full ${statusBadge(project.status)}`}
                    >
                      {project.status}
                    </Badge>

                    <Badge
                      variant="outline"
                      className="rounded-full bg-background"
                    >
                      Dossier ID #{project.id}
                    </Badge>

                    <Badge
                      variant="outline"
                      className="rounded-full bg-background"
                    >
                      {project.domain_label || "Domaine non renseigné"}
                    </Badge>
                  </div>
                </div>
              </div>
            </div>

            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {projects.length > 1 && (
                <select
                  value={project.id}
                  disabled={deletingDocumentId !== null}
                  onChange={(event) =>
                    changeProject(Number(event.target.value))
                  }
                  className="h-10 min-w-[260px] rounded-xl border border-border bg-background px-3 text-sm outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
                >
                  {projects.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.organisme} — {item.project_name} — {item.year}
                    </option>
                  ))}
                </select>
              )}

              <Button
                variant="outline"
                onClick={loadData}
                disabled={deletingDocumentId !== null}
                className="h-10 rounded-xl"
              >
                <RefreshCw className="size-4" />
                Actualiser
              </Button>
            </div>
          </div>
        </section>

        {/* ================================================================= */}
        {/* KPI                                                              */}
        {/* ================================================================= */}

        <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <OverviewCard
            icon={FileText}
            label="Documents"
            value={String(documents.length)}
            detail="fichiers projet"
            tone="brand"
          />

          <OverviewCard
            icon={BrainCircuit}
            label="Verrous"
            value={String(overview?.diagnostic.verrous.count || 0)}
            detail={`${stats.pertinent} pertinents · ${stats.moyen} moyens`}
            tone="brand"
          />

          <OverviewCard
            icon={BookOpen}
            label="Articles utiles"
            value={String(stats.usefulArticles)}
            detail={`${stats.direct} directs`}
            tone="success"
          />

          <OverviewCard
            icon={CalendarDays}
            label="Créé le"
            value={formatDate(project.created_at)}
            detail={`consultant #${project.consultant_id}`}
            tone="brand"
            compactValue
          />
        </section>

        {/* ================================================================= */}
        {/* Workflow                                                         */}
        {/* ================================================================= */}

        <section className="rounded-2xl border border-border/80 bg-card px-4 py-4 shadow-sm sm:px-6">
          <div className="grid gap-3 lg:grid-cols-4">
            {workflow.map((step, index) => (
              <button
                key={step.number}
                type="button"
                onClick={step.onClick}
                className="group relative flex min-w-0 items-center gap-3 rounded-xl px-2 py-1.5 text-left transition hover:bg-muted/45"
              >
                {index < workflow.length - 1 && (
                  <span
                    aria-hidden="true"
                    className={`absolute left-[calc(50%+42px)] right-[-14px] top-1/2 hidden h-px -translate-y-1/2 lg:block ${
                      step.complete
                        ? "bg-success/55"
                        : "border-t border-dashed border-brand/30"
                    }`}
                  />
                )}

                <span
                  className={`relative z-10 grid size-8 shrink-0 place-items-center rounded-full text-xs font-semibold ${
                    step.complete
                      ? "bg-success text-white"
                      : step.current
                        ? "bg-brand text-brand-foreground"
                        : "border border-border bg-background text-muted-foreground"
                  }`}
                >
                  {step.complete ? (
                    <CheckCircle2 className="size-4" />
                  ) : (
                    step.number
                  )}
                </span>

                <div className="relative z-10 min-w-0 bg-card pr-3 group-hover:bg-transparent">
                  <p className="truncate text-xs font-semibold text-foreground">
                    {step.number} &nbsp; {step.label}
                  </p>
                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {step.detail}
                  </p>
                </div>
              </button>
            ))}
          </div>
        </section>

        {/* ================================================================= */}
        {/* Agents                                                           */}
        {/* ================================================================= */}

        <section className="grid gap-4 lg:grid-cols-3">

          {/* EnnoDiagnostic */}

          <Card className={`overflow-hidden rounded-2xl shadow-sm ${statusCardClass(diagnosticState)}`}>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span className="grid size-8 place-items-center rounded-xl bg-success/8 text-success">
                  <BrainCircuit className="size-4" />
                </span>
                EnnoDiagnostic
              </CardTitle>

              <CardDescription className="text-xs leading-5">
                Diagnostic technique CIR, verrous et validation consultant.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid grid-cols-3 gap-2">
                <MiniMetric
                  label="Verrous"
                  value={overview?.diagnostic.verrous.count || 0}
                />
                <MiniMetric
                  label="Pertinents"
                  value={stats.pertinent}
                  tone="success"
                />
                <MiniMetric
                  label="Moyens"
                  value={stats.moyen}
                  tone="warning"
                />
              </div>

              <AgentStatus
                state={diagnosticState}
                okText="Verrous synchronisés et prêts à valider."
                warningText="Diagnostic trouvé, mais verrous non synchronisés."
                emptyText="Aucun diagnostic importé pour ce projet."
              />

              <div className="flex flex-wrap gap-2">
                <Button
                  className="rounded-xl bg-brand hover:bg-brand/90"
                  onClick={openDiagnosis}
                >
                  Ouvrir EnnoDiagnostic
                  <ArrowRight className="size-4" />
                </Button>

                <Button
                  variant="outline"
                  className="rounded-xl"
                  onClick={importDiagnostic}
                  disabled={actionLoading === "diagnostic"}
                >
                  {actionLoading === "diagnostic" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Database className="size-4" />
                  )}
                  Importer résultat existant
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* EnnoScholar */}

          <Card className={`overflow-hidden rounded-2xl shadow-sm ${statusCardClass(scholarState)}`}>
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span className="grid size-8 place-items-center rounded-xl bg-brand/8 text-brand">
                  <BookOpen className="size-4" />
                </span>
                EnnoScholar
              </CardTitle>

              <CardDescription className="text-xs leading-5">
                Recherche scientifique, tri des articles et état de l’art.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid grid-cols-3 gap-2">
                <MiniMetric
                  label="Articles"
                  value={overview?.scholar.articles.count || 0}
                />
                <MiniMetric
                  label="Directs"
                  value={stats.direct}
                  tone="success"
                />
                <MiniMetric
                  label="Hors sujet"
                  value={stats.horsSujet}
                />
              </div>

              <AgentStatus
                state={scholarState}
                okText="Articles synchronisés et prêts à sélectionner."
                warningText="Rapport EnnoScholar trouvé, mais articles non synchronisés."
                emptyText="Aucun rapport EnnoScholar importé pour ce projet."
              />

              <div className="flex flex-wrap gap-2">
                <Button
                  className="rounded-xl bg-brand hover:bg-brand/90"
                  onClick={openScholar}
                >
                  Ouvrir EnnoScholar
                  <ArrowRight className="size-4" />
                </Button>

                <Button
                  variant="outline"
                  className="rounded-xl"
                  onClick={importScholar}
                  disabled={actionLoading === "scholar"}
                >
                  {actionLoading === "scholar" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Database className="size-4" />
                  )}
                  Importer résultat existant
                </Button>
              </div>
            </CardContent>
          </Card>

          {/* EnnoAmelioration */}

          <Card className="overflow-hidden rounded-2xl border-brand/15 bg-[linear-gradient(135deg,rgba(109,70,178,0.035),rgba(255,255,255,0.96))] shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2 text-sm">
                <span className="grid size-8 place-items-center rounded-xl bg-brand/8 text-brand">
                  <FilePenLine className="size-4" />
                </span>
                EnnoAmelioration
              </CardTitle>

              <CardDescription className="text-xs leading-5">
                Révision contrôlée des sections et du CIR complet à partir des preuves disponibles.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="rounded-xl border border-brand/15 bg-brand/[0.04] p-4">
                <p className="text-sm font-semibold text-foreground">
                  Amélioration guidée
                </p>

                <p className="mt-1 text-xs leading-5 text-muted-foreground">
                  Conservez l’original, comparez chaque proposition et validez explicitement la version retenue.
                </p>
              </div>

              <Button
                onClick={openImprovement}
                className="w-full justify-between rounded-xl bg-brand hover:bg-brand/90"
              >
                Continuer la rédaction
                <ArrowRight className="size-4" />
              </Button>
            </CardContent>
          </Card>
        </section>

        {/* ================================================================= */}
        {/* Documents                                                        */}
        {/* ================================================================= */}

        <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
          <CardHeader className="border-b border-border/70 px-4 py-4 sm:px-5">
            <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">
              <div>
                <CardTitle className="flex items-center gap-2 text-sm">
                  <FileText className="size-4 text-brand" />
                  Documents du projet
                </CardTitle>

                <CardDescription className="mt-1 text-xs">
                  Documents enregistrés côté backend pour ce dossier.
                </CardDescription>
              </div>

              <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">

                {/* Recherche */}

                <div className="relative min-w-[250px] sm:min-w-[290px]">
                  <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />

                  <Input
                    value={documentSearch}
                    onChange={(event) =>
                      setDocumentSearch(event.target.value)
                    }
                    placeholder="Rechercher un document…"
                    className="h-9 rounded-xl pl-9"
                  />
                </div>

                {/* Type */}

                <select
                  value={documentTypeFilter}
                  onChange={(event) =>
                    setDocumentTypeFilter(event.target.value)
                  }
                  className="h-9 min-w-[150px] rounded-xl border border-border bg-background px-3 text-xs outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
                >
                  <option value="all">
                    Tous les types
                  </option>

                  {documentTypes.map((type) => (
                    <option key={type} value={type}>
                      {type}
                    </option>
                  ))}
                </select>

                <Button
                  variant="outline"
                  size="sm"
                  className="h-9 rounded-xl"
                  onClick={importDocuments}
                  disabled={actionLoading === "documents" || deletingDocumentId !== null}
                >
                  {actionLoading === "documents" ? (
                    <Loader2 className="size-4 animate-spin" />
                  ) : (
                    <Database className="size-4" />
                  )}
                  Importer existants
                </Button>

                <Button
                  size="sm"
                  className="h-9 rounded-xl bg-brand hover:bg-brand/90"
                  onClick={openUpload}
                >
                  <Upload className="size-4" />
                  Déposer
                </Button>
              </div>
            </div>
          </CardHeader>

          <CardContent className="p-0">
            {documentDeleteError && (
              <p role="alert" className="mx-5 mt-4 rounded-xl border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
                {documentDeleteError}
              </p>
            )}
            {documentDeleteNotice && (
              <p role="status" className="mx-5 mt-4 text-sm text-foreground">
                {documentDeleteNotice}
              </p>
            )}
            {documents.length === 0 ? (
              <div className="m-5 rounded-xl border border-dashed p-8 text-center">
                <p className="text-sm font-medium text-foreground">
                  Aucun document enregistré en base.
                </p>

                <p className="mt-1 text-xs text-muted-foreground">
                  Importez les documents existants ou ajoutez de nouvelles sources.
                </p>
              </div>
            ) : filteredDocuments.length === 0 ? (
              <div className="m-5 rounded-xl border border-dashed p-8 text-center">
                <Search className="mx-auto size-5 text-muted-foreground" />

                <p className="mt-3 text-sm font-medium text-foreground">
                  Aucun document trouvé
                </p>

                <p className="mt-1 text-xs text-muted-foreground">
                  Modifiez la recherche ou le filtre de type.
                </p>
              </div>
            ) : (
              <>
                {/* Table desktop */}

                <div className="hidden lg:block">
                  <div className="grid grid-cols-[minmax(180px,1.5fr)_150px_150px_140px_90px_116px] gap-4 border-b border-border/70 bg-muted/[0.16] px-5 py-2.5 text-[10px] font-semibold uppercase tracking-[0.08em] text-muted-foreground">
                    <span>Nom</span>
                    <span>Type</span>
                    <span>Date</span>
                    <span>Statut</span>
                    <span>ID</span>
                    <span className="text-center">Actions</span>
                  </div>

                  {paginatedDocuments.map((doc) => {
                    const name = sourceFileName(doc)
                    const type = documentTypeLabel(doc)

                    return (
                      <div
                        key={doc.id}
                        className="grid min-h-[52px] grid-cols-[minmax(180px,1.5fr)_150px_150px_140px_90px_116px] items-center gap-4 border-b border-border/55 px-5 py-2.5 text-xs transition last:border-b-0 hover:bg-brand/[0.018]"
                      >
                        <div className="flex min-w-0 items-center gap-2.5">
                          <span className="grid size-7 shrink-0 place-items-center rounded-lg bg-brand/[0.065] text-brand">
                            <FileText className="size-3.5" />
                          </span>

                          <div className="min-w-0">
                            <p className="truncate font-medium text-foreground">
                              {name}
                            </p>
                            {doc.size_bytes ? (
                              <p className="mt-0.5 text-[10px] text-muted-foreground">
                                {formatSize(doc.size_bytes)}
                              </p>
                            ) : null}
                          </div>
                        </div>

                        <div>
                          <Badge
                            variant="outline"
                            className={`rounded-full px-2 py-0.5 text-[10px] ${documentTypeClass(type)}`}
                          >
                            {type}
                          </Badge>
                        </div>

                        <span className="text-muted-foreground">
                          {formatDate(doc.created_at)}
                        </span>

                        <div>
                          <Badge
                            variant="outline"
                            className="rounded-full border-success/20 bg-success/8 px-2 py-0.5 text-[10px] text-success"
                          >
                            Disponible
                          </Badge>
                        </div>

                        <span className="font-medium text-foreground">
                          #{doc.id}
                        </span>

                        <div className="flex justify-center">
                          <DocumentDeleteButton
                            name={name}
                            deleting={deletingDocumentId === doc.id}
                            disabled={deletingDocumentId !== null || actionLoading === "documents"}
                            onClick={() => void removeDocument(doc)}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* Cards mobile */}

                <div className="divide-y divide-border/60 lg:hidden">
                  {paginatedDocuments.map((doc) => {
                    const name = sourceFileName(doc)
                    const type = documentTypeLabel(doc)

                    return (
                      <div
                        key={doc.id}
                        className="space-y-3 p-4"
                      >
                        <div className="flex items-start gap-3">
                          <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/[0.065] text-brand">
                            <FileText className="size-4" />
                          </span>

                          <div className="min-w-0 flex-1">
                            <p className="truncate text-sm font-medium text-foreground">
                              {name}
                            </p>

                            <p className="mt-1 text-xs text-muted-foreground">
                              {formatDate(doc.created_at)} · #{doc.id}
                            </p>
                          </div>
                        </div>

                        <div className="flex flex-wrap items-center gap-2">
                          <Badge
                            variant="outline"
                            className={`rounded-full text-[10px] ${documentTypeClass(type)}`}
                          >
                            {type}
                          </Badge>

                          <Badge
                            variant="outline"
                            className="rounded-full border-success/20 bg-success/8 text-[10px] text-success"
                          >
                            Disponible
                          </Badge>

                          {doc.size_bytes ? (
                            <span className="text-[10px] text-muted-foreground">
                              {formatSize(doc.size_bytes)}
                            </span>
                          ) : null}
                          <DocumentDeleteButton
                            name={name}
                            deleting={deletingDocumentId === doc.id}
                            disabled={deletingDocumentId !== null || actionLoading === "documents"}
                            onClick={() => void removeDocument(doc)}
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>

                {/* Pagination */}

                <div className="flex flex-col gap-3 border-t border-border/70 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <p className="text-xs text-muted-foreground">
                    {filteredDocuments.length === 0
                      ? "0 document"
                      : `${documentStartIndex + 1}-${Math.min(
                          documentStartIndex + documentPageSize,
                          filteredDocuments.length,
                        )} sur ${filteredDocuments.length} document${
                          filteredDocuments.length > 1 ? "s" : ""
                        }`}
                  </p>

                  <div className="flex items-center justify-center gap-1">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 rounded-lg px-2.5"
                      disabled={currentDocumentPage === 1}
                      onClick={() =>
                        setDocumentPage((page) =>
                          Math.max(1, page - 1),
                        )
                      }
                    >
                      <ChevronLeft className="size-4" />
                      <span className="hidden sm:inline">
                        Précédent
                      </span>
                    </Button>

                    {visibleDocumentPages.map((page) => (
                      <Button
                        key={page}
                        variant={
                          page === currentDocumentPage
                            ? "default"
                            : "ghost"
                        }
                        size="icon"
                        className="size-8 rounded-lg text-xs"
                        onClick={() => setDocumentPage(page)}
                      >
                        {page}
                      </Button>
                    ))}

                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-8 rounded-lg px-2.5"
                      disabled={
                        currentDocumentPage === totalDocumentPages
                      }
                      onClick={() =>
                        setDocumentPage((page) =>
                          Math.min(totalDocumentPages, page + 1),
                        )
                      }
                    >
                      <span className="hidden sm:inline">
                        Suivant
                      </span>
                      <ChevronRight className="size-4" />
                    </Button>
                  </div>

                  <div className="flex items-center justify-end gap-2">
                    <span className="text-xs text-muted-foreground">
                      Afficher
                    </span>

                    <select
                      value={documentPageSize}
                      onChange={(event) =>
                        setDocumentPageSize(
                          Number(event.target.value),
                        )
                      }
                      className="h-8 rounded-lg border border-border bg-background px-2 text-xs outline-none"
                    >
                      {PAGE_SIZE_OPTIONS.map((size) => (
                        <option key={size} value={size}>
                          {size} / page
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Sous-composants                                                            */
/* -------------------------------------------------------------------------- */

function DocumentDeleteButton({ name, deleting, disabled, onClick }: {
  name: string
  deleting: boolean
  disabled: boolean
  onClick: () => void
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      className="h-9 rounded-lg px-2 text-destructive hover:bg-destructive/10 hover:text-destructive"
      aria-label={`Supprimer ${name}`}
      disabled={disabled}
      onClick={onClick}
    >
      {deleting ? <Loader2 aria-hidden="true" className="size-4 animate-spin" /> : <Trash2 aria-hidden="true" className="size-4" />}
      {deleting ? "Suppression…" : "Supprimer"}
    </Button>
  )
}

function OverviewCard({
  icon: Icon,
  label,
  value,
  detail,
  tone = "brand",
  compactValue = false,
}: {
  icon: typeof FileText
  label: string
  value: string
  detail: string
  tone?: "brand" | "success"
  compactValue?: boolean
}) {
  return (
    <Card className="rounded-2xl border-border/80 shadow-sm">
      <CardContent className="flex items-center gap-4 p-4">
        <span
          className={`grid size-10 shrink-0 place-items-center rounded-xl ${
            tone === "success"
              ? "bg-success/8 text-success"
              : "bg-brand/[0.065] text-brand"
          }`}
        >
          <Icon className="size-5" />
        </span>

        <div className="min-w-0">
          <p className="text-xs text-muted-foreground">
            {label}
          </p>

          <p
            className={`mt-0.5 truncate font-semibold tracking-[-0.02em] text-foreground ${
              compactValue ? "text-lg" : "text-2xl"
            }`}
          >
            {value}
          </p>

          <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
            {detail}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}

function MiniMetric({
  label,
  value,
  tone = "default",
}: {
  label: string
  value: number
  tone?: "default" | "success" | "warning"
}) {
  return (
    <div className="rounded-xl border border-border/80 bg-card px-3 py-2.5 text-center shadow-sm">
      <p className="text-[10px] text-muted-foreground">
        {label}
      </p>

      <p
        className={`mt-0.5 text-lg font-semibold ${
          tone === "success"
            ? "text-success"
            : tone === "warning"
              ? "text-warning"
              : "text-foreground"
        }`}
      >
        {value}
      </p>
    </div>
  )
}

function AgentStatus({
  state,
  okText,
  warningText,
  emptyText,
}: {
  state: StatusState
  okText: string
  warningText: string
  emptyText: string
}) {
  if (state === "ok") {
    return (
      <div className="flex items-start gap-2 text-xs text-muted-foreground">
        <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-success" />
        <span>{okText}</span>
      </div>
    )
  }

  if (state === "warning") {
    return (
      <div className="flex items-start gap-2 text-xs text-muted-foreground">
        <Clock className="mt-0.5 size-4 shrink-0 text-warning" />
        <span>{warningText}</span>
      </div>
    )
  }

  return (
    <div className="flex items-start gap-2 text-xs text-muted-foreground">
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      <span>{emptyText}</span>
    </div>
  )
}

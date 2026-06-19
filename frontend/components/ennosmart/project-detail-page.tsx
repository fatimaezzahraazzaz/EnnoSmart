"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  Clock,
  Database,
  FileText,
  FolderKanban,
  Loader2,
  RefreshCw,
  Upload,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

import {
  getArticles,
  getDiagnosticLatest,
  getDocuments,
  getProject,
  getProjects,
  getScholarLatest,
  getVerrous,
  importExistingDiagnostic,
  importExistingScholar,
  importExistingDocuments,
  type ArticleRead,
  type DocumentRead,
  type ProjectRead,
  type VerrouRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"

interface ProjectDetailPageProps {
  navigateTo: (page: AppPage) => void
}

type StatusState = "ok" | "warning" | "empty"

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
  if (status.includes("terminé") || status.includes("completed")) {
    return "bg-success/10 text-success border-success/30"
  }

  if (status.includes("Créé") || status.includes("created")) {
    return "bg-muted text-muted-foreground border-border"
  }

  return "bg-brand/10 text-brand border-brand/30"
}

function statusCardClass(state: StatusState) {
  switch (state) {
    case "ok":
      return "border-success/30 bg-success/5"
    case "warning":
      return "border-warning/30 bg-warning/5"
    default:
      return "border-border"
  }
}

function sourceFileName(doc: DocumentRead) {
  return doc.original_filename || doc.filename || doc.file_path || doc.storage_path || `Document #${doc.id}`
}

function countArticlesByTag(articles: ArticleRead[], tag: string) {
  return articles.filter((article) =>
    (article.tag_article || "").toLowerCase().includes(tag.toLowerCase())
  ).length
}

export default function ProjectDetailPage({ navigateTo }: ProjectDetailPageProps) {
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [verrous, setVerrous] = useState<VerrouRead[]>([])
  const [articles, setArticles] = useState<ArticleRead[]>([])
  const [diagnosticLatest, setDiagnosticLatest] = useState<any>(null)
  const [scholarLatest, setScholarLatest] = useState<any>(null)

  const [loading, setLoading] = useState(true)
  const [actionLoading, setActionLoading] = useState<"diagnostic" | "scholar" | "documents" | null>(null)
  const [error, setError] = useState("")

  const selectedProjectId = project?.id

  const stats = useMemo(() => {
    const pertinent = verrous.filter((verrou) =>
      (verrou.tag_cir || "").toUpperCase().includes("PERTINENT")
    ).length

    const moyen = verrous.filter((verrou) =>
      (verrou.tag_cir || "").toUpperCase().includes("MOYEN")
    ).length

    const direct = countArticlesByTag(articles, "Direct")
    const fondamental = countArticlesByTag(articles, "Fondamental")
    const connexe = countArticlesByTag(articles, "Connexe")
    const horsSujet = countArticlesByTag(articles, "Hors sujet")

    return {
      pertinent,
      moyen,
      direct,
      fondamental,
      connexe,
      horsSujet,
      usefulArticles: articles.length - horsSujet,
    }
  }, [verrous, articles])

  const diagnosticState: StatusState =
    verrous.length > 0 ? "ok" : diagnosticLatest ? "warning" : "empty"

  const scholarState: StatusState =
    articles.length > 0 ? "ok" : scholarLatest ? "warning" : "empty"

  const loadData = async () => {
    setLoading(true)
    setError("")

    try {
      const projectList = await getProjects()
      setProjects(projectList)

      if (projectList.length === 0) {
        setProject(null)
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selected =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selected.id)

      const [
        projectData,
        documentsData,
        verrousData,
        articlesData,
        diagnosticData,
        scholarData,
      ] = await Promise.all([
        getProject(selected.id),
        getDocuments(selected.id).catch(() => []),
        getVerrous(selected.id).catch(() => []),
        getArticles(selected.id).catch(() => []),
        getDiagnosticLatest(selected.id).catch(() => null),
        getScholarLatest(selected.id).catch(() => null),
      ])

      setProject(projectData)
      setDocuments(documentsData)
      setVerrous(verrousData)
      setArticles(articlesData)
      setDiagnosticLatest(diagnosticData)
      setScholarLatest(scholarData)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le détail du projet."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const changeProject = async (projectId: number) => {
    setCurrentProjectId(projectId)
    setLoading(true)
    setError("")

    try {
      const [
        projectData,
        documentsData,
        verrousData,
        articlesData,
        diagnosticData,
        scholarData,
      ] = await Promise.all([
        getProject(projectId),
        getDocuments(projectId).catch(() => []),
        getVerrous(projectId).catch(() => []),
        getArticles(projectId).catch(() => []),
        getDiagnosticLatest(projectId).catch(() => null),
        getScholarLatest(projectId).catch(() => null),
      ])

      setProject(projectData)
      setDocuments(documentsData)
      setVerrous(verrousData)
      setArticles(articlesData)
      setDiagnosticLatest(diagnosticData)
      setScholarLatest(scholarData)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le projet sélectionné."
      )
    } finally {
      setLoading(false)
    }
  }

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
          : "Impossible d’importer le diagnostic existant."
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
          : "Impossible d’importer EnnoScholar."
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
          : "Impossible d’importer les documents existants."
      )
    } finally {
      setActionLoading(null)
    }
  }

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement du détail projet depuis FastAPI...
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-7xl mx-auto space-y-4">
        <Button variant="ghost" size="sm" onClick={() => navigateTo("projects")}>
          <ArrowLeft className="size-4 mr-2" />
          Retour aux projets
        </Button>

        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="p-5 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <div className="space-y-3">
              <div>
                <p className="text-sm font-semibold">Erreur détail projet</p>
                <p className="text-xs mt-1">{error}</p>
              </div>
              <Button size="sm" variant="outline" onClick={loadData}>
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
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet disponible.
            </p>
            <Button
              className="mt-4"
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

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="space-y-2">
          <Button variant="ghost" size="sm" onClick={() => navigateTo("projects")}>
            <ArrowLeft className="size-4 mr-2" />
            Retour aux projets
          </Button>

          <div>
            <div className="flex items-center gap-2 mb-1">
              <div className="size-8 rounded-lg bg-brand flex items-center justify-center">
                <FolderKanban className="size-4 text-brand-foreground" />
              </div>
              <h1 className="text-2xl font-bold text-foreground">
                {project.project_name}
              </h1>
            </div>

            <p className="text-sm text-muted-foreground">
              {project.organisme} — {project.year}
            </p>

            <div className="flex items-center gap-2 mt-2 flex-wrap">
              <Badge variant="outline" className={statusBadge(project.status)}>
                {project.status}
              </Badge>
              <Badge variant="outline">
                Dossier ID #{project.id}
              </Badge>
              <Badge variant="outline">
                {project.domain_label || "Domaine non renseigné"}
              </Badge>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {projects.length > 1 && (
            <select
              value={project.id}
              onChange={(event) => changeProject(Number(event.target.value))}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm"
            >
              {projects.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.organisme} — {item.project_name} — {item.year}
                </option>
              ))}
            </select>
          )}

          <Button variant="outline" size="sm" onClick={loadData}>
            <RefreshCw className="size-4 mr-2" />
            Actualiser
          </Button>
        </div>
      </div>

      {/* Main overview cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Documents</p>
            <p className="text-2xl font-bold text-foreground mt-1">
              {documents.length}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              fichiers projet
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Verrous</p>
            <p className="text-2xl font-bold text-brand mt-1">
              {verrous.length}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {stats.pertinent} pertinents · {stats.moyen} moyens
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Articles utiles</p>
            <p className="text-2xl font-bold text-success mt-1">
              {stats.usefulArticles}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              {stats.direct} directs
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Créé le</p>
            <p className="text-lg font-bold text-foreground mt-1">
              {formatDate(project.created_at)}
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              consultant #{project.consultant_id}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Action cards */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Card className={statusCardClass(diagnosticState)}>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <BrainCircuit className="size-4 text-brand" />
              EnnoDiagnostic
            </CardTitle>
            <CardDescription className="text-xs">
              Diagnostic technique CIR, verrous et validation consultant.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Verrous</p>
                <p className="text-xl font-bold text-foreground">{verrous.length}</p>
              </div>

              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Pertinents</p>
                <p className="text-xl font-bold text-success">{stats.pertinent}</p>
              </div>

              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Moyens</p>
                <p className="text-xl font-bold text-warning">{stats.moyen}</p>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {diagnosticState === "ok" ? (
                <>
                  <CheckCircle2 className="size-4 text-success" />
                  Verrous synchronisés et prêts à valider.
                </>
              ) : diagnosticState === "warning" ? (
                <>
                  <Clock className="size-4 text-warning" />
                  Diagnostic trouvé, mais verrous non synchronisés.
                </>
              ) : (
                <>
                  <AlertCircle className="size-4 text-muted-foreground" />
                  Aucun diagnostic importé pour ce projet.
                </>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              <Button className="bg-brand hover:bg-brand/90" onClick={openDiagnosis}>
                Ouvrir EnnoDiagnostic
              </Button>

              <Button
                variant="outline"
                onClick={importDiagnostic}
                disabled={actionLoading === "diagnostic"}
              >
                {actionLoading === "diagnostic" ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Database className="size-4 mr-2" />
                )}
                Importer résultat existant
              </Button>
            </div>
          </CardContent>
        </Card>

        <Card className={statusCardClass(scholarState)}>
          <CardHeader>
            <CardTitle className="text-sm flex items-center gap-2">
              <BookOpen className="size-4 text-brand" />
              EnnoScholar
            </CardTitle>
            <CardDescription className="text-xs">
              Recherche scientifique, tri des articles et état de l’art.
            </CardDescription>
          </CardHeader>

          <CardContent className="space-y-4">
            <div className="grid grid-cols-3 gap-3">
              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Articles</p>
                <p className="text-xl font-bold text-foreground">{articles.length}</p>
              </div>

              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Directs</p>
                <p className="text-xl font-bold text-success">{stats.direct}</p>
              </div>

              <div className="p-3 rounded-md bg-white border border-border text-center">
                <p className="text-xs text-muted-foreground">Hors sujet</p>
                <p className="text-xl font-bold text-muted-foreground">{stats.horsSujet}</p>
              </div>
            </div>

            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              {scholarState === "ok" ? (
                <>
                  <CheckCircle2 className="size-4 text-success" />
                  Articles synchronisés et prêts à sélectionner.
                </>
              ) : scholarState === "warning" ? (
                <>
                  <Clock className="size-4 text-warning" />
                  Rapport EnnoScholar trouvé, mais articles non synchronisés.
                </>
              ) : (
                <>
                  <AlertCircle className="size-4 text-muted-foreground" />
                  Aucun rapport EnnoScholar importé pour ce projet.
                </>
              )}
            </div>

            <div className="flex flex-wrap gap-2">
              <Button className="bg-brand hover:bg-brand/90" onClick={openScholar}>
                Ouvrir EnnoScholar
              </Button>

              <Button
                variant="outline"
                onClick={importScholar}
                disabled={actionLoading === "scholar"}
              >
                {actionLoading === "scholar" ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Database className="size-4 mr-2" />
                )}
                Importer résultat existant
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Documents */}
      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4 text-brand" />
                Documents du projet
              </CardTitle>
              <CardDescription className="text-xs">
                Documents enregistrés côté backend pour ce dossier.
              </CardDescription>
            </div>

            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={importDocuments}
                disabled={actionLoading === "documents"}
              >
                {actionLoading === "documents" ? (
                  <Loader2 className="size-4 mr-2 animate-spin" />
                ) : (
                  <Database className="size-4 mr-2" />
                )}
                Importer existants
              </Button>

              <Button variant="outline" size="sm" onClick={openUpload}>
                <Upload className="size-4 mr-2" />
                Déposer
              </Button>
            </div>
          </div>
        </CardHeader>

        <CardContent>
          {documents.length === 0 ? (
            <div className="p-6 text-center border border-dashed rounded-lg">
              <p className="text-sm font-medium text-foreground">
                Aucun document enregistré en base.
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Les résultats IA existent déjà dans les outputs, mais les documents
                n’ont pas encore été uploadés via l’API documents.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {documents.map((doc) => (
                <div
                  key={doc.id}
                  className="flex items-center justify-between gap-3 p-3 border border-border rounded-md bg-muted/30"
                >
                  <div className="flex items-start gap-3 min-w-0">
                    <FileText className="size-4 text-brand mt-0.5 flex-shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">
                        {sourceFileName(doc)}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {doc.document_type || doc.mime_type || "Type non renseigné"} ·{" "}
                        {formatSize(doc.size_bytes)} · {formatDate(doc.created_at)}
                      </p>
                    </div>
                  </div>

                  <Badge variant="outline">#{doc.id}</Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  ArrowRight,
  BarChart3,
  BookOpen,
  BrainCircuit,
  Building2,
  CheckCircle2,
  FileText,
  FolderKanban,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

import {
  getArticles,
  getDiagnosticLatest,
  getDocuments,
  getMe,
  getProjects,
  getScholarLatest,
  getVerrous,
  type ArticleRead,
  type DocumentRead,
  type ProjectRead,
  type UserRead,
  type VerrouRead,
} from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

interface DashboardPageProps {
  navigateTo: (page: AppPage) => void
}

type ProjectDashboard = {
  project: ProjectRead
  documents: DocumentRead[]
  verrous: VerrouRead[]
  articles: ArticleRead[]
  diagnosticLatest: any | null
  scholarLatest: any | null
}

function firstName(fullName?: string) {
  if (!fullName) return "consultant"
  return fullName.trim().split(/\s+/)[0] || "consultant"
}

function statusBadgeClass(status: string) {
  const value = status.toLowerCase()

  if (value.includes("terminé") || value.includes("completed")) {
    return "bg-success/10 text-success border-success/30"
  }

  if (value.includes("attente") || value.includes("validation")) {
    return "bg-warning/10 text-warning border-warning/30"
  }

  if (value.includes("créé") || value.includes("created")) {
    return "bg-muted text-muted-foreground border-border"
  }

  return "bg-brand/10 text-brand border-brand/30"
}

function riskFromProject(item: ProjectDashboard) {
  const pertinent = item.verrous.filter((verrou) =>
    (verrou.tag_cir || "").toUpperCase().includes("PERTINENT")
  ).length

  const moyen = item.verrous.filter((verrou) =>
    (verrou.tag_cir || "").toUpperCase().includes("MOYEN")
  ).length

  if (item.verrous.length === 0) return "Non évalué"
  if (pertinent >= 3 && moyen <= 3) return "Faible"
  return "Moyen"
}

function riskBadgeClass(risk: string) {
  switch (risk) {
    case "Faible":
      return "bg-success/10 text-success border-success/30"
    case "Moyen":
      return "bg-warning/10 text-warning border-warning/30"
    case "Élevé":
      return "bg-destructive/10 text-destructive border-destructive/30"
    default:
      return "bg-muted text-muted-foreground border-border"
  }
}

function diagnosticIsCompleted(item: ProjectDashboard) {
  return item.verrous.length > 0 || Boolean(item.diagnosticLatest)
}

function scholarIsCompleted(item: ProjectDashboard) {
  return item.articles.length > 0 || Boolean(item.scholarLatest)
}

function countUsefulArticles(articles: ArticleRead[]) {
  return articles.filter((article) => {
    const tag = (article.tag_article || "").toLowerCase()
    return !tag.includes("hors")
  }).length
}

function countDirectArticles(articles: ArticleRead[]) {
  return articles.filter((article) =>
    (article.tag_article || "").toLowerCase().includes("direct")
  ).length
}

function countPendingVerrous(verrous: VerrouRead[]) {
  return verrous.filter((verrou) => verrou.consultant_status === "en_attente").length
}

function countPendingArticles(articles: ArticleRead[]) {
  return articles.filter((article) => article.consultant_status === "en_attente").length
}

function formatScore(value: number | null) {
  if (value === null || value === undefined) return "—"

  const normalized = value <= 1 ? value * 100 : value
  return `${Math.round(normalized)}%`
}

function averageVerrouScore(verrous: VerrouRead[]) {
  const scores = verrous
    .map((verrou) => verrou.score)
    .filter((score): score is number => typeof score === "number")

  if (scores.length === 0) return null

  return scores.reduce((sum, score) => sum + score, 0) / scores.length
}

function getRecentProjects(items: ProjectDashboard[]) {
  return [...items]
    .sort((a, b) => {
      const dateA = new Date(a.project.created_at).getTime()
      const dateB = new Date(b.project.created_at).getTime()
      return dateB - dateA
    })
    .slice(0, 5)
}

function getActivity(items: ProjectDashboard[]) {
  const activities: {
    id: string
    icon: "diagnostic" | "scholar" | "document" | "project"
    title: string
    subtitle: string
    projectId: number
    target: AppPage
  }[] = []

  for (const item of items) {
    const label = `${item.project.organisme} / ${item.project.project_name}`

    if (item.verrous.length > 0) {
      activities.push({
        id: `diagnostic-${item.project.id}`,
        icon: "diagnostic",
        title: `EnnoDiagnostic a détecté ${item.verrous.length} verrou(s)`,
        subtitle: label,
        projectId: item.project.id,
        target: "diagnosis",
      })
    }

    if (item.articles.length > 0) {
      activities.push({
        id: `scholar-${item.project.id}`,
        icon: "scholar",
        title: `EnnoScholar a synchronisé ${item.articles.length} article(s)`,
        subtitle: `${countDirectArticles(item.articles)} direct(s) · ${countUsefulArticles(item.articles)} utile(s)`,
        projectId: item.project.id,
        target: "scholar",
      })
    }

    if (item.documents.length > 0) {
      activities.push({
        id: `documents-${item.project.id}`,
        icon: "document",
        title: `${item.documents.length} document(s) lié(s) au dossier`,
        subtitle: label,
        projectId: item.project.id,
        target: "project-detail",
      })
    }

    if (item.verrous.length === 0 && item.articles.length === 0 && item.documents.length === 0) {
      activities.push({
        id: `project-${item.project.id}`,
        icon: "project",
        title: "Nouveau dossier créé",
        subtitle: label,
        projectId: item.project.id,
        target: "project-detail",
      })
    }
  }

  return activities.slice(0, 6)
}

export default function DashboardPage({ navigateTo }: DashboardPageProps) {
  const [user, setUser] = useState<UserRead | null>(null)
  const [items, setItems] = useState<ProjectDashboard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const loadDashboard = async () => {
    setLoading(true)
    setError("")

    try {
      const [currentUser, projects] = await Promise.all([
        getMe(),
        getProjects(),
      ])

      setUser(currentUser)

      const dashboardItems = await Promise.all(
        projects.map(async (project) => {
          const [
            documents,
            verrous,
            articles,
            diagnosticLatest,
            scholarLatest,
          ] = await Promise.all([
            getDocuments(project.id).catch(() => []),
            getVerrous(project.id).catch(() => []),
            getArticles(project.id).catch(() => []),
            getDiagnosticLatest(project.id).catch(() => null),
            getScholarLatest(project.id).catch(() => null),
          ])

          return {
            project,
            documents,
            verrous,
            articles,
            diagnosticLatest,
            scholarLatest,
          }
        })
      )

      setItems(dashboardItems)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le tableau de bord."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadDashboard()
  }, [])

  const stats = useMemo(() => {
    const organismes = new Set(items.map((item) => item.project.organisme)).size
    const activeProjects = items.length
    const completedDiagnostics = items.filter(diagnosticIsCompleted).length
    const completedScholars = items.filter(scholarIsCompleted).length

    const pendingVerrous = items.reduce(
      (sum, item) => sum + countPendingVerrous(item.verrous),
      0
    )

    const pendingArticles = items.reduce(
      (sum, item) => sum + countPendingArticles(item.articles),
      0
    )

    const documents = items.reduce((sum, item) => sum + item.documents.length, 0)
    const articles = items.reduce((sum, item) => sum + item.articles.length, 0)
    const usefulArticles = items.reduce(
      (sum, item) => sum + countUsefulArticles(item.articles),
      0
    )

    return {
      organismes,
      activeProjects,
      completedDiagnostics,
      completedScholars,
      pendingAnalyses: pendingVerrous + pendingArticles,
      documents,
      articles,
      usefulArticles,
    }
  }, [items])

  const recentProjects = useMemo(() => getRecentProjects(items), [items])
  const activities = useMemo(() => getActivity(items), [items])

  const openProject = (projectId: number, target: AppPage = "project-detail") => {
    setCurrentProjectId(projectId)
    navigateTo(target)
  }

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement du tableau de bord depuis PostgreSQL...
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="p-5 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <div className="space-y-3">
              <div>
                <p className="text-sm font-semibold">Erreur tableau de bord</p>
                <p className="text-xs mt-1">{error}</p>
              </div>
              <Button size="sm" variant="outline" onClick={loadDashboard}>
                Réessayer
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-bold text-foreground tracking-tight">
            Bonjour, {firstName(user?.full_name)}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Aperçu réel de vos dossiers CIR, diagnostics et recherches scientifiques.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={loadDashboard}>
            <RefreshCw className="size-4 mr-2" />
            Actualiser
          </Button>

          <Button
            className="bg-brand hover:bg-brand/90"
            onClick={() => navigateTo("projects")}
          >
            <Sparkles className="size-4 mr-2" />
            Nouveau dossier
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <Card className="hover-lift">
          <CardContent className="p-5 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase">
                Organismes suivis
              </p>
              <p className="text-3xl font-bold text-foreground mt-2">
                {stats.organismes}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                depuis PostgreSQL
              </p>
            </div>

            <div className="size-11 rounded-lg bg-brand/10 flex items-center justify-center">
              <Building2 className="size-5 text-brand" />
            </div>
          </CardContent>
        </Card>

        <Card className="hover-lift">
          <CardContent className="p-5 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase">
                Dossiers CIR actifs
              </p>
              <p className="text-3xl font-bold text-foreground mt-2">
                {stats.activeProjects}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {stats.documents} document(s)
              </p>
            </div>

            <div className="size-11 rounded-lg bg-blue-500/10 flex items-center justify-center">
              <FolderKanban className="size-5 text-blue-600" />
            </div>
          </CardContent>
        </Card>

        <Card className="hover-lift">
          <CardContent className="p-5 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase">
                EnnoDiagnostics terminés
              </p>
              <p className="text-3xl font-bold text-foreground mt-2">
                {stats.completedDiagnostics}
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                {stats.completedScholars} EnnoScholar
              </p>
            </div>

            <div className="size-11 rounded-lg bg-success/10 flex items-center justify-center">
              <CheckCircle2 className="size-5 text-success" />
            </div>
          </CardContent>
        </Card>
      </div>

      {items.length === 0 && (
        <Card>
          <CardContent className="p-10 text-center">
            <div className="size-12 rounded-full bg-brand/10 flex items-center justify-center mx-auto mb-4">
              <FolderKanban className="size-6 text-brand" />
            </div>
            <p className="text-sm font-semibold text-foreground">
              Aucun dossier CIR pour ce consultant.
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Crée un projet pour commencer l’analyse.
            </p>
            <Button
              className="mt-4 bg-brand hover:bg-brand/90"
              onClick={() => navigateTo("projects")}
            >
              Aller aux projets
            </Button>
          </CardContent>
        </Card>
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2 space-y-4">
            <div className="flex items-center justify-between gap-3">
              <h2 className="text-lg font-bold text-foreground">
                Derniers dossiers analysés
              </h2>

              <Button
                variant="ghost"
                size="sm"
                onClick={() => navigateTo("projects")}
              >
                Voir tous
                <ArrowRight className="size-4 ml-2" />
              </Button>
            </div>

            <div className="space-y-3">
              {recentProjects.map((item) => {
                const score = averageVerrouScore(item.verrous)
                const risk = riskFromProject(item)
                const direct = countDirectArticles(item.articles)

                return (
                  <Card
                    key={item.project.id}
                    className="hover-lift cursor-pointer"
                    onClick={() => openProject(item.project.id, "project-detail")}
                  >
                    <CardContent className="p-5">
                      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                        <div className="space-y-2">
                          <p className="text-sm font-semibold text-foreground">
                            {item.project.organisme} — {item.project.project_name} — {item.project.year}
                          </p>

                          <div className="flex flex-wrap items-center gap-2">
                            <Badge
                              variant="outline"
                              className={statusBadgeClass(item.project.status)}
                            >
                              {item.project.status}
                            </Badge>

                            <Badge variant="outline">
                              {item.documents.length} document(s)
                            </Badge>

                            <Badge variant="outline">
                              {item.verrous.length} verrou(s)
                            </Badge>

                            <Badge variant="outline">
                              {direct} direct(s)
                            </Badge>
                          </div>
                        </div>

                        <div className="grid grid-cols-3 gap-4 lg:min-w-[320px]">
                          <div className="text-center">
                            <p className="text-xs text-muted-foreground">
                              Score verrous
                            </p>
                            <p className="text-lg font-bold text-brand mt-1">
                              {formatScore(score)}
                            </p>
                          </div>

                          <div className="text-center">
                            <p className="text-xs text-muted-foreground">
                              Articles utiles
                            </p>
                            <p className="text-lg font-bold text-success mt-1">
                              {countUsefulArticles(item.articles)}
                            </p>
                          </div>

                          <div className="text-center">
                            <p className="text-xs text-muted-foreground">
                              Risque
                            </p>
                            <Badge
                              variant="outline"
                              className={`mt-1 ${riskBadgeClass(risk)}`}
                            >
                              {risk}
                            </Badge>
                          </div>
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                )
              })}
            </div>
          </div>

          <div className="space-y-4">
            <h2 className="text-lg font-bold text-foreground">
              Activité récente
            </h2>

            <Card>
              <CardContent className="p-0">
                {activities.length === 0 ? (
                  <div className="p-6 text-center">
                    <p className="text-sm text-muted-foreground">
                      Aucune activité pour le moment.
                    </p>
                  </div>
                ) : (
                  <div className="divide-y divide-border">
                    {activities.map((activity) => {
                      const Icon =
                        activity.icon === "diagnostic"
                          ? BrainCircuit
                          : activity.icon === "scholar"
                            ? BookOpen
                            : activity.icon === "document"
                              ? FileText
                              : BarChart3

                      return (
                        <button
                          key={activity.id}
                          onClick={() => openProject(activity.projectId, activity.target)}
                          className="w-full p-4 text-left hover:bg-muted/40 transition-colors"
                        >
                          <div className="flex items-start gap-3">
                            <div className="size-8 rounded-full bg-brand/10 flex items-center justify-center flex-shrink-0">
                              <Icon className="size-4 text-brand" />
                            </div>

                            <div className="min-w-0">
                              <p className="text-sm font-medium text-foreground">
                                {activity.title}
                              </p>
                              <p className="text-xs text-muted-foreground mt-1">
                                {activity.subtitle}
                              </p>
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  )
}

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
  FilePenLine,
  FolderKanban,
  Loader2,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

import {
  getProjectOverviews,
  type ProjectOverview,
  type UserRead,
} from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

interface DashboardPageProps {
  navigateTo: (page: AppPage) => void
  user: UserRead
}

type ProjectDashboard = ProjectOverview

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
  const { count, pertinent, moyen } = item.diagnostic.verrous

  if (count === 0) return "Non évalué"
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
  return item.diagnostic.verrous.count > 0 || item.diagnostic.available
}

function scholarIsCompleted(item: ProjectDashboard) {
  return item.scholar.articles.count > 0 || item.scholar.available
}

function formatScore(value: number | null) {
  if (value === null || value === undefined) return "—"

  const normalized = value <= 1 ? value * 100 : value
  return `${Math.round(normalized)}%`
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

    if (item.diagnostic.verrous.count > 0) {
      activities.push({
        id: `diagnostic-${item.project.id}`,
        icon: "diagnostic",
        title: `EnnoDiagnostic a détecté ${item.diagnostic.verrous.count} verrou(s)`,
        subtitle: label,
        projectId: item.project.id,
        target: "diagnosis",
      })
    }

    if (item.scholar.articles.count > 0) {
      activities.push({
        id: `scholar-${item.project.id}`,
        icon: "scholar",
        title: `EnnoScholar a synchronisé ${item.scholar.articles.count} article(s)`,
        subtitle: `${item.scholar.articles.direct} direct(s) · ${item.scholar.articles.useful} utile(s)`,
        projectId: item.project.id,
        target: "scholar",
      })
    }

    if (item.documents.count > 0) {
      activities.push({
        id: `documents-${item.project.id}`,
        icon: "document",
        title: `${item.documents.count} document(s) lié(s) au dossier`,
        subtitle: label,
        projectId: item.project.id,
        target: "project-detail",
      })
    }

    if (item.diagnostic.verrous.count === 0 && item.scholar.articles.count === 0 && item.documents.count === 0) {
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

export default function DashboardPage({ navigateTo, user }: DashboardPageProps) {
  const [items, setItems] = useState<ProjectDashboard[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const loadDashboard = async () => {
    setLoading(true)
    setError("")

    try {
      setItems(await getProjectOverviews())
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
      (sum, item) => sum + item.diagnostic.verrous.pending,
      0
    )

    const pendingArticles = items.reduce(
      (sum, item) => sum + item.scholar.articles.pending,
      0
    )

    const documents = items.reduce((sum, item) => sum + item.documents.count, 0)
    const articles = items.reduce((sum, item) => sum + item.scholar.articles.count, 0)
    const usefulArticles = items.reduce(
      (sum, item) => sum + item.scholar.articles.useful,
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
    <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-7 lg:p-9">
      <section className="relative overflow-hidden rounded-[28px] bg-[radial-gradient(circle_at_10%_10%,rgba(216,180,254,.32),transparent_30%),linear-gradient(125deg,#260953,#5115a6_52%,#7e22ce)] px-6 py-9 text-center text-white shadow-xl shadow-violet-950/15 sm:px-10 sm:py-12">
        <div className="absolute -right-20 -top-24 size-72 rounded-full border border-white/10" />
        <div className="absolute -bottom-32 -left-20 size-80 rounded-full border border-white/10" />
        <button type="button" onClick={loadDashboard} className="absolute right-4 top-4 flex size-9 items-center justify-center rounded-full border border-white/15 bg-white/10 text-violet-100 transition hover:bg-white/20" aria-label="Actualiser le tableau de bord"><RefreshCw className="size-4" /></button>
        <div className="relative mx-auto max-w-3xl">
          <div className="mx-auto mb-4 flex w-fit items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 text-xs font-medium text-violet-100 backdrop-blur"><Sparkles className="size-3.5" />Espace de pilotage multi-agents</div>
          <h1 className="text-3xl font-semibold leading-tight tracking-[-0.04em] sm:text-5xl">Bonjour {firstName(user?.full_name)},<br/><span className="text-violet-200">quel dossier allons-nous faire avancer ?</span></h1>
          <p className="mx-auto mt-4 max-w-2xl text-sm leading-6 text-violet-100/75 sm:text-base">Créez un dossier, centralisez les preuves puis laissez chaque agent spécialisé intervenir au bon moment.</p>
          <div className="mt-7 flex flex-col items-center justify-center gap-3 sm:flex-row">
            <Button size="lg" className="h-12 min-w-52 bg-white text-violet-950 shadow-lg hover:bg-violet-50" onClick={() => navigateTo("new-project")}><Sparkles className="size-4" />Créer un nouveau dossier</Button>
            <Button size="lg" variant="outline" className="h-12 border-white/25 bg-white/5 text-white hover:bg-white/15 hover:text-white" onClick={() => navigateTo("projects")}><FolderKanban className="size-4" />Ouvrir mes projets</Button>
          </div>
        </div>
        <div className="relative mx-auto mt-9 grid max-w-4xl grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            { page: "upload" as AppPage, label: "Déposer", detail: "Documents", icon: Upload },
            { page: "diagnosis" as AppPage, label: "Diagnostiquer", detail: "Verrous CIR", icon: BrainCircuit },
            { page: "scholar" as AppPage, label: "Rechercher", detail: "État de l’art", icon: BookOpen },
            { page: "improvement" as AppPage, label: "Améliorer", detail: "Rédaction", icon: FilePenLine },
          ].map((action) => <button key={action.page} onClick={() => navigateTo(action.page)} className="group flex items-center gap-3 rounded-2xl border border-white/10 bg-white/[0.07] p-3 text-left backdrop-blur-sm transition hover:-translate-y-0.5 hover:bg-white/15"><span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-white/10"><action.icon className="size-4" /></span><span><span className="block text-sm font-semibold">{action.label}</span><span className="block text-[11px] text-violet-200/75">{action.detail}</span></span></button>)}
        </div>
      </section>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
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
                const score = item.diagnostic.verrous.average_score
                const risk = riskFromProject(item)
                const direct = item.scholar.articles.direct

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
                              {item.documents.count} document(s)
                            </Badge>

                            <Badge variant="outline">
                              {item.diagnostic.verrous.count} verrou(s)
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
                              {item.scholar.articles.useful}
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

"use client"

import { useEffect, useMemo, useState } from "react"
import {
  ArrowRight,
  BarChart3,
  BookOpen,
  BrainCircuit,
  Building2,
  FileText,
  FilePenLine,
  FolderKanban,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import {
  EmptyState,
  LoadingState,
  MetricCard,
  PageHeader,
  SectionHeader,
  StatusNotice,
  WorkflowSteps,
} from "@/components/ennosmart/workspace-ui"

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
  const isAdmin = user.role === "admin" || user.role === "superadmin"
  const isSuperadmin = user.role === "superadmin"
  const dashboardDescription = isSuperadmin
    ? "Supervisez le portefeuille, l'équipe et les services d'intelligence qui soutiennent la production CIR."
    : isAdmin
      ? "Pilotez le portefeuille, répartissez les dossiers et repérez les validations qui nécessitent une intervention."
      : "Retrouvez vos dossiers assignés, les validations en attente et la prochaine étape de chaque workflow CIR."
  const quickSteps = isSuperadmin
    ? [
        { label: "Superviser", description: "Équipe & projets", icon: BarChart3, onClick: () => navigateTo("admin") },
        { label: "Mémoire CIR", description: "Corpus validé", icon: BookOpen, onClick: () => navigateTo("cir-memory") },
        { label: "Configurer", description: "Modèles IA", icon: BrainCircuit, onClick: () => navigateTo("system-settings") },
        { label: "Contrôler", description: "Portefeuille", icon: FolderKanban, onClick: () => navigateTo("projects") },
      ]
    : isAdmin
      ? [
          { label: "Affecter", description: "Équipe & rôles", icon: Building2, onClick: () => navigateTo("admin") },
          { label: "Contrôler", description: "Portefeuille", icon: FolderKanban, onClick: () => navigateTo("projects") },
          { label: "Diagnostiquer", description: "Dossier actif", icon: BrainCircuit, onClick: () => navigateTo("diagnosis") },
          { label: "Valider", description: "Preuves", icon: BookOpen, onClick: () => navigateTo("scholar") },
        ]
      : [
          { label: "Déposer", description: "Documents", icon: Upload, onClick: () => navigateTo("upload") },
          { label: "Diagnostiquer", description: "Verrous CIR", icon: BrainCircuit, onClick: () => navigateTo("diagnosis") },
          { label: "Rechercher", description: "Preuves", icon: BookOpen, onClick: () => navigateTo("scholar") },
          { label: "Améliorer", description: "Livrables", icon: FilePenLine, onClick: () => navigateTo("improvement") },
        ]

  const openProject = (projectId: number, target: AppPage = "project-detail") => {
    setCurrentProjectId(projectId)
    navigateTo(target)
  }

  if (loading) {
    return (
      <div className="workspace-page">
        <LoadingState label="Chargement de l'espace de pilotage…" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="workspace-page">
        <StatusNotice
          state="failed"
          title="Le tableau de bord n'a pas pu être chargé"
          description={error}
          action={<Button size="sm" variant="outline" onClick={loadDashboard}>Réessayer</Button>}
        />
      </div>
    )
  }

  return (
    <div className="workspace-page-wide space-y-7">
      <PageHeader
        eyebrow={`${user.role === "superadmin" ? "Super administration" : user.role === "admin" ? "Administration" : "Production CIR"} · Bonjour ${firstName(user?.full_name)}`}
        title={isAdmin ? "Centre de pilotage CIR" : "Mon espace de production CIR"}
        description={dashboardDescription}
        actions={
          <>
            <Button variant="outline" onClick={loadDashboard}><RefreshCw data-icon="inline-start" />Actualiser</Button>
            {isAdmin ? <Button onClick={() => navigateTo("admin")}><BarChart3 data-icon="inline-start" />Ouvrir le pilotage</Button> : <Button onClick={() => navigateTo("new-project")}><Sparkles data-icon="inline-start" />Nouveau dossier</Button>}
          </>
        }
      />

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <MetricCard label={isAdmin ? "Portefeuille visible" : "Mes dossiers actifs"} value={stats.activeProjects} detail={`${stats.organismes} organisme(s)`} icon={FolderKanban} />
        <MetricCard label="Validations en attente" value={stats.pendingAnalyses} detail="verrous et articles" icon={BrainCircuit} tone={stats.pendingAnalyses > 0 ? "warning" : "neutral"} />
        <MetricCard label="Diagnostics disponibles" value={stats.completedDiagnostics} detail={`${stats.documents} document(s) indexé(s)`} icon={Building2} tone="info" />
        <MetricCard label="Articles utiles" value={stats.usefulArticles} detail={`${stats.articles} article(s) analysé(s)`} icon={BookOpen} tone="success" />
      </div>

      <section className="space-y-3" aria-labelledby="workflow-title">
        <SectionHeader id="workflow-title" title="Continuer le flux de travail" description="Chaque module reprend automatiquement le dossier actif." />
        <WorkflowSteps
          steps={quickSteps}
        />
      </section>

      {items.length === 0 && (
        <EmptyState
          icon={FolderKanban}
          title="Aucun dossier CIR"
          description="Créez un premier dossier pour déposer les sources et lancer l'analyse."
          action={<Button onClick={() => navigateTo("new-project")}>Créer le premier dossier</Button>}
        />
      )}

      {items.length > 0 && (
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
          <div className="xl:col-span-2 space-y-4">
            <SectionHeader
              title="Dossiers récents"
              description="Les derniers contextes consultés ou analysés."
              action={<Button
                variant="ghost"
                size="sm"
                onClick={() => navigateTo("projects")}
              >
                Voir tous <ArrowRight data-icon="inline-end" />
              </Button>}
            />

            <div className="space-y-3">
              {recentProjects.map((item) => {
                const score = item.diagnostic.verrous.average_score
                const risk = riskFromProject(item)
                const direct = item.scholar.articles.direct

                return (
                  <Card
                    key={item.project.id}
                    className="cursor-pointer hover:border-brand/25"
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
            <SectionHeader title="Activité récente" description="Éléments produits par les modules." />

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

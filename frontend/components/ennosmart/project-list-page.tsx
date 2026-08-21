"use client"

import {
  AppPage,
  NavigateOptions,
} from "@/components/ennosmart/app-shell"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Building2,
  Search,
  ArrowRight,
  ChevronDown,
  RefreshCw,
  PlusCircle,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { getProjects, type ProjectRead } from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"
import {
  EmptyState,
  LoadingState,
  PageHeader,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"

interface ProjectListPageProps {
  navigateTo: (page: AppPage, options?: NavigateOptions) => void
}

type OrganizationGroup = {
  id: string
  name: string
  projects: ProjectRead[]
}

const statusColor = (status: string) => {
  switch (status) {
    case "Validé":
    case "Diagnostic terminé":
    case "EnnoScholar terminé":
      return "bg-success/10 text-success"
    case "À vérifier consultant":
    case "Analyse terminée":
      return "bg-brand/10 text-brand"
    case "Créé":
      return "bg-muted text-muted-foreground"
    default:
      return "bg-warning/10 text-warning"
  }
}

function groupProjectsByOrganization(projects: ProjectRead[]) {
  const map = new Map<string, OrganizationGroup>()

  for (const project of projects) {
    const key = project.organisme || "Organisme inconnu"

    if (!map.has(key)) {
      map.set(key, {
        id: key.toLowerCase().replace(/\s+/g, "-"),
        name: key,
        projects: [],
      })
    }

    map.get(key)!.projects.push(project)
  }

  return Array.from(map.values())
}

export default function ProjectListPage({ navigateTo }: ProjectListPageProps) {
  const [search, setSearch] = useState("")
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [expandedOrgs, setExpandedOrgs] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  const loadProjects = async () => {
    setLoading(true)
    setError("")

    try {
      const data = await getProjects()
      setProjects(data)
      const groups = groupProjectsByOrganization(data)
      setExpandedOrgs(groups.map((org) => org.id))
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger les projets."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadProjects()
  }, [])

  const organizations = useMemo(() => groupProjectsByOrganization(projects), [projects])

  const filteredOrganizations = organizations.filter(
    (org) =>
      org.name.toLowerCase().includes(search.toLowerCase()) ||
      org.projects.some((proj) =>
        proj.project_name.toLowerCase().includes(search.toLowerCase())
      )
  )

  const toggleOrg = (orgId: string) => {
    setExpandedOrgs((prev) =>
      prev.includes(orgId)
        ? prev.filter((id) => id !== orgId)
        : [...prev, orgId]
    )
  }

  const openProjectDetail = (projectId: number) => {
    setCurrentProjectId(projectId)
    navigateTo("project-detail")
  }

  const addProjectForOrganization = (orgName: string) => {
    navigateTo("new-project", {
      newProjectPreset: {
        organisme: orgName,
        lockOrganisme: true,
      },
    })
  }

  return (
    <div className="workspace-page-wide space-y-6">
      <PageHeader
        eyebrow="Portefeuille"
        title="Projets CIR"
        description={`${organizations.length} organisme${organizations.length > 1 ? "s" : ""} · ${projects.length} dossier${projects.length > 1 ? "s" : ""}`}
        actions={<><Button variant="outline" onClick={loadProjects}><RefreshCw data-icon="inline-start" />Actualiser</Button><Button onClick={() => navigateTo("new-project")}><PlusCircle data-icon="inline-start" />Nouveau dossier</Button></>}
      />

      <div className="workspace-toolbar">
        <div className="relative w-full sm:max-w-md">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <Input
          placeholder="Rechercher un organisme ou un dossier..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10"
        />
        </div>
      </div>

      {loading && (
        <LoadingState label="Chargement des projets…" />
      )}

      {error && (
        <StatusNotice state="failed" title="Impossible de charger les projets" description={error} action={<Button size="sm" variant="outline" onClick={loadProjects}>Réessayer</Button>} />
      )}

      {!loading && !error && projects.length === 0 && (
        <EmptyState icon={Building2} title="Aucun projet" description="Créez un dossier pour commencer à centraliser les sources CIR." action={<Button onClick={() => navigateTo("new-project")}><PlusCircle data-icon="inline-start" />Nouveau dossier</Button>} />
      )}

      {!loading && !error && (
        <div className="space-y-6">
          {filteredOrganizations.map((org) => (
            <div key={org.id} className="space-y-3">
              <div className="flex w-full items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 shadow-xs transition-colors hover:border-brand/25">
                <button
                  type="button"
                  onClick={() => toggleOrg(org.id)}
                  className="flex flex-1 items-center gap-3 text-left min-w-0"
                >
                  <Building2 className="size-5 text-brand flex-shrink-0" />
                  <div className="text-left min-w-0">
                    <p className="font-semibold text-foreground truncate">
                      {org.name}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {org.projects.length} dossier
                      {org.projects.length > 1 ? "s" : ""}
                    </p>
                  </div>
                </button>

                <div className="flex items-center gap-2 flex-shrink-0">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => addProjectForOrganization(org.name)}
                    className="hidden border-brand/30 text-brand hover:bg-brand/10 sm:inline-flex"
                  >
                    <PlusCircle className="size-4 mr-2" />
                    Ajouter un projet
                  </Button>

                  <button
                    type="button"
                    onClick={() => toggleOrg(org.id)}
                    className="size-8 rounded-md flex items-center justify-center hover:bg-background"
                  >
                    <ChevronDown
                      className={`size-4 text-muted-foreground transition-transform ${
                        expandedOrgs.includes(org.id) ? "rotate-180" : ""
                      }`}
                    />
                  </button>
                </div>
              </div>

              {expandedOrgs.includes(org.id) && (
                <div className="space-y-2 pl-2">
                  {org.projects.map((project) => {
                    return (
                      <Card
                        key={project.id}
                        className="cursor-pointer border border-border animate-fadeIn hover:border-brand/25"
                        onClick={() => openProjectDetail(project.id)}
                      >
                        <CardContent className="p-4">
                          <div className="flex items-start justify-between gap-4 mb-3">
                            <div className="flex-1 min-w-0">
                              <p className="font-semibold text-foreground text-sm">
                                {project.project_name}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                {project.domain_label || "Domaine non renseigné"}
                              </p>
                            </div>
                            <Badge
                              className={statusColor(project.status)}
                              variant="outline"
                            >
                              {project.status}
                            </Badge>
                          </div>

                          <div className="grid grid-cols-2 sm:grid-cols-[140px_140px_1fr] gap-3 pt-3 border-t border-border">
                            <div className="text-center sm:text-left">
                              <p className="text-xs text-muted-foreground font-medium">
                                Année
                              </p>
                              <p className="text-sm font-semibold text-foreground mt-1">
                                {project.year}
                              </p>
                            </div>

                            <div className="text-center sm:text-left">
                              <p className="text-xs text-muted-foreground font-medium">
                                Dossier ID
                              </p>
                              <p className="text-sm font-semibold text-foreground mt-1">
                                #{project.id}
                              </p>
                            </div>

                            <div className="flex items-end justify-end col-span-2 sm:col-span-1">
                              <Button
                                size="sm"
                                variant="ghost"
                                className="text-brand hover:bg-brand/10"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  openProjectDetail(project.id)
                                }}
                              >
                                <ArrowRight className="size-4" />
                              </Button>
                            </div>
                          </div>
                        </CardContent>
                      </Card>
                    )
                  })}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

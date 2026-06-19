"use client"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Card, CardContent } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Building2,
  Search,
  ArrowRight,
  ChevronDown,
  Loader2,
  AlertCircle,
  RefreshCw,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { getProjects, type ProjectRead } from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

interface ProjectListPageProps {
  navigateTo: (page: AppPage) => void
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

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-bold text-foreground tracking-tight">
            Projets CIR
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            {organizations.length} organisme{organizations.length > 1 ? "s" : ""} ·{" "}
            {projects.length} dossier{projects.length > 1 ? "s" : ""}
          </p>
        </div>

        <Button variant="outline" size="sm" onClick={loadProjects}>
          <RefreshCw className="size-4 mr-2" />
          Actualiser
        </Button>
      </div>

      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
        <Input
          placeholder="Rechercher un organisme ou un dossier..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="pl-10"
        />
      </div>

      {loading && (
        <Card className="border border-border">
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement des projets depuis FastAPI...
          </CardContent>
        </Card>
      )}

      {error && (
        <Card className="border border-destructive/30 bg-destructive/10">
          <CardContent className="p-4 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <div>
              <p className="font-medium text-sm">
                Impossible de charger les projets
              </p>
              <p className="text-xs mt-1">{error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      {!loading && !error && projects.length === 0 && (
        <Card className="border border-border">
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet pour ce consultant.
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Crée un projet depuis Swagger ou depuis la future page de création.
            </p>
          </CardContent>
        </Card>
      )}

      {!loading && !error && (
        <div className="space-y-6">
          {filteredOrganizations.map((org) => (
            <div key={org.id} className="space-y-3">
              <button
                onClick={() => toggleOrg(org.id)}
                className="w-full flex items-center justify-between p-4 bg-muted/30 hover:bg-muted/50 rounded-lg border border-border transition-colors"
              >
                <div className="flex items-center gap-3">
                  <Building2 className="size-5 text-brand flex-shrink-0" />
                  <div className="text-left">
                    <p className="font-semibold text-foreground">{org.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {org.projects.length} dossier
                      {org.projects.length > 1 ? "s" : ""}
                    </p>
                  </div>
                </div>
                <ChevronDown
                  className={`size-4 text-muted-foreground transition-transform ${
                    expandedOrgs.includes(org.id) ? "rotate-180" : ""
                  }`}
                />
              </button>

              {expandedOrgs.includes(org.id) && (
                <div className="space-y-2 pl-2">
                  {org.projects.map((project) => {

                    return (
                      <Card
                        key={project.id}
                        className="border border-border hover-lift animate-fadeIn cursor-pointer"
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
                                onClick={() => openProjectDetail(project.id)}
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

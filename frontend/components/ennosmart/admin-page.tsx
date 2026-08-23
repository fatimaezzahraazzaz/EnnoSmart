"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  ArrowRight,
  BookOpen,
  BrainCircuit,
  Building2,
  CalendarClock,
  CheckCircle2,
  Clock3,
  FileText,
  FolderKanban,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserCheck,
  UserRound,
  Users,
} from "lucide-react"

import type {
  AppPage,
} from "@/components/ennosmart/app-shell"

import {
  Badge,
} from "@/components/ui/badge"

import {
  Button,
} from "@/components/ui/button"

import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card"

import {
  Input,
} from "@/components/ui/input"

import {
  Label,
} from "@/components/ui/label"

import {
  assignAdminProject,
  createAdminUser,
  getAdminOverview,
  getAdminProjects,
  getAdminUsers,
  getProjectOverviews,
  updateAdminProjectWorkflow,
  updateAdminUser,
  type AdminOverview,
  type AdminProject,
  type AdminUser,
  type ProjectOverview,
  type UserRead,
} from "@/lib/api"

import {
  setCurrentProjectId,
} from "@/lib/project-session"

import {
  LoadingState,
  PageHeader,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"


/* -------------------------------------------------------------------------- */
/* Types                                                                      */
/* -------------------------------------------------------------------------- */

type AdminProjectState =
  | "collecte"
  | "diagnostic"
  | "scholar"

type AttentionLevel =
  | "critical"
  | "warning"
  | "info"

type ProjectAlert = {
  id: string
  label: string
  level: AttentionLevel
}

interface AdminPageProps {
  user: UserRead

  /**
   * Optionnel pour rester compatible avec l'ancien AppShell.
   * Pour activer "Voir le dossier", passe navigateTo={navigateTo}.
   */
  navigateTo?: (
    page: AppPage,
  ) => void
}


/* -------------------------------------------------------------------------- */
/* Helpers généraux                                                           */
/* -------------------------------------------------------------------------- */

function normalizedPriority(
  priority?: string | null,
) {
  const value =
    String(
      priority ||
      "normale",
    ).toLowerCase()

  if (
    value === "basse" ||
    value === "normale" ||
    value === "haute" ||
    value === "urgente"
  ) {
    return value
  }

  return "normale"
}


function priorityLabel(
  priority?: string | null,
) {
  switch (
    normalizedPriority(
      priority,
    )
  ) {
    case "basse":
      return "Basse"

    case "haute":
      return "Haute"

    case "urgente":
      return "Urgente"

    default:
      return "Normale"
  }
}


function priorityBadgeClass(
  priority?: string | null,
) {
  switch (
    normalizedPriority(
      priority,
    )
  ) {
    case "urgente":
      return "border-rose-200 bg-rose-50 text-rose-700"

    case "haute":
      return "border-violet-200 bg-violet-50 text-violet-700"

    case "basse":
      return "border-slate-200 bg-slate-50 text-slate-500"

    default:
      return "border-blue-200 bg-blue-50 text-blue-700"
  }
}


function parseDate(
  value?: string | null,
) {
  if (!value) return null

  const date =
    new Date(value)

  if (
    Number.isNaN(
      date.getTime(),
    )
  ) {
    return null
  }

  return date
}


function formatDate(
  value?: string | null,
) {
  const date =
    parseDate(value)

  if (!date) return "—"

  return new Intl.DateTimeFormat(
    "fr-FR",
    {
      day: "2-digit",
      month: "short",
      year: "numeric",
    },
  ).format(date)
}


function formatDateTime(
  value?: string | null,
) {
  const date =
    parseDate(value)

  if (!date) return "—"

  return new Intl.DateTimeFormat(
    "fr-FR",
    {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
    },
  ).format(date)
}


function latestTimestamp(
  project: AdminProject,
  overview?: ProjectOverview,
) {
  const candidates = [
    project.created_at,
    project.workflow.updated_at,

    overview
      ?.documents
      .latest_at,

    overview
      ?.diagnostic
      .latest_run
      ?.completed_at,

    overview
      ?.diagnostic
      .latest_run
      ?.created_at,

    overview
      ?.diagnostic
      .verrous
      .latest_at,

    overview
      ?.scholar
      .latest_run
      ?.completed_at,

    overview
      ?.scholar
      .latest_run
      ?.created_at,

    overview
      ?.scholar
      .articles
      .latest_at,
  ]
    .map(
      parseDate,
    )
    .filter(
      (
        item,
      ): item is Date =>
        item !== null,
    )

  if (
    candidates.length ===
    0
  ) {
    return null
  }

  return candidates.sort(
    (
      a,
      b,
    ) =>
      b.getTime() -
      a.getTime(),
  )[0]
}


function daysSince(
  date: Date | null,
) {
  if (!date) return null

  const diff =
    Date.now() -
    date.getTime()

  if (diff < 0) return 0

  return Math.floor(
    diff /
      (
        1000 *
        60 *
        60 *
        24
      ),
  )
}


/* -------------------------------------------------------------------------- */
/* État automatique du dossier                                                */
/* -------------------------------------------------------------------------- */

function projectState(
  project: AdminProject,
): AdminProjectState {
  if (
    project.counts
      .scholar_runs >
    0
  ) {
    return "scholar"
  }

  if (
    project.counts
      .diagnostics >
    0
  ) {
    return "diagnostic"
  }

  return "collecte"
}


function projectStateLabel(
  state: AdminProjectState,
) {
  switch (state) {
    case "scholar":
      return "Recherche scientifique disponible"

    case "diagnostic":
      return "Diagnostic disponible"

    default:
      return "Collecte des sources"
  }
}


function projectStateDescription(
  project: AdminProject,
  state: AdminProjectState,
) {
  switch (state) {
    case "scholar":
      return `${project.counts.scholar_runs} recherche(s) EnnoScholar détectée(s)`

    case "diagnostic":
      return `${project.counts.diagnostics} diagnostic(s) détecté(s)`

    default:
      return project.counts.documents > 0
        ? `${project.counts.documents} document(s) disponible(s)`
        : "Aucun document disponible"
  }
}


/* -------------------------------------------------------------------------- */
/* Alertes automatiques                                                       */
/* -------------------------------------------------------------------------- */

function getProjectAlerts(
  project: AdminProject,
  overview?: ProjectOverview,
): ProjectAlert[] {
  const alerts: ProjectAlert[] =
    []

  if (
    !project.consultant
  ) {
    alerts.push({
      id: "unassigned",
      label:
        "Aucun consultant affecté",
      level: "critical",
    })
  }

  if (
    project.counts
      .documents === 0
  ) {
    alerts.push({
      id: "no-documents",
      label:
        "Aucun document déposé",
      level: "warning",
    })
  }

  if (
    project.counts
      .documents > 0 &&
    project.counts
      .diagnostics === 0
  ) {
    alerts.push({
      id: "diagnostic-missing",
      label:
        "Documents présents, diagnostic non lancé",
      level: "warning",
    })
  }

  if (
    project.counts
      .diagnostics > 0 &&
    project.counts
      .scholar_runs === 0
  ) {
    alerts.push({
      id: "scholar-missing",
      label:
        "Diagnostic disponible, EnnoScholar non lancé",
      level: "warning",
    })
  }

  const dueDate =
    parseDate(
      project.workflow
        .due_date,
    )

  if (
    dueDate &&
    dueDate.getTime() <
      Date.now()
  ) {
    alerts.push({
      id: "due-date",
      label:
        `Échéance dépassée depuis le ${formatDate(
          project.workflow
            .due_date,
        )}`,
      level: "critical",
    })
  }

  const latest =
    latestTimestamp(
      project,
      overview,
    )

  const inactiveDays =
    daysSince(latest)

  if (
    inactiveDays !== null &&
    inactiveDays >= 7
  ) {
    alerts.push({
      id: "inactive",
      label:
        `Aucune activité détectée depuis ${inactiveDays} jours`,
      level: "warning",
    })
  }

  return alerts
}


function attentionBadgeClass(
  count: number,
) {
  if (count <= 0) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700"
  }

  return "border-violet-200 bg-violet-50 text-violet-700"
}


/* -------------------------------------------------------------------------- */
/* Stat card                                                                  */
/* -------------------------------------------------------------------------- */

function StatCard({
  label,
  value,
  detail,
  icon: Icon,
  tone = "brand",
}: {
  label: string
  value: number
  detail: string
  icon: typeof Users
  tone?:
    | "brand"
    | "violet"
    | "success"
    | "slate"
}) {
  const toneClasses = {
    brand: {
      icon:
        "bg-brand/[0.08] text-brand",
      value:
        "text-foreground",
    },

    violet: {
      icon:
        "bg-violet-50 text-violet-600",
      value:
        "text-violet-700",
    },

    success: {
      icon:
        "bg-emerald-50 text-emerald-600",
      value:
        "text-emerald-700",
    },

    slate: {
      icon:
        "bg-slate-100 text-slate-600",
      value:
        "text-slate-800",
    },
  }[tone]

  return (
    <Card className="rounded-2xl border-border/80 shadow-sm">
      <CardContent className="flex items-center gap-4 p-5">
        <div
          className={`grid size-11 shrink-0 place-items-center rounded-xl ${toneClasses.icon}`}
        >
          <Icon className="size-5" />
        </div>

        <div className="min-w-0">
          <p
            className={`text-2xl font-semibold tracking-[-0.03em] ${toneClasses.value}`}
          >
            {value}
          </p>

          <p className="text-sm font-medium text-foreground">
            {label}
          </p>

          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {detail}
          </p>
        </div>
      </CardContent>
    </Card>
  )
}


/* -------------------------------------------------------------------------- */
/* Pipeline automatique                                                       */
/* -------------------------------------------------------------------------- */

function AutomaticPipeline({
  project,
}: {
  project: AdminProject
}) {
  const docsDone =
    project.counts
      .documents > 0

  const diagnosticDone =
    project.counts
      .diagnostics > 0

  const scholarDone =
    project.counts
      .scholar_runs > 0

  const steps = [
    {
      label: "Sources",
      detail:
        `${project.counts.documents} doc.`,
      done: docsDone,
      icon: FileText,
    },

    {
      label: "Diagnostic",
      detail:
        `${project.counts.diagnostics} run.`,
      done:
        diagnosticDone,
      icon: BrainCircuit,
    },

    {
      label: "EnnoScholar",
      detail:
        `${project.counts.scholar_runs} recherche.`,
      done:
        scholarDone,
      icon: BookOpen,
    },
  ]

  return (
    <div className="grid gap-2 sm:grid-cols-3">
      {steps.map(
        (
          step,
          index,
        ) => {
          const Icon =
            step.icon

          const isCurrent =
            !step.done &&
            (
              index === 0 ||
              steps[
                index - 1
              ]?.done
            )

          return (
            <div
              key={
                step.label
              }
              className={`relative rounded-xl border px-3 py-3 ${
                step.done
                  ? "border-emerald-200/80 bg-emerald-50/55"
                  : isCurrent
                    ? "border-violet-200 bg-violet-50/55"
                    : "border-border/80 bg-muted/[0.12]"
              }`}
            >
              <div className="flex items-center gap-2.5">
                <span
                  className={`grid size-8 shrink-0 place-items-center rounded-lg ${
                    step.done
                      ? "bg-emerald-100 text-emerald-700"
                      : isCurrent
                        ? "bg-violet-100 text-violet-700"
                        : "bg-background text-muted-foreground"
                  }`}
                >
                  {step.done ? (
                    <CheckCircle2 className="size-4" />
                  ) : (
                    <Icon className="size-4" />
                  )}
                </span>

                <div className="min-w-0">
                  <p className="truncate text-xs font-semibold text-foreground">
                    {
                      step.label
                    }
                  </p>

                  <p className="mt-0.5 truncate text-[10px] text-muted-foreground">
                    {
                      step.detail
                    }
                  </p>
                </div>
              </div>
            </div>
          )
        },
      )}
    </div>
  )
}


/* -------------------------------------------------------------------------- */
/* Project card                                                               */
/* -------------------------------------------------------------------------- */

function ProjectCard({
  project,
  overview,
  users,
  priorityValue,
  busy,
  navigateTo,
  onAssign,
  onPriorityChange,
  onSavePriority,
}: {
  project: AdminProject
  overview?: ProjectOverview
  users: AdminUser[]
  priorityValue: string
  busy: string | null
  navigateTo?: (
    page: AppPage,
  ) => void
  onAssign: (
    projectId: number,
    consultantId: number,
  ) => void
  onPriorityChange: (
    projectId: number,
    value: string,
  ) => void
  onSavePriority: (
    projectId: number,
  ) => void
}) {
  const state =
    projectState(project)

  const alerts =
    getProjectAlerts(
      project,
      overview,
    )

  const latestActivity =
    latestTimestamp(
      project,
      overview,
    )

  const canOpen =
    Boolean(navigateTo)

  const openProject =
    () => {
      if (!navigateTo) return

      setCurrentProjectId(
        project.id,
      )

      navigateTo(
        "project-detail",
      )
    }

  const priorityChanged =
    normalizedPriority(
      priorityValue,
    ) !==
    normalizedPriority(
      project.workflow
        .priority,
    )

  return (
    <article className="overflow-hidden rounded-2xl border border-border/80 bg-card shadow-sm transition hover:border-brand/15 hover:shadow-[0_12px_34px_rgba(42,25,75,.06)]">

      {/* Header */}

      <div className="flex flex-col gap-4 border-b border-border/65 px-5 py-4 lg:flex-row lg:items-start lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/[0.07] text-brand">
              <Building2 className="size-4" />
            </span>

            <div className="min-w-0">
              <div className="flex flex-wrap items-center gap-2">
                <h3 className="truncate text-sm font-semibold text-foreground">
                  {
                    project.organisme
                  }{" "}
                  ·{" "}
                  {
                    project.project_name
                  }
                </h3>

                <Badge
                  variant="outline"
                  className="rounded-full bg-background"
                >
                  {
                    project.year
                  }
                </Badge>

                <Badge
                  variant="outline"
                  className={`rounded-full ${priorityBadgeClass(
                    project.workflow
                      .priority,
                  )}`}
                >
                  Priorité{" "}
                  {
                    priorityLabel(
                      project.workflow
                        .priority,
                    ).toLowerCase()
                  }
                </Badge>
              </div>

              <p className="mt-1 text-xs text-muted-foreground">
                {
                  project.domain_label ||
                  "Domaine non renseigné"
                }
              </p>
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Badge
            variant="outline"
            className={`rounded-full ${attentionBadgeClass(
              alerts.length,
            )}`}
          >
            {alerts.length >
            0
              ? `${alerts.length} point(s) à surveiller`
              : "Aucun signal d’alerte"}
          </Badge>

          {canOpen && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="rounded-xl"
              onClick={
                openProject
              }
            >
              Voir le dossier
              <ArrowRight className="size-4" />
            </Button>
          )}
        </div>
      </div>


      <div className="grid gap-5 p-5 xl:grid-cols-[minmax(0,1.5fr)_340px]">

        {/* Corps principal */}

        <div className="space-y-4">

          {/* Etat réel */}

          <div className="flex flex-col gap-3 rounded-xl border border-brand/10 bg-brand/[0.025] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-start gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-background text-brand shadow-sm ring-1 ring-brand/10">
                {state ===
                "scholar" ? (
                  <BookOpen className="size-4" />
                ) : state ===
                  "diagnostic" ? (
                  <BrainCircuit className="size-4" />
                ) : (
                  <FileText className="size-4" />
                )}
              </span>

              <div>
                <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-brand/70">
                  État détecté automatiquement
                </p>

                <p className="mt-1 text-sm font-semibold text-foreground">
                  {
                    projectStateLabel(
                      state,
                    )
                  }
                </p>

                <p className="mt-0.5 text-xs text-muted-foreground">
                  {
                    projectStateDescription(
                      project,
                      state,
                    )
                  }
                </p>
              </div>
            </div>

            <div className="shrink-0 text-left sm:text-right">
              <p className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                Dernière activité détectée
              </p>

              <p className="mt-1 text-xs font-medium text-foreground">
                {
                  latestActivity
                    ? formatDateTime(
                        latestActivity.toISOString(),
                      )
                    : "Aucune activité"
                }
              </p>
            </div>
          </div>


          {/* Pipeline */}

          <AutomaticPipeline
            project={
              project
            }
          />


          {/* Alertes */}

          {alerts.length >
          0 ? (
            <div className="space-y-2">
              <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                À surveiller
              </p>

              <div className="flex flex-wrap gap-2">
                {alerts.map(
                  (
                    alert,
                  ) => (
                    <span
                      key={
                        alert.id
                      }
                      className={`inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-[11px] font-medium ${
                        alert.level ===
                        "critical"
                          ? "border-rose-200 bg-rose-50 text-rose-700"
                          : alert.level ===
                              "warning"
                            ? "border-violet-200 bg-violet-50 text-violet-700"
                            : "border-blue-200 bg-blue-50 text-blue-700"
                      }`}
                    >
                      <AlertCircle className="size-3.5" />

                      {
                        alert.label
                      }
                    </span>
                  ),
                )}
              </div>
            </div>
          ) : (
            <div className="flex items-center gap-2 text-xs text-emerald-700">
              <CheckCircle2 className="size-4" />
              Aucun point de vigilance automatique détecté.
            </div>
          )}
        </div>


        {/* Pilotage humain */}

        <aside className="space-y-4 rounded-xl border border-border/75 bg-muted/[0.11] p-4">
          <div>
            <p className="text-xs font-semibold text-foreground">
              Pilotage administratif
            </p>

            <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
              Seuls l’affectation et la priorité sont définis manuellement.
            </p>
          </div>


          {/* Consultant */}

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              Consultant affecté
            </Label>

            <select
              value={
                project.consultant
                  ?.id ||
                ""
              }
              onChange={(
                event,
              ) =>
                onAssign(
                  project.id,
                  Number(
                    event.target
                      .value,
                  ),
                )
              }
              className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
              disabled={
                busy ===
                `project-${project.id}`
              }
            >
              <option
                value=""
                disabled
              >
                Choisir un consultant
              </option>

              {users
                .filter(
                  (
                    item,
                  ) =>
                    item.is_active &&
                    item.role !==
                      "superadmin",
                )
                .map(
                  (
                    item,
                  ) => (
                    <option
                      key={
                        item.id
                      }
                      value={
                        item.id
                      }
                    >
                      {
                        item.full_name
                      }
                    </option>
                  ),
                )}
            </select>
          </div>


          {/* Priorité */}

          <div className="space-y-1.5">
            <Label className="text-xs text-muted-foreground">
              Priorité
            </Label>

            <select
              value={
                priorityValue
              }
              onChange={(
                event,
              ) =>
                onPriorityChange(
                  project.id,
                  event.target
                    .value,
                )
              }
              className="h-10 w-full rounded-xl border border-border bg-background px-3 text-sm outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
            >
              <option value="basse">
                Basse
              </option>

              <option value="normale">
                Normale
              </option>

              <option value="haute">
                Haute
              </option>

              <option value="urgente">
                Urgente
              </option>
            </select>
          </div>


          {project.workflow
            .due_date && (
            <div className="flex items-start gap-2 rounded-xl border border-border/70 bg-background px-3 py-2.5">
              <CalendarClock className="mt-0.5 size-4 shrink-0 text-muted-foreground" />

              <div>
                <p className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">
                  Échéance actuelle
                </p>

                <p className="mt-0.5 text-xs font-medium text-foreground">
                  {
                    formatDate(
                      project.workflow
                        .due_date,
                    )
                  }
                </p>
              </div>
            </div>
          )}


          <Button
            type="button"
            className="w-full rounded-xl"
            disabled={
              !priorityChanged ||
              busy ===
                `priority-${project.id}`
            }
            onClick={() =>
              onSavePriority(
                project.id,
              )
            }
          >
            {busy ===
            `priority-${project.id}` ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <UserCheck className="size-4" />
            )}

            {priorityChanged
              ? "Enregistrer la priorité"
              : "Priorité enregistrée"}
          </Button>
        </aside>
      </div>
    </article>
  )
}


/* -------------------------------------------------------------------------- */
/* Admin page                                                                 */
/* -------------------------------------------------------------------------- */

export default function AdminPage({
  user,
  navigateTo,
}: AdminPageProps) {
  const [
    tab,
    setTab,
  ] =
    useState<
      "team" | "projects"
    >("team")

  const [
    overview,
    setOverview,
  ] =
    useState<AdminOverview | null>(
      null,
    )

  const [
    users,
    setUsers,
  ] =
    useState<AdminUser[]>([])

  const [
    projects,
    setProjects,
  ] =
    useState<AdminProject[]>([])

  const [
    projectOverviews,
    setProjectOverviews,
  ] =
    useState<ProjectOverview[]>([])

  const [
    priorityEdits,
    setPriorityEdits,
  ] =
    useState<
      Record<
        number,
        string
      >
    >({})

  const [
    search,
    setSearch,
  ] =
    useState("")

  const [
    projectConsultantFilter,
    setProjectConsultantFilter,
  ] =
    useState("all")

  const [
    projectStateFilter,
    setProjectStateFilter,
  ] =
    useState("all")

  const [
    projectPriorityFilter,
    setProjectPriorityFilter,
  ] =
    useState("all")

  const [
    showCreate,
    setShowCreate,
  ] =
    useState(false)

  const [
    createForm,
    setCreateForm,
  ] =
    useState({
      full_name: "",
      email: "",
      password: "",
      company: "",
      job_title:
        "Consultant CIR",
      role:
        "consultant" as AdminUser["role"],
    })

  const [
    loading,
    setLoading,
  ] =
    useState(true)

  const [
    busy,
    setBusy,
  ] =
    useState<
      string | null
    >(null)

  const [
    message,
    setMessage,
  ] =
    useState("")

  const [
    error,
    setError,
  ] =
    useState("")


  /* ---------------------------------------------------------------------- */
  /* Chargement                                                             */
  /* ---------------------------------------------------------------------- */

  const load =
    async () => {
      setLoading(true)
      setError("")

      try {
        const [
          summary,
          team,
          dossiers,
          dossierOverviews,
        ] =
          await Promise.all([
            getAdminOverview(),
            getAdminUsers(),
            getAdminProjects(),

            getProjectOverviews()
              .catch(
                () =>
                  [] as ProjectOverview[],
              ),
          ])

        setOverview(
          summary,
        )

        setUsers(
          team,
        )

        setProjects(
          dossiers,
        )

        setProjectOverviews(
          dossierOverviews,
        )

        setPriorityEdits(
          Object.fromEntries(
            dossiers.map(
              (
                project,
              ) => [
                project.id,
                normalizedPriority(
                  project.workflow
                    .priority,
                ),
              ],
            ),
          ),
        )
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Administration indisponible.",
        )
      } finally {
        setLoading(false)
      }
    }


  useEffect(() => {
    void load()
  }, [])


  /* ---------------------------------------------------------------------- */
  /* Mapping overview projet                                                */
  /* ---------------------------------------------------------------------- */

  const overviewByProjectId =
    useMemo(
      () =>
        new Map(
          projectOverviews.map(
            (
              item,
            ) => [
              item.project.id,
              item,
            ],
          ),
        ),
      [projectOverviews],
    )


  /* ---------------------------------------------------------------------- */
  /* Stats de pilotage                                                      */
  /* ---------------------------------------------------------------------- */

  const projectPilotStats =
    useMemo(() => {
      let attention = 0
      let unassigned = 0
      let inactive = 0

      for (
        const project of
        projects
      ) {
        const projectOverview =
          overviewByProjectId.get(
            project.id,
          )

        if (
          getProjectAlerts(
            project,
            projectOverview,
          ).length > 0
        ) {
          attention += 1
        }

        if (
          !project.consultant
        ) {
          unassigned += 1
        }

        const last =
          latestTimestamp(
            project,
            projectOverview,
          )

        const days =
          daysSince(last)

        if (
          days !== null &&
          days >= 7
        ) {
          inactive += 1
        }
      }

      return {
        attention,
        unassigned,
        inactive,
      }
    }, [
      projects,
      overviewByProjectId,
    ])


  /* ---------------------------------------------------------------------- */
  /* Filtres                                                                */
  /* ---------------------------------------------------------------------- */

  const filteredUsers =
    useMemo(
      () =>
        users.filter(
          (
            item,
          ) =>
            `${item.full_name} ${item.email} ${item.company || ""}`
              .toLowerCase()
              .includes(
                search.toLowerCase(),
              ),
        ),
      [
        users,
        search,
      ],
    )


  const filteredProjects =
    useMemo(() => {
      const normalizedSearch =
        search
          .trim()
          .toLowerCase()

      return projects.filter(
        (
          item,
        ) => {
          const state =
            projectState(
              item,
            )

          const matchesSearch =
            !normalizedSearch ||
            `${item.organisme} ${item.project_name} ${item.consultant?.full_name || ""} ${item.year}`
              .toLowerCase()
              .includes(
                normalizedSearch,
              )

          const matchesConsultant =
            projectConsultantFilter ===
              "all" ||
            (
              projectConsultantFilter ===
                "unassigned" &&
              !item.consultant
            ) ||
            String(
              item.consultant
                ?.id ||
                "",
            ) ===
              projectConsultantFilter

          const matchesState =
            projectStateFilter ===
              "all" ||
            (
              projectStateFilter ===
                "attention" &&
              getProjectAlerts(
                item,
                overviewByProjectId.get(
                  item.id,
                ),
              ).length > 0
            ) ||
            state ===
              projectStateFilter

          const matchesPriority =
            projectPriorityFilter ===
              "all" ||
            normalizedPriority(
              item.workflow
                .priority,
            ) ===
              projectPriorityFilter

          return (
            matchesSearch &&
            matchesConsultant &&
            matchesState &&
            matchesPriority
          )
        },
      )
    }, [
      projects,
      search,
      projectConsultantFilter,
      projectStateFilter,
      projectPriorityFilter,
      overviewByProjectId,
    ])


  /* ---------------------------------------------------------------------- */
  /* Utilisateurs                                                           */
  /* ---------------------------------------------------------------------- */

  const createUser =
    async (
      event: React.FormEvent,
    ) => {
      event.preventDefault()

      setBusy(
        "create",
      )

      setError("")
      setMessage("")

      try {
        await createAdminUser({
          ...createForm,

          company:
            createForm.company ||
            undefined,
        })

        setCreateForm({
          full_name: "",
          email: "",
          password: "",
          company: "",
          job_title:
            "Consultant CIR",
          role:
            "consultant",
        })

        setShowCreate(false)

        setMessage(
          "Le compte a été créé et peut se connecter.",
        )

        await load()
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Création impossible.",
        )
      } finally {
        setBusy(null)
      }
    }


  const toggleUser =
    async (
      target: AdminUser,
    ) => {
      setBusy(
        `user-${target.id}`,
      )

      setError("")
      setMessage("")

      try {
        await updateAdminUser(
          target.id,
          {
            is_active:
              !target.is_active,
          },
        )

        setMessage(
          `Compte ${
            target.is_active
              ? "désactivé"
              : "activé"
          }.`,
        )

        await load()
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Action impossible.",
        )
      } finally {
        setBusy(null)
      }
    }


  /* ---------------------------------------------------------------------- */
  /* Affectation                                                            */
  /* ---------------------------------------------------------------------- */

  const assign =
    async (
      projectId: number,
      consultantId: number,
    ) => {
      setBusy(
        `project-${projectId}`,
      )

      setError("")
      setMessage("")

      try {
        const updated =
          await assignAdminProject(
            projectId,
            consultantId,
          )

        setProjects(
          (
            items,
          ) =>
            items.map(
              (
                item,
              ) =>
                item.id ===
                projectId
                  ? updated
                  : item,
            ),
        )

        setMessage(
          "Affectation mise à jour.",
        )
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Affectation impossible.",
        )
      } finally {
        setBusy(null)
      }
    }


  /* ---------------------------------------------------------------------- */
  /* Priorité uniquement                                                    */
  /* ---------------------------------------------------------------------- */

  const savePriority =
    async (
      projectId: number,
    ) => {
      const project =
        projects.find(
          (
            item,
          ) =>
            item.id ===
            projectId,
        )

      if (!project) return

      const priority =
        priorityEdits[
          projectId
        ] ||
        normalizedPriority(
          project.workflow
            .priority,
        )

      setBusy(
        `priority-${projectId}`,
      )

      setError("")
      setMessage("")

      try {
        const updated =
          await updateAdminProjectWorkflow(
            projectId,
            {
              /*
               * Le backend actuel exige encore stage + progress_percent.
               * On les renvoie strictement à l'identique :
               * ils ne sont plus éditables dans l'interface.
               */
              stage:
                project.workflow
                  .stage,

              progress_percent:
                project.workflow
                  .progress_percent,

              priority,

              due_date:
                project.workflow
                  .due_date,

              notes:
                project.workflow
                  .notes,
            },
          )

        setProjects(
          (
            items,
          ) =>
            items.map(
              (
                item,
              ) =>
                item.id ===
                projectId
                  ? updated
                  : item,
            ),
        )

        setPriorityEdits(
          (
            current,
          ) => ({
            ...current,
            [projectId]:
              normalizedPriority(
                updated.workflow
                  .priority,
              ),
          }),
        )

        setMessage(
          "Priorité du dossier mise à jour.",
        )
      } catch (err) {
        setError(
          err instanceof Error
            ? err.message
            : "Mise à jour impossible.",
        )
      } finally {
        setBusy(null)
      }
    }


  /* ---------------------------------------------------------------------- */
  /* Loading                                                                */
  /* ---------------------------------------------------------------------- */

  if (
    loading &&
    !overview
  ) {
    return (
      <LoadingState label="Chargement de l'administration…" />
    )
  }


  /* ---------------------------------------------------------------------- */
  /* Render                                                                 */
  /* ---------------------------------------------------------------------- */

  return (
    <div className="workspace-page-wide space-y-5 pb-10">

      {/* ================================================================== */}
      {/* Header                                                             */}
      {/* ================================================================== */}

      <PageHeader
        eyebrow="Pilotage administratif"
        title="Équipe & portefeuille"
        description="Affectez les consultants et pilotez les dossiers à partir de l’activité réellement détectée dans EnnoSmart."
        icon={
          ShieldCheck
        }
        actions={
          <Button
            variant="outline"
            onClick={load}
            disabled={
              loading
            }
            className="rounded-xl"
          >
            <RefreshCw
              className={
                loading
                  ? "size-4 animate-spin"
                  : "size-4"
              }
            />

            Actualiser
          </Button>
        }
      />


      {message && (
        <StatusNotice
          state="validated"
          title={
            message
          }
        />
      )}


      {error && (
        <StatusNotice
          state="failed"
          title="Action administrative impossible"
          description={
            error
          }
        />
      )}


      {/* ================================================================== */}
      {/* Stats                                                              */}
      {/* ================================================================== */}

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          icon={Users}
          label="Consultants"
          value={
            overview?.users
              .consultants ||
            0
          }
          detail={`${overview?.users.active || 0} comptes actifs`}
          tone="brand"
        />

        <StatCard
          icon={
            FolderKanban
          }
          label="Projets"
          value={
            projects.length
          }
          detail="dossiers visibles"
          tone="slate"
        />

        <StatCard
          icon={
            AlertCircle
          }
          label="À surveiller"
          value={
            projectPilotStats.attention
          }
          detail={`${projectPilotStats.inactive} sans activité depuis +7 j`}
          tone="violet"
        />

        <StatCard
          icon={
            UserRound
          }
          label="Sans consultant"
          value={
            projectPilotStats.unassigned
          }
          detail="affectation nécessaire"
          tone={
            projectPilotStats.unassigned >
            0
              ? "violet"
              : "success"
          }
        />
      </div>


      {/* ================================================================== */}
      {/* Main card                                                          */}
      {/* ================================================================== */}

      <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">

        {/* ---------------------------------------------------------------- */}
        {/* Tabs / recherche / filtres                                      */}
        {/* ---------------------------------------------------------------- */}

        <CardHeader className="border-b border-border/70 px-4 py-4 sm:px-5">
          <div className="flex flex-col gap-4 xl:flex-row xl:items-center xl:justify-between">

            <div className="flex w-fit gap-1 rounded-xl bg-muted p-1">
              <button
                type="button"
                onClick={() =>
                  setTab(
                    "team",
                  )
                }
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  tab ===
                  "team"
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Users className="mr-2 inline size-4" />
                Consultants
              </button>

              <button
                type="button"
                onClick={() =>
                  setTab(
                    "projects",
                  )
                }
                className={`rounded-lg px-4 py-2 text-sm font-medium transition ${
                  tab ===
                  "projects"
                    ? "bg-card text-foreground shadow-sm"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <FolderKanban className="mr-2 inline size-4" />
                Projets
              </button>
            </div>


            <div className="flex flex-col gap-2 lg:flex-row lg:flex-wrap lg:items-center">

              {/* Recherche */}

              <div className="relative min-w-[260px]">
                <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />

                <Input
                  value={
                    search
                  }
                  onChange={(
                    event,
                  ) =>
                    setSearch(
                      event.target
                        .value,
                    )
                  }
                  placeholder={
                    tab ===
                    "team"
                      ? "Rechercher un collaborateur…"
                      : "Rechercher un dossier…"
                  }
                  className="h-10 rounded-xl pl-9"
                />
              </div>


              {tab ===
                "projects" && (
                <>
                  {/* Consultant */}

                  <select
                    value={
                      projectConsultantFilter
                    }
                    onChange={(
                      event,
                    ) =>
                      setProjectConsultantFilter(
                        event.target
                          .value,
                      )
                    }
                    className="h-10 rounded-xl border border-border bg-background px-3 text-xs outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
                  >
                    <option value="all">
                      Tous les consultants
                    </option>

                    <option value="unassigned">
                      Sans consultant
                    </option>

                    {users
                      .filter(
                        (
                          item,
                        ) =>
                          item.is_active &&
                          item.role !==
                            "superadmin",
                      )
                      .map(
                        (
                          item,
                        ) => (
                          <option
                            key={
                              item.id
                            }
                            value={
                              String(
                                item.id,
                              )
                            }
                          >
                            {
                              item.full_name
                            }
                          </option>
                        ),
                      )}
                  </select>


                  {/* État */}

                  <select
                    value={
                      projectStateFilter
                    }
                    onChange={(
                      event,
                    ) =>
                      setProjectStateFilter(
                        event.target
                          .value,
                      )
                    }
                    className="h-10 rounded-xl border border-border bg-background px-3 text-xs outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
                  >
                    <option value="all">
                      Tous les états
                    </option>

                    <option value="attention">
                      À surveiller
                    </option>

                    <option value="collecte">
                      Collecte
                    </option>

                    <option value="diagnostic">
                      Diagnostic
                    </option>

                    <option value="scholar">
                      EnnoScholar
                    </option>
                  </select>


                  {/* Priorité */}

                  <select
                    value={
                      projectPriorityFilter
                    }
                    onChange={(
                      event,
                    ) =>
                      setProjectPriorityFilter(
                        event.target
                          .value,
                      )
                    }
                    className="h-10 rounded-xl border border-border bg-background px-3 text-xs outline-none transition focus:border-brand/35 focus:ring-2 focus:ring-brand/10"
                  >
                    <option value="all">
                      Toutes les priorités
                    </option>

                    <option value="urgente">
                      Urgente
                    </option>

                    <option value="haute">
                      Haute
                    </option>

                    <option value="normale">
                      Normale
                    </option>

                    <option value="basse">
                      Basse
                    </option>
                  </select>
                </>
              )}


              {tab ===
                "team" && (
                <Button
                  onClick={() =>
                    setShowCreate(
                      (
                        current,
                      ) =>
                        !current,
                    )
                  }
                  className="h-10 rounded-xl"
                >
                  <Plus className="size-4" />
                  Nouveau compte
                </Button>
              )}
            </div>
          </div>
        </CardHeader>


        <CardContent className="p-0">

          {/* ============================================================ */}
          {/* Équipe                                                       */}
          {/* ============================================================ */}

          {tab ===
          "team" ? (
            <>

              {showCreate && (
                <form
                  onSubmit={
                    createUser
                  }
                  className="grid gap-4 border-b bg-muted/30 p-5 md:grid-cols-3"
                >
                  <div className="space-y-2">
                    <Label>
                      Nom complet
                    </Label>

                    <Input
                      value={
                        createForm.full_name
                      }
                      onChange={(
                        event,
                      ) =>
                        setCreateForm({
                          ...createForm,
                          full_name:
                            event.target
                              .value,
                        })
                      }
                      required
                    />
                  </div>


                  <div className="space-y-2">
                    <Label>
                      E-mail
                    </Label>

                    <Input
                      type="email"
                      value={
                        createForm.email
                      }
                      onChange={(
                        event,
                      ) =>
                        setCreateForm({
                          ...createForm,
                          email:
                            event.target
                              .value,
                        })
                      }
                      required
                    />
                  </div>


                  <div className="space-y-2">
                    <Label>
                      Mot de passe temporaire
                    </Label>

                    <Input
                      type="password"
                      minLength={
                        8
                      }
                      value={
                        createForm.password
                      }
                      onChange={(
                        event,
                      ) =>
                        setCreateForm({
                          ...createForm,
                          password:
                            event.target
                              .value,
                        })
                      }
                      required
                    />
                  </div>


                  <div className="space-y-2">
                    <Label>
                      Entreprise
                    </Label>

                    <Input
                      value={
                        createForm.company
                      }
                      onChange={(
                        event,
                      ) =>
                        setCreateForm({
                          ...createForm,
                          company:
                            event.target
                              .value,
                        })
                      }
                    />
                  </div>


                  <div className="space-y-2">
                    <Label>
                      Fonction
                    </Label>

                    <Input
                      value={
                        createForm.job_title
                      }
                      onChange={(
                        event,
                      ) =>
                        setCreateForm({
                          ...createForm,
                          job_title:
                            event.target
                              .value,
                        })
                      }
                    />
                  </div>


                  {user.role ===
                    "superadmin" && (
                    <div className="space-y-2">
                      <Label>
                        Rôle
                      </Label>

                      <select
                        value={
                          createForm.role
                        }
                        onChange={(
                          event,
                        ) =>
                          setCreateForm({
                            ...createForm,
                            role:
                              event.target
                                .value as AdminUser["role"],
                          })
                        }
                        className="h-9 w-full rounded-lg border bg-background px-3 text-sm"
                      >
                        <option value="consultant">
                          Consultant
                        </option>

                        <option value="admin">
                          Administrateur
                        </option>

                        <option value="superadmin">
                          Superadmin
                        </option>
                      </select>
                    </div>
                  )}


                  <div className="flex gap-2 md:col-span-3">
                    <Button
                      type="submit"
                      disabled={
                        busy ===
                        "create"
                      }
                    >
                      {busy ===
                        "create" && (
                        <Loader2 className="size-4 animate-spin" />
                      )}

                      Créer le compte
                    </Button>

                    <Button
                      type="button"
                      variant="outline"
                      onClick={() =>
                        setShowCreate(
                          false,
                        )
                      }
                    >
                      Annuler
                    </Button>
                  </div>
                </form>
              )}


              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm">
                  <thead>
                    <tr className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground">
                      <th className="px-5 py-3">
                        Collaborateur
                      </th>

                      <th className="px-5 py-3">
                        Rôle
                      </th>

                      <th className="px-5 py-3">
                        Projets
                      </th>

                      <th className="px-5 py-3">
                        Statut
                      </th>

                      <th className="px-5 py-3 text-right">
                        Action
                      </th>
                    </tr>
                  </thead>


                  <tbody>
                    {filteredUsers.map(
                      (
                        item,
                      ) => (
                        <tr
                          key={
                            item.id
                          }
                          className="border-b last:border-0 hover:bg-muted/20"
                        >
                          <td className="px-5 py-4">
                            <p className="font-medium">
                              {
                                item.full_name
                              }
                            </p>

                            <p className="text-xs text-muted-foreground">
                              {
                                item.email
                              }

                              {item.company
                                ? ` · ${item.company}`
                                : ""}
                            </p>
                          </td>


                          <td className="px-5 py-4">
                            <Badge variant="secondary">
                              {
                                item.role
                              }
                            </Badge>
                          </td>


                          <td className="px-5 py-4 font-medium">
                            {
                              item.project_count
                            }
                          </td>


                          <td className="px-5 py-4">
                            <span
                              className={`inline-flex items-center gap-1.5 text-xs font-medium ${
                                item.is_active
                                  ? "text-success"
                                  : "text-muted-foreground"
                              }`}
                            >
                              <span
                                className={`size-2 rounded-full ${
                                  item.is_active
                                    ? "bg-success"
                                    : "bg-muted-foreground"
                                }`}
                              />

                              {item.is_active
                                ? "Actif"
                                : "Désactivé"}
                            </span>
                          </td>


                          <td className="px-5 py-4 text-right">
                            <Button
                              size="sm"
                              variant="outline"
                              disabled={
                                busy ===
                                  `user-${item.id}` ||
                                item.id ===
                                  user.id ||
                                (
                                  item.role ===
                                    "superadmin" &&
                                  user.role !==
                                    "superadmin"
                                )
                              }
                              onClick={() =>
                                toggleUser(
                                  item,
                                )
                              }
                            >
                              {item.is_active
                                ? "Désactiver"
                                : "Activer"}
                            </Button>
                          </td>
                        </tr>
                      ),
                    )}
                  </tbody>
                </table>
              </div>
            </>
          ) : (

            /* ========================================================== */
            /* Projets - pilotage automatique                             */
            /* ========================================================== */

            <div className="bg-muted/[0.08] p-4 sm:p-5">

              {filteredProjects.length ===
              0 ? (
                <div className="rounded-2xl border border-dashed border-border bg-card px-6 py-12 text-center">
                  <FolderKanban className="mx-auto size-6 text-muted-foreground" />

                  <p className="mt-3 text-sm font-semibold text-foreground">
                    Aucun dossier trouvé
                  </p>

                  <p className="mt-1 text-xs text-muted-foreground">
                    Modifiez la recherche ou les filtres de pilotage.
                  </p>
                </div>
              ) : (
                <div className="space-y-4">
                  {filteredProjects.map(
                    (
                      project,
                    ) => (
                      <ProjectCard
                        key={
                          project.id
                        }
                        project={
                          project
                        }
                        overview={
                          overviewByProjectId.get(
                            project.id,
                          )
                        }
                        users={
                          users
                        }
                        priorityValue={
                          priorityEdits[
                            project.id
                          ] ||
                          normalizedPriority(
                            project.workflow
                              .priority,
                          )
                        }
                        busy={
                          busy
                        }
                        navigateTo={
                          navigateTo
                        }
                        onAssign={
                          assign
                        }
                        onPriorityChange={(
                          projectId,
                          value,
                        ) =>
                          setPriorityEdits(
                            (
                              current,
                            ) => ({
                              ...current,
                              [projectId]:
                                value,
                            }),
                          )
                        }
                        onSavePriority={
                          savePriority
                        }
                      />
                    ),
                  )}
                </div>
              )}
            </div>
          )}
        </CardContent>
      </Card>


      {/* ================================================================== */}
      {/* Important                                                          */}
      {/* ================================================================== */}

      {tab ===
        "projects" && (
        <div className="flex items-start gap-3 rounded-2xl border border-blue-100 bg-blue-50/55 px-4 py-3">
          <Clock3 className="mt-0.5 size-4 shrink-0 text-blue-600" />

          <p className="text-[11px] leading-5 text-slate-600">
            L’étape affichée est calculée automatiquement à partir des documents,
            diagnostics et recherches EnnoScholar réellement présents. Le pourcentage
            manuel n’est plus utilisé dans cette interface.
          </p>
        </div>
      )}
    </div>
  )
}

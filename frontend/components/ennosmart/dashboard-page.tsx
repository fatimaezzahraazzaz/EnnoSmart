"use client"

import { useEffect, useMemo, useState } from "react"
import {
  ArrowRight,
  BookOpen,
  BrainCircuit,
  CheckCircle2,
  FilePenLine,
  FileText,
  FolderKanban,
  RefreshCw,
  Sparkles,
  Upload,
} from "lucide-react"

import { AppPage } from "@/components/ennosmart/app-shell"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  EmptyState,
  LoadingState,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"
import {
  getProjectOverviews,
  type ProjectOverview,
  type UserRead,
} from "@/lib/api"
import { setCurrentProjectId } from "@/lib/project-session"

type ProjectDashboard = ProjectOverview

interface DashboardPageProps {
  navigateTo: (page: AppPage) => void
  user: UserRead
}

/* -------------------------------------------------------------------------- */
/* Helpers                                                                    */
/* -------------------------------------------------------------------------- */

function firstName(fullName?: string) {
  if (!fullName) return "consultant"
  return fullName.trim().split(/\s+/)[0] || "consultant"
}

function formatScore(value: number | null) {
  if (value === null || value === undefined) return "—"
  const normalized = value <= 1 ? value * 100 : value
  return `${Math.round(normalized)}%`
}

function normalizedScorePercent(value: number | null) {
  if (
    value === null ||
    value === undefined ||
    !Number.isFinite(value)
  ) {
    return null
  }

  const normalized = value <= 1 ? value * 100 : value

  return Math.max(
    0,
    Math.min(100, Math.round(normalized)),
  )
}

function eligibilityScoreTone(percent: number | null) {
  if (percent === null) return "text-slate-400"
  if (percent >= 70) return "text-emerald-700"
  if (percent >= 40) return "text-amber-700"
  return "text-rose-700"
}

function EligibilityScore({
  value,
}: {
  value: number | null
}) {
  const percent = normalizedScorePercent(value)

  return (
    <div
      className="w-full max-w-[104px]"
      aria-label={
        percent === null
          ? "Score d’éligibilité non évalué"
          : `Score d’éligibilité ${percent} sur 100`
      }
    >
      <div className="flex items-baseline justify-between gap-2 lg:justify-center">
        <span className="text-[10px] font-medium text-slate-500 lg:hidden">
          Éligibilité
        </span>

        <span
          className={`text-sm font-bold tabular-nums ${eligibilityScoreTone(
            percent,
          )}`}
        >
          {formatScore(value)}
        </span>
      </div>

      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-slate-100 ring-1 ring-inset ring-slate-200"
        role={percent === null ? undefined : "progressbar"}
        aria-valuemin={percent === null ? undefined : 0}
        aria-valuemax={percent === null ? undefined : 100}
        aria-valuenow={percent === null ? undefined : percent}
      >
        {percent !== null && (
          <span
            className="block h-full rounded-full bg-current text-violet-500 transition-[width] duration-300"
            style={{
              width: `${percent}%`,
            }}
          />
        )}
      </div>
    </div>
  )
}

function relativeTime(value: string) {
  const timestamp = new Date(value).getTime()

  if (!Number.isFinite(timestamp)) return "—"

  const diffMinutes = Math.max(
    0,
    Math.floor(
      (Date.now() - timestamp) / 60000,
    ),
  )

  if (diffMinutes < 1) return "À l’instant"
  if (diffMinutes < 60) return `Il y a ${diffMinutes} min`

  const diffHours = Math.floor(diffMinutes / 60)

  if (diffHours < 24) return `Il y a ${diffHours} h`

  const diffDays = Math.floor(diffHours / 24)

  if (diffDays === 1) return "Hier"
  if (diffDays < 7) return `Il y a ${diffDays} j`

  return new Intl.DateTimeFormat("fr-FR", {
    day: "2-digit",
    month: "short",
  }).format(new Date(value))
}

function statusBadgeClass(status: string) {
  const value = status.toLowerCase()

  if (
    value.includes("terminé") ||
    value.includes("completed")
  ) {
    return "border-emerald-200 bg-emerald-50 text-emerald-700"
  }

  if (
    value.includes("attente") ||
    value.includes("validation")
  ) {
    return "border-violet-200 bg-violet-50 text-violet-700"
  }

  if (
    value.includes("créé") ||
    value.includes("created")
  ) {
    return "border-slate-200 bg-slate-50 text-slate-600"
  }

  return "border-indigo-200 bg-indigo-50 text-indigo-700"
}

function riskFromProject(item: ProjectDashboard) {
  const {
    count,
    pertinent,
    moyen,
  } = item.diagnostic.verrous

  if (count === 0) return "Non évalué"

  if (
    pertinent >= 3 &&
    moyen <= 3
  ) {
    return "Faible"
  }

  return "Moyen"
}

function riskBadgeClass(risk: string) {
  switch (risk) {
    case "Faible":
      return "border-emerald-200 bg-emerald-50 text-emerald-700"

    case "Moyen":
      return "border-violet-200 bg-violet-50 text-violet-700"

    case "Élevé":
      return "border-red-200 bg-red-50 text-red-700"

    default:
      return "border-slate-200 bg-slate-50 text-slate-500"
  }
}

function diagnosticIsCompleted(item: ProjectDashboard) {
  return (
    item.diagnostic.verrous.count > 0 ||
    item.diagnostic.available
  )
}

function scholarIsAvailable(item: ProjectDashboard) {
  return (
    item.scholar.articles.count > 0 ||
    item.scholar.available
  )
}

function getRecentProjects(
  items: ProjectDashboard[],
) {
  return [...items]
    .sort((a, b) => {
      const dateA = new Date(
        a.project.created_at,
      ).getTime()

      const dateB = new Date(
        b.project.created_at,
      ).getTime()

      return dateB - dateA
    })
    .slice(0, 5)
}

/* -------------------------------------------------------------------------- */
/* Hero                                                                       */
/* -------------------------------------------------------------------------- */

function HeaderDecoration() {
  return (
    <div
      className="pointer-events-none absolute inset-y-0 right-0 hidden w-[58%] overflow-hidden lg:block"
      aria-hidden="true"
    >
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_76%_45%,rgba(124,58,237,.11),transparent_35%),radial-gradient(circle_at_96%_10%,rgba(99,102,241,.08),transparent_30%)]" />

      <svg
        className="absolute inset-0 h-full w-full"
        viewBox="0 0 760 150"
        fill="none"
        preserveAspectRatio="none"
      >
        <path
          d="M0 108C111 67 179 122 291 83C416 40 500 102 760 32"
          stroke="rgba(124,58,237,.18)"
          strokeWidth="1.2"
        />

        <path
          d="M31 122C154 82 230 137 352 95C472 53 579 91 760 59"
          stroke="rgba(79,70,229,.14)"
          strokeWidth="1"
        />

        <path
          d="M89 75C201 38 289 81 390 55C505 26 608 50 745 22"
          stroke="rgba(168,85,247,.11)"
          strokeWidth="1"
        />

        {[130, 276, 405, 535, 666].map(
          (x, index) => (
            <circle
              key={x}
              cx={x}
              cy={[79, 96, 58, 73, 44][index]}
              r={index === 2 ? 3.5 : 2.5}
              fill="white"
              stroke="rgba(124,58,237,.55)"
              strokeWidth="1.2"
            />
          ),
        )}
      </svg>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* KPI                                                                        */
/* -------------------------------------------------------------------------- */

const kpiThemes = {
  violet: {
    card:
      "border-violet-200/80 bg-[linear-gradient(135deg,rgba(255,255,255,.98),rgba(247,244,255,.93))]",
    icon:
      "bg-[linear-gradient(145deg,#7c3aed,#6d28d9)] text-white shadow-[0_12px_28px_rgba(109,40,217,.20)]",
    accent: "bg-violet-500",
    value: "text-violet-700",
  },

  blue: {
    card:
      "border-blue-200/80 bg-[linear-gradient(135deg,rgba(255,255,255,.98),rgba(242,247,255,.94))]",
    icon:
      "bg-[linear-gradient(145deg,#3b82f6,#2563eb)] text-white shadow-[0_12px_28px_rgba(37,99,235,.18)]",
    accent: "bg-blue-500",
    value: "text-blue-700",
  },

  indigo: {
    card:
      "border-indigo-200/80 bg-[linear-gradient(135deg,rgba(255,255,255,.98),rgba(244,244,255,.94))]",
    icon:
      "bg-[linear-gradient(145deg,#6366f1,#4f46e5)] text-white shadow-[0_12px_28px_rgba(79,70,229,.20)]",
    accent: "bg-indigo-500",
    value: "text-indigo-700",
  },
}

function KpiCard({
  label,
  value,
  detail,
  icon: Icon,
  theme,
}: {
  label: string
  value: string | number
  detail: string
  icon: typeof FolderKanban
  theme: keyof typeof kpiThemes
}) {
  const colors = kpiThemes[theme]

  return (
    <div
      className={`group relative min-h-[128px] overflow-hidden rounded-[20px] border px-5 py-5 shadow-[0_10px_28px_rgba(40,25,70,.045)] transition-transform duration-200 hover:-translate-y-0.5 ${colors.card}`}
    >
      <div className="flex items-center gap-4">
        <div
          className={`grid size-[58px] shrink-0 place-items-center rounded-[18px] ${colors.icon}`}
        >
          <Icon
            className="size-6"
            strokeWidth={1.7}
            aria-hidden="true"
          />
        </div>

        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-slate-800">
            {label}
          </p>

          <p
            className={`mt-1 text-[29px] font-bold leading-none tracking-[-0.04em] ${colors.value}`}
          >
            {value}
          </p>

          <p className="mt-2 truncate text-xs text-slate-500">
            {detail}
          </p>
        </div>
      </div>

      <div
        className={`absolute inset-x-4 bottom-0 h-[2px] rounded-full opacity-75 ${colors.accent}`}
      />
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Workflow                                                                   */
/* -------------------------------------------------------------------------- */

function WorkflowStep({
  number,
  title,
  detail,
  badge,
  icon: Icon,
  tone,
  connected = true,
}: {
  number: number
  title: string
  detail: string
  badge: string
  icon: typeof Upload
  tone: "violet" | "blue" | "indigo" | "emerald"
  connected?: boolean
}) {
  const styles = {
    violet: {
      circle:
        "border-violet-300 bg-violet-50 text-violet-600",
      badge:
        "bg-violet-50 text-violet-700 ring-violet-100",
      line: "bg-violet-200",
    },

    blue: {
      circle:
        "border-blue-300 bg-blue-50 text-blue-600",
      badge:
        "bg-blue-50 text-blue-700 ring-blue-100",
      line: "bg-blue-200",
    },

    indigo: {
      circle:
        "border-indigo-300 bg-indigo-50 text-indigo-600",
      badge:
        "bg-indigo-50 text-indigo-700 ring-indigo-100",
      line: "bg-indigo-200",
    },

    emerald: {
      circle:
        "border-emerald-300 bg-emerald-50 text-emerald-600",
      badge:
        "bg-emerald-50 text-emerald-700 ring-emerald-100",
      line: "bg-emerald-200",
    },
  }[tone]

  return (
    <div className="relative flex min-w-0 flex-1 items-start gap-3">
      {connected && (
        <span
          className={`absolute left-[53px] right-[-12px] top-[25px] hidden h-px xl:block ${styles.line}`}
          aria-hidden="true"
        />
      )}

      <span
        className={`relative z-10 grid size-[50px] shrink-0 place-items-center rounded-full border-2 bg-white ${styles.circle}`}
      >
        <Icon className="size-5" />
      </span>

      <div className="relative z-10 min-w-0 bg-white pr-3">
        <p className="text-xs font-bold text-slate-800">
          {number}. {title}
        </p>

        <p className="mt-1 text-[11px] leading-4 text-slate-500">
          {detail}
        </p>

        <span
          className={`mt-2 inline-flex rounded-lg px-2 py-1 text-[10px] font-medium ring-1 ring-inset ${styles.badge}`}
        >
          {badge}
        </span>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* Vue portefeuille                                                           */
/* -------------------------------------------------------------------------- */

function PortfolioQuickView({
  total,
  collection,
  diagnostics,
  scholar,
  completedDiagnostics,
}: {
  total: number
  collection: number
  diagnostics: number
  scholar: number
  completedDiagnostics: number
}) {
  const safeTotal = Math.max(total, 1)

  const rows = [
    {
      label: "Collecte",
      count: collection,
      dot: "bg-violet-400",
    },
    {
      label: "Diagnostic",
      count: diagnostics,
      dot: "bg-blue-500",
    },
    {
      label: "EnnoScholar",
      count: scholar,
      dot: "bg-indigo-500",
    },
  ]

  return (
    <section className="overflow-hidden rounded-[22px] border border-slate-200/80 bg-white/95 shadow-[0_10px_34px_rgba(43,30,77,.045)]">
      <div className="border-b border-slate-100 px-5 py-4">
        <h2 className="text-[15px] font-bold text-slate-900">
          Vue rapide du portefeuille
        </h2>

        <p className="mt-1 text-[11px] text-slate-500">
          Où se trouvent actuellement vos dossiers.
        </p>
      </div>

      <div className="p-5">
        <div className="flex items-center gap-5">
          <div className="relative grid size-[112px] shrink-0 place-items-center">
            <div className="absolute inset-0 rounded-full bg-[conic-gradient(#8b5cf6_0_38%,#3b82f6_38%_68%,#6366f1_68%_100%)] opacity-85" />

            <div className="absolute inset-[12px] rounded-full bg-white" />

            <div className="relative text-center">
              <p className="text-2xl font-bold tracking-[-0.04em] text-slate-900">
                {total}
              </p>

              <p className="text-[9px] leading-3 text-slate-500">
                dossiers
                <br />
                actifs
              </p>
            </div>
          </div>

          <div className="min-w-0 flex-1 space-y-3">
            {rows.map((row) => (
              <div
                key={row.label}
                className="flex items-center gap-2"
              >
                <span
                  className={`size-2 shrink-0 rounded-full ${row.dot}`}
                />

                <span className="min-w-0 flex-1 text-xs text-slate-600">
                  {row.label}
                </span>

                <span className="text-xs font-bold text-slate-900">
                  {row.count}
                </span>

                <span className="w-10 text-right text-[10px] text-slate-400">
                  {Math.round(
                    (row.count / safeTotal) * 100,
                  )}
                  %
                </span>
              </div>
            ))}
          </div>
        </div>

        <div className="mt-5 rounded-2xl border border-violet-100 bg-[linear-gradient(135deg,rgba(250,248,255,.95),rgba(246,243,255,.90))] p-4">
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-xl bg-violet-600 text-white shadow-sm">
              <Sparkles className="size-4" />
            </span>

            <div>
              <p className="text-xs font-bold text-violet-800">
                À retenir aujourd’hui
              </p>

              <p className="mt-1 text-[11px] leading-5 text-slate-600">
                {completedDiagnostics > 0
                  ? `${completedDiagnostics} diagnostic(s) sont disponibles dans votre portefeuille.`
                  : "Aucun diagnostic terminé pour le moment."}
              </p>

              <p className="text-[11px] leading-5 text-slate-500">
                Les résultats EnnoScholar restent accessibles depuis chaque dossier concerné.
              </p>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}

/* -------------------------------------------------------------------------- */
/* Dashboard                                                                  */
/* -------------------------------------------------------------------------- */

export default function DashboardPage({
  navigateTo,
  user,
}: DashboardPageProps) {
  const [
    items,
    setItems,
  ] = useState<ProjectDashboard[]>([])

  const [
    loading,
    setLoading,
  ] = useState(true)

  const [
    error,
    setError,
  ] = useState("")

  const [
    lastUpdatedAt,
    setLastUpdatedAt,
  ] = useState<Date | null>(null)

  const loadDashboard = async () => {
    setLoading(true)
    setError("")

    try {
      setItems(
        await getProjectOverviews(),
      )

      setLastUpdatedAt(
        new Date(),
      )
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger le tableau de bord.",
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadDashboard()
  }, [])

  const stats = useMemo(() => {
    const organismes = new Set(
      items.map(
        (item) =>
          item.project.organisme,
      ),
    ).size

    const activeProjects =
      items.length

    const completedDiagnostics =
      items.filter(
        diagnosticIsCompleted,
      ).length

    const scholarProjects =
      items.filter(
        scholarIsAvailable,
      ).length

    const documents =
      items.reduce(
        (sum, item) =>
          sum +
          item.documents.count,
        0,
      )


    const projectsWithDocuments =
      items.filter(
        (item) =>
          item.documents.count > 0,
      ).length

    const collectionProjects =
      items.filter(
        (item) =>
          !diagnosticIsCompleted(item) &&
          !scholarIsAvailable(item),
      ).length

    const diagnosticProjects =
      items.filter(
        (item) =>
          diagnosticIsCompleted(item) &&
          !scholarIsAvailable(item),
      ).length

    return {
      organismes,
      activeProjects,
      completedDiagnostics,
      scholarProjects,
      documents,
      projectsWithDocuments,
      collectionProjects,
      diagnosticProjects,
    }
  }, [items])

  const recentProjects =
    useMemo(
      () =>
        getRecentProjects(items),
      [items],
    )

  const isAdmin =
    user.role === "admin" ||
    user.role === "superadmin"

  const openProject = (
    projectId: number,
    target: AppPage =
      "project-detail",
  ) => {
    setCurrentProjectId(
      projectId,
    )

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
          action={
            <Button
              size="sm"
              variant="outline"
              onClick={
                loadDashboard
              }
            >
              Réessayer
            </Button>
          }
        />
      </div>
    )
  }

  return (
    <div className="mx-auto w-full max-w-[1800px] space-y-4 px-4 py-5 sm:px-6 lg:px-7 lg:py-6">

      {/* ================================================================== */}
      {/* Accueil                                                            */}
      {/* ================================================================== */}

      <section className="relative min-h-[150px] overflow-hidden rounded-[22px] border border-violet-100/80 bg-[linear-gradient(110deg,rgba(255,255,255,.96),rgba(249,247,255,.95),rgba(242,238,255,.80))] px-6 py-6 shadow-[0_10px_32px_rgba(49,35,84,.05)] sm:px-7">
        <HeaderDecoration />

        <div className="relative z-10 flex min-h-[96px] flex-col justify-between gap-5 lg:flex-row lg:items-center">
          <div className="max-w-[650px]">
            <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-violet-600">
              ENNOMA · CIR
            </p>

            <h1 className="mt-2 text-[28px] font-bold tracking-[-0.035em] text-slate-950 sm:text-[31px]">
              Bonjour {firstName(user.full_name)},
            </h1>

            <p className="mt-1 text-sm font-medium text-slate-600">
              Bienvenue dans votre espace de production CIR.
            </p>

            <p className="mt-3 max-w-[600px] text-xs leading-5 text-slate-500">
              Retrouvez vos dossiers, suivez les diagnostics et les résultats EnnoScholar sans multiplier les chemins de navigation.
            </p>
          </div>

          <div className="flex shrink-0 flex-wrap items-center gap-2 lg:self-start">
            {lastUpdatedAt && (
              <div className="hidden rounded-xl border border-white/90 bg-white/75 px-3 py-2 text-[11px] text-slate-500 shadow-sm backdrop-blur-sm xl:block">
                Dernière mise à jour ·{" "}
                {lastUpdatedAt.toLocaleTimeString(
                  "fr-FR",
                  {
                    hour: "2-digit",
                    minute: "2-digit",
                  },
                )}
              </div>
            )}

            <Button
              variant="outline"
              onClick={
                loadDashboard
              }
              className="bg-white/85"
            >
              <RefreshCw className="size-4" />
              Actualiser
            </Button>

            <Button
              onClick={() =>
                navigateTo(
                  isAdmin
                    ? "projects"
                    : "new-project",
                )
              }
              className="bg-[#48218f] shadow-[0_8px_20px_rgba(72,33,143,.20)] hover:bg-[#391a75]"
            >
              <Sparkles className="size-4" />

              {isAdmin
                ? "Voir les dossiers"
                : "Nouveau dossier"}
            </Button>
          </div>
        </div>
      </section>

      {/* ================================================================== */}
      {/* 3 KPI seulement                                                    */}
      {/* ================================================================== */}

      <section className="grid gap-3 md:grid-cols-3">
        <KpiCard
          label={
            isAdmin
              ? "Dossiers actifs"
              : "Mes dossiers actifs"
          }
          value={
            stats.activeProjects
          }
          detail={`${stats.organismes} organisme(s)`}
          icon={FolderKanban}
          theme="violet"
        />

        <KpiCard
          label="Diagnostics disponibles"
          value={
            stats.completedDiagnostics
          }
          detail={`${stats.documents} document(s) indexé(s)`}
          icon={BrainCircuit}
          theme="blue"
        />

        <KpiCard
          label="EnnoScholar"
          value={
            stats.scholarProjects
          }
          detail={`${stats.scholarProjects} projet(s) avec résultats EnnoScholar`}
          icon={BookOpen}
          theme="indigo"
        />
      </section>

      {/* ================================================================== */}
      {/* Parcours informatif - NON CLIQUABLE                                */}
      {/* ================================================================== */}

      <section className="rounded-[22px] border border-slate-200/80 bg-white/95 px-5 py-5 shadow-[0_10px_34px_rgba(43,30,77,.045)] sm:px-6">
        <div>
          <h2 className="text-[15px] font-bold text-slate-900">
            Progression du workflow CIR
          </h2>

          <p className="mt-1 text-[11px] text-slate-500">
            Un repère visuel de votre parcours. Cette zone est informative et ne redirige vers aucune page.
          </p>
        </div>

        <div className="mt-5 grid gap-5 sm:grid-cols-2 xl:flex xl:items-start">
          <WorkflowStep
            number={1}
            title="Déposer"
            detail="Constituer les sources du dossier."
            badge={`${stats.projectsWithDocuments} dossier(s) documenté(s)`}
            icon={Upload}
            tone="violet"
          />

          <WorkflowStep
            number={2}
            title="Diagnostiquer"
            detail="Qualifier les verrous CIR."
            badge={`${stats.completedDiagnostics} diagnostic(s) disponible(s)`}
            icon={BrainCircuit}
            tone="blue"
          />

          <WorkflowStep
            number={3}
            title="EnnoScholar"
            detail="Construire les preuves scientifiques."
            badge={`${stats.scholarProjects} dossier(s) avec recherche`}
            icon={BookOpen}
            tone="indigo"
          />

          <WorkflowStep
            number={4}
            title="Améliorer"
            detail="Renforcer et finaliser les livrables."
            badge="Étape de rédaction"
            icon={FilePenLine}
            tone="emerald"
            connected={false}
          />
        </div>
      </section>

      {/* ================================================================== */}
      {/* Contenu principal                                                  */}
      {/* ================================================================== */}

      {items.length === 0 ? (
        <EmptyState
          icon={FolderKanban}
          title="Aucun dossier CIR"
          description="Créez un premier dossier pour déposer les sources et lancer l'analyse."
          action={
            <Button
              onClick={() =>
                navigateTo(
                  "new-project",
                )
              }
            >
              Créer le premier dossier
            </Button>
          }
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.72fr)_minmax(340px,.78fr)]">

          {/* ============================================================ */}
          {/* Dossiers récents                                             */}
          {/* ============================================================ */}

          <section className="overflow-hidden rounded-[22px] border border-slate-200/80 bg-white/95 shadow-[0_10px_34px_rgba(43,30,77,.045)]">
            <div className="flex items-center justify-between gap-4 border-b border-slate-100 px-5 py-4">
              <div>
                <h2 className="text-[15px] font-bold text-slate-900">
                  Dossiers récents
                </h2>

                <p className="mt-1 text-[11px] text-slate-500">
                  Vos derniers dossiers et leurs principaux indicateurs.
                </p>
              </div>

              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  navigateTo(
                    "projects",
                  )
                }
                className="h-9 bg-white"
              >
                Voir tous les dossiers
                <ArrowRight className="size-4" />
              </Button>
            </div>

            <div className="hidden grid-cols-[minmax(250px,1.45fr)_105px_125px_110px_110px_100px_28px] gap-3 border-b border-slate-100 bg-slate-50/55 px-5 py-2.5 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-500 lg:grid">
              <span>Dossier</span>
              <span>Statut</span>
              <span className="text-center">
                Score d’éligibilité
              </span>
              <span className="text-center">
                EnnoScholar
              </span>
              <span className="text-center">
                Risque
              </span>
              <span className="text-center">
                Dernière activité
              </span>
              <span />
            </div>

            <div className="divide-y divide-slate-100">
              {recentProjects.map(
                (item) => {
                  const score =
                    item.diagnostic
                      .eligibility
                      ?.score ??
                    null

                  const risk =
                    riskFromProject(
                      item,
                    )

                  return (
                    <div
                      key={
                        item.project.id
                      }
                      role="button"
                      tabIndex={0}
                      onClick={() =>
                        openProject(
                          item.project.id,
                          "project-detail",
                        )
                      }
                      onKeyDown={(
                        event,
                      ) => {
                        if (
                          event.key ===
                            "Enter" ||
                          event.key ===
                            " "
                        ) {
                          openProject(
                            item.project.id,
                            "project-detail",
                          )
                        }
                      }}
                      className="group cursor-pointer px-5 py-4 transition hover:bg-violet-50/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-violet-500/35"
                    >
                      <div className="grid grid-cols-2 gap-x-4 gap-y-5 lg:grid-cols-[minmax(250px,1.45fr)_105px_125px_110px_110px_100px_28px] lg:items-center lg:gap-3">

                        {/* Dossier */}

                        <div className="col-span-2 min-w-0 lg:col-span-1">
                          <div className="flex items-center gap-3">
                            <div className="grid size-9 shrink-0 place-items-center rounded-xl bg-violet-50 text-violet-600 ring-1 ring-violet-100">
                              <FolderKanban className="size-4" />
                            </div>

                            <div className="min-w-0">
                              <p className="truncate text-sm font-semibold text-slate-900">
                                {
                                  item.project
                                    .organisme
                                }{" "}
                                —{" "}
                                {
                                  item.project
                                    .project_name
                                }{" "}
                                —{" "}
                                {
                                  item.project
                                    .year
                                }
                              </p>

                              <div className="mt-1.5 flex flex-wrap gap-1.5">
                                <Badge
                                  variant="outline"
                                  className="h-5 rounded-md border-slate-200 bg-white px-1.5 text-[9px] font-medium text-slate-500"
                                >
                                  {
                                    item.documents
                                      .count
                                  }{" "}
                                  document(s)
                                </Badge>

                                <Badge
                                  variant="outline"
                                  className="h-5 rounded-md border-slate-200 bg-white px-1.5 text-[9px] font-medium text-slate-500"
                                >
                                  {
                                    item.diagnostic
                                      .verrous
                                      .count
                                  }{" "}
                                  verrou(x)
                                </Badge>
                              </div>
                            </div>
                          </div>
                        </div>

                        {/* Statut */}

                        <div>
                          <p className="mb-1.5 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:hidden">
                            Statut
                          </p>

                          <Badge
                            variant="outline"
                            className={`h-6 rounded-full px-2 text-[10px] font-semibold ${statusBadgeClass(
                              item.project
                                .status,
                            )}`}
                          >
                            {
                              item.project
                                .status
                            }
                          </Badge>
                        </div>

                        {/* Eligibilité */}

                        <div className="flex items-center justify-start lg:justify-center">
                          <EligibilityScore
                            value={score}
                          />
                        </div>

                        {/* EnnoScholar */}

                        <div>
                          <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:hidden">
                            EnnoScholar
                          </p>

                          <div className="lg:text-center">
                            <Badge
                              variant="outline"
                              className={
                                scholarIsAvailable(item)
                                  ? "h-6 rounded-full border-indigo-200 bg-indigo-50 px-2 text-[9px] font-semibold text-indigo-700"
                                  : "h-6 rounded-full border-slate-200 bg-slate-50 px-2 text-[9px] font-medium text-slate-500"
                              }
                            >
                              {scholarIsAvailable(item)
                                ? "Disponible"
                                : "Non lancé"}
                            </Badge>
                          </div>
                        </div>

                        {/* Risque */}

                        <div className="lg:text-center">
                          <p className="mb-1.5 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:hidden">
                            Risque
                          </p>

                          <Badge
                            variant="outline"
                            className={`h-6 rounded-full px-2 text-[9px] font-medium ${riskBadgeClass(
                              risk,
                            )}`}
                          >
                            {risk}
                          </Badge>
                        </div>

                        {/* Date */}

                        <div>
                          <p className="mb-1 text-[9px] font-bold uppercase tracking-[0.08em] text-slate-400 lg:hidden">
                            Dernière activité
                          </p>

                          <p className="text-[10px] text-slate-500 lg:text-center">
                            {relativeTime(
                              item.project
                                .created_at,
                            )}
                          </p>
                        </div>

                        <ArrowRight
                          className="hidden size-4 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-violet-600 lg:block"
                          aria-hidden="true"
                        />
                      </div>
                    </div>
                  )
                },
              )}
            </div>

            <div className="border-t border-slate-100 p-3 text-center">
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  navigateTo(
                    "projects",
                  )
                }
                className="text-xs text-slate-600 hover:text-violet-700"
              >
                Voir tous les dossiers
                <ArrowRight className="size-4" />
              </Button>
            </div>
          </section>

          {/* ============================================================ */}
          {/* Vue rapide - remplace Activité récente                       */}
          {/* ============================================================ */}

          <PortfolioQuickView
            total={
              stats.activeProjects
            }
            collection={
              stats.collectionProjects
            }
            diagnostics={
              stats.diagnosticProjects
            }
            scholar={
              stats.scholarProjects
            }
            completedDiagnostics={
              stats.completedDiagnostics
            }
          />
        </div>
      )}
    </div>
  )
}

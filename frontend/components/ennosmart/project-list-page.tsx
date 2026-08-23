"use client"

import type {
  AppPage,
  NavigateOptions,
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
} from "@/components/ui/card"

import {
  Input,
} from "@/components/ui/input"

import {
  ArrowRight,
  Building2,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Filter,
  FolderKanban,
  PlusCircle,
  RefreshCw,
  Search,
  X,
} from "lucide-react"

import {
  useEffect,
  useMemo,
  useState,
} from "react"

import {
  getProjects,
  type ProjectRead,
} from "@/lib/api"

import {
  setCurrentProjectId,
} from "@/lib/project-session"

import {
  EmptyState,
  LoadingState,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"


/* -------------------------------------------------------------------------- */
/*                                   Types                                    */
/* -------------------------------------------------------------------------- */

interface ProjectListPageProps {
  navigateTo: (
    page: AppPage,
    options?: NavigateOptions,
  ) => void
}


type OrganizationGroup = {
  id: string
  name: string
  projects: ProjectRead[]
}


/* -------------------------------------------------------------------------- */
/*                              Helpers statuts                                */
/* -------------------------------------------------------------------------- */

const completedStatuses = [
  "Validé",
  "EnnoScholar terminé",
]


function isCompletedStatus(status?: string | null) {
  return completedStatuses.includes(
    status || "",
  )
}


function statusColor(status: string) {
  switch (status) {

    case "Validé":
    case "EnnoScholar terminé":
      return "border-success/20 bg-success/10 text-success"

    case "Diagnostic terminé":
    case "Analyse terminée":
    case "À vérifier consultant":
      return "border-brand/20 bg-brand/10 text-brand"

    case "Créé":
      return "border-border bg-muted text-muted-foreground"

    default:
      return "border-warning/20 bg-warning/10 text-warning"
  }
}


/* -------------------------------------------------------------------------- */
/*                       Regroupement par organisme                            */
/* -------------------------------------------------------------------------- */

function groupProjectsByOrganization(
  projects: ProjectRead[],
) {

  const map =
    new Map<
      string,
      OrganizationGroup
    >()


  for (const project of projects) {

    const key =
      project.organisme ||
      "Organisme inconnu"


    if (!map.has(key)) {

      map.set(
        key,
        {
          id: key
            .toLowerCase()
            .replace(/\s+/g, "-"),

          name: key,

          projects: [],
        },
      )
    }


    map
      .get(key)!
      .projects
      .push(project)
  }


  return Array.from(
    map.values(),
  )
}


/* -------------------------------------------------------------------------- */
/*                              Pagination                                     */
/* -------------------------------------------------------------------------- */

function pageNumbers(
  current: number,
  total: number,
) {

  if (total <= 5) {
    return Array.from(
      { length: total },
      (_, index) =>
        index + 1,
    )
  }


  if (current <= 3) {
    return [
      1,
      2,
      3,
      4,
      5,
    ]
  }


  if (current >= total - 2) {
    return [
      total - 4,
      total - 3,
      total - 2,
      total - 1,
      total,
    ]
  }


  return [
    current - 2,
    current - 1,
    current,
    current + 1,
    current + 2,
  ]
}


/* -------------------------------------------------------------------------- */
/*                                  Page                                      */
/* -------------------------------------------------------------------------- */

export default function ProjectListPage({
  navigateTo,
}: ProjectListPageProps) {

  /* ------------------------------------------------------------------------ */
  /* State                                                                    */
  /* ------------------------------------------------------------------------ */

  const [
    search,
    setSearch,
  ] =
    useState("")


  const [
    projects,
    setProjects,
  ] =
    useState<ProjectRead[]>([])


  const [
    expandedOrgs,
    setExpandedOrgs,
  ] =
    useState<string[]>([])


  const [
    loading,
    setLoading,
  ] =
    useState(true)


  const [
    error,
    setError,
  ] =
    useState("")


  const [
    organizationFilter,
    setOrganizationFilter,
  ] =
    useState("all")


  const [
    statusFilter,
    setStatusFilter,
  ] =
    useState("all")


  const [
    yearFilter,
    setYearFilter,
  ] =
    useState("all")


  const [
    page,
    setPage,
  ] =
    useState(1)


  const [
    pageSize,
    setPageSize,
  ] =
    useState(5)


  /* ------------------------------------------------------------------------ */
  /* Chargement                                                                */
  /* ------------------------------------------------------------------------ */

  const loadProjects =
    async () => {

      setLoading(true)
      setError("")


      try {

        const data =
          await getProjects()


        setProjects(data)


        const groups =
          groupProjectsByOrganization(
            data,
          )


        setExpandedOrgs(
          groups.map(
            (organization) =>
              organization.id,
          ),
        )


        setPage(1)

      } catch (err) {

        setError(
          err instanceof Error
            ? err.message
            : "Impossible de charger les projets.",
        )

      } finally {

        setLoading(false)

      }
    }


  useEffect(() => {
    void loadProjects()
  }, [])


  /* ------------------------------------------------------------------------ */
  /* Données globales                                                         */
  /* ------------------------------------------------------------------------ */

  const organizations =
    useMemo(
      () =>
        groupProjectsByOrganization(
          projects,
        ),
      [projects],
    )


  const organizationNames =
    useMemo(
      () =>
        organizations
          .map(
            (organization) =>
              organization.name,
          )
          .sort(
            (a, b) =>
              a.localeCompare(b),
          ),
      [organizations],
    )


  const statuses =
    useMemo(
      () =>
        Array.from(
          new Set(
            projects
              .map(
                (project) =>
                  project.status,
              )
              .filter(Boolean),
          ),
        ).sort(
          (a, b) =>
            a.localeCompare(b),
        ),
      [projects],
    )


  const years =
    useMemo(
      () =>
        Array.from(
          new Set(
            projects.map(
              (project) =>
                String(
                  project.year,
                ),
            ),
          ),
        ).sort(
          (a, b) =>
            Number(b) -
            Number(a),
        ),
      [projects],
    )


  /* ------------------------------------------------------------------------ */
  /* KPIs                                                                     */
  /* ------------------------------------------------------------------------ */

  const completedProjects =
    useMemo(
      () =>
        projects.filter(
          (project) =>
            isCompletedStatus(
              project.status,
            ),
        ).length,
      [projects],
    )


  const activeProjects =
    Math.max(
      0,
      projects.length -
        completedProjects,
    )


  /* ------------------------------------------------------------------------ */
  /* Filtres                                                                  */
  /* ------------------------------------------------------------------------ */

  const filteredProjects =
    useMemo(() => {

      const normalizedSearch =
        search
          .trim()
          .toLowerCase()


      return projects.filter(
        (project) => {

          const matchesSearch =
            !normalizedSearch ||
            project.organisme
              ?.toLowerCase()
              .includes(
                normalizedSearch,
              ) ||
            project.project_name
              ?.toLowerCase()
              .includes(
                normalizedSearch,
              ) ||
            project.domain_label
              ?.toLowerCase()
              .includes(
                normalizedSearch,
              ) ||
            project.status
              ?.toLowerCase()
              .includes(
                normalizedSearch,
              ) ||
            String(
              project.year,
            ).includes(
              normalizedSearch,
            ) ||
            String(
              project.id,
            ).includes(
              normalizedSearch,
            )


          const matchesOrganization =
            organizationFilter ===
              "all" ||
            project.organisme ===
              organizationFilter


          const matchesStatus =
            statusFilter ===
              "all" ||
            project.status ===
              statusFilter


          const matchesYear =
            yearFilter ===
              "all" ||
            String(
              project.year,
            ) === yearFilter


          return (
            matchesSearch &&
            matchesOrganization &&
            matchesStatus &&
            matchesYear
          )
        },
      )

    }, [
      projects,
      search,
      organizationFilter,
      statusFilter,
      yearFilter,
    ])


  /* ------------------------------------------------------------------------ */
  /* Pagination                                                               */
  /* ------------------------------------------------------------------------ */

  const totalPages =
    Math.max(
      1,
      Math.ceil(
        filteredProjects.length /
          pageSize,
      ),
    )


  const currentPage =
    Math.min(
      page,
      totalPages,
    )


  const firstIndex =
    (
      currentPage -
      1
    ) *
    pageSize


  const paginatedProjects =
    filteredProjects.slice(
      firstIndex,
      firstIndex +
        pageSize,
    )


  const paginatedOrganizations =
    useMemo(
      () =>
        groupProjectsByOrganization(
          paginatedProjects,
        ),
      [paginatedProjects],
    )


  const pagination =
    pageNumbers(
      currentPage,
      totalPages,
    )


  const firstDisplayed =
    filteredProjects.length === 0
      ? 0
      : firstIndex + 1


  const lastDisplayed =
    Math.min(
      firstIndex +
        pageSize,
      filteredProjects.length,
    )


  /* ------------------------------------------------------------------------ */
  /* Reset pagination lorsqu'un filtre change                                */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {

    setPage(1)

  }, [
    search,
    organizationFilter,
    statusFilter,
    yearFilter,
    pageSize,
  ])


  /* ------------------------------------------------------------------------ */
  /* Actions                                                                  */
  /* ------------------------------------------------------------------------ */

  const toggleOrg =
    (
      orgId: string,
    ) => {

      setExpandedOrgs(
        (previous) =>
          previous.includes(
            orgId,
          )
            ? previous.filter(
                (id) =>
                  id !== orgId,
              )
            : [
                ...previous,
                orgId,
              ],
      )
    }


  const openProjectDetail =
    (
      projectId: number,
    ) => {

      setCurrentProjectId(
        projectId,
      )

      navigateTo(
        "project-detail",
      )
    }


  const addProjectForOrganization =
    (
      orgName: string,
    ) => {

      navigateTo(
        "new-project",
        {
          newProjectPreset: {
            organisme:
              orgName,

            lockOrganisme:
              true,
          },
        },
      )
    }


  const clearFilters =
    () => {

      setSearch("")
      setOrganizationFilter(
        "all",
      )
      setStatusFilter(
        "all",
      )
      setYearFilter(
        "all",
      )
      setPage(1)
    }


  const hasActiveFilters =
    Boolean(
      search.trim(),
    ) ||
    organizationFilter !==
      "all" ||
    statusFilter !==
      "all" ||
    yearFilter !==
      "all"


  /* ------------------------------------------------------------------------ */
  /* Render                                                                   */
  /* ------------------------------------------------------------------------ */

  return (

    <div className="workspace-page-wide space-y-5 pb-10">


      {/* ================================================================== */}
      {/* Hero                                                               */}
      {/* ================================================================== */}

      <section
        className="
          relative
          overflow-hidden
          rounded-[22px]
          border
          border-brand/15
          bg-card
          shadow-sm
        "
      >

        {/* décor */}

        <div
          aria-hidden="true"
          className="
            pointer-events-none
            absolute
            inset-0
            bg-[radial-gradient(circle_at_85%_20%,rgba(109,70,178,.09),transparent_32%)]
          "
        />


        <FolderKanban
          aria-hidden="true"
          className="
            pointer-events-none
            absolute
            -bottom-7
            right-8
            hidden
            size-32
            rotate-[-5deg]
            text-brand/[0.055]
            sm:block
          "
        />


        <div
          className="
            relative
            flex
            flex-col
            gap-5
            px-5
            py-6
            sm:px-7
            lg:flex-row
            lg:items-center
            lg:justify-between
          "
        >

          <div>

            <p
              className="
                text-[10px]
                font-semibold
                uppercase
                tracking-[0.15em]
                text-brand
              "
            >
              Portefeuille
            </p>


            <h1
              className="
                mt-2
                text-2xl
                font-semibold
                tracking-[-0.03em]
                text-foreground
                sm:text-[28px]
              "
            >
              Projets CIR
            </h1>


            <p
              className="
                mt-2
                text-sm
                text-muted-foreground
              "
            >

              {organizations.length}

              {" organisme"}

              {organizations.length >
              1
                ? "s"
                : ""}

              {" · "}

              {projects.length}

              {" dossier"}

              {projects.length >
              1
                ? "s"
                : ""}

            </p>

          </div>


          <div
            className="
              flex
              flex-wrap
              gap-2
              lg:pr-32
            "
          >

            <Button
              variant="outline"
              className="rounded-xl"
              onClick={
                loadProjects
              }
              disabled={
                loading
              }
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


            <Button
              className="
                rounded-xl
                shadow-[0_8px_20px_rgba(90,50,150,.16)]
              "
              onClick={() =>
                navigateTo(
                  "new-project",
                )
              }
            >

              <PlusCircle className="size-4" />

              Nouveau dossier

            </Button>

          </div>

        </div>

      </section>


      {/* ================================================================== */}
      {/* KPI                                                                */}
      {/* ================================================================== */}

      {!loading &&
        !error &&
        projects.length >
          0 && (

        <section
          className="
            grid
            gap-3
            sm:grid-cols-2
            xl:grid-cols-4
          "
        >

          {/* Organismes */}

          <Card
            className="
              rounded-2xl
              border-border/80
              shadow-sm
            "
          >

            <CardContent
              className="
                flex
                items-center
                justify-between
                gap-4
                p-5
              "
            >

              <div>

                <p
                  className="
                    text-xs
                    font-medium
                    text-muted-foreground
                  "
                >
                  Organismes
                </p>


                <p
                  className="
                    mt-1
                    text-2xl
                    font-semibold
                    tracking-[-0.03em]
                  "
                >

                  {
                    organizations.length
                  }

                </p>


                <p
                  className="
                    mt-1
                    text-[11px]
                    text-muted-foreground
                  "
                >
                  Entreprises clientes
                </p>

              </div>


              <div
                className="
                  grid
                  size-11
                  place-items-center
                  rounded-xl
                  bg-brand/[0.07]
                  text-brand
                "
              >

                <Building2 className="size-5" />

              </div>

            </CardContent>

          </Card>


          {/* Dossiers */}

          <Card
            className="
              rounded-2xl
              border-border/80
              shadow-sm
            "
          >

            <CardContent
              className="
                flex
                items-center
                justify-between
                gap-4
                p-5
              "
            >

              <div>

                <p
                  className="
                    text-xs
                    font-medium
                    text-muted-foreground
                  "
                >
                  Dossiers
                </p>


                <p
                  className="
                    mt-1
                    text-2xl
                    font-semibold
                    tracking-[-0.03em]
                  "
                >

                  {
                    projects.length
                  }

                </p>


                <p
                  className="
                    mt-1
                    text-[11px]
                    text-muted-foreground
                  "
                >
                  Tous dossiers confondus
                </p>

              </div>


              <div
                className="
                  grid
                  size-11
                  place-items-center
                  rounded-xl
                  bg-violet-500/[0.07]
                  text-violet-600
                "
              >

                <FolderKanban className="size-5" />

              </div>

            </CardContent>

          </Card>


          {/* En cours */}

          <Card
            className="
              rounded-2xl
              border-border/80
              shadow-sm
            "
          >

            <CardContent
              className="
                flex
                items-center
                justify-between
                gap-4
                p-5
              "
            >

              <div>

                <p
                  className="
                    text-xs
                    font-medium
                    text-muted-foreground
                  "
                >
                  En cours
                </p>


                <p
                  className="
                    mt-1
                    text-2xl
                    font-semibold
                    tracking-[-0.03em]
                  "
                >

                  {
                    activeProjects
                  }

                </p>


                <p
                  className="
                    mt-1
                    text-[11px]
                    text-muted-foreground
                  "
                >
                  Dossiers actifs
                </p>

              </div>


              <div
                className="
                  grid
                  size-11
                  place-items-center
                  rounded-xl
                  bg-brand/[0.07]
                  text-brand
                "
              >

                <RefreshCw className="size-5" />

              </div>

            </CardContent>

          </Card>


          {/* Terminés */}

          <Card
            className="
              rounded-2xl
              border-border/80
              shadow-sm
            "
          >

            <CardContent
              className="
                flex
                items-center
                justify-between
                gap-4
                p-5
              "
            >

              <div>

                <p
                  className="
                    text-xs
                    font-medium
                    text-muted-foreground
                  "
                >
                  Terminés
                </p>


                <p
                  className="
                    mt-1
                    text-2xl
                    font-semibold
                    tracking-[-0.03em]
                    text-success
                  "
                >

                  {
                    completedProjects
                  }

                </p>


                <p
                  className="
                    mt-1
                    text-[11px]
                    text-muted-foreground
                  "
                >
                  Dossiers finalisés
                </p>

              </div>


              <div
                className="
                  grid
                  size-11
                  place-items-center
                  rounded-xl
                  bg-success/10
                  text-success
                "
              >

                <CheckCircle2 className="size-5" />

              </div>

            </CardContent>

          </Card>

        </section>

      )}


      {/* ================================================================== */}
      {/* Barre recherche / filtres                                          */}
      {/* ================================================================== */}

      {!loading &&
        !error &&
        projects.length >
          0 && (

        <section
          className="
            rounded-2xl
            border
            border-border/80
            bg-card
            p-3
            shadow-sm
          "
        >

          <div
            className="
              grid
              gap-3
              lg:grid-cols-[minmax(280px,1fr)_180px_180px_150px_auto]
            "
          >

            {/* recherche */}

            <div className="relative min-w-0">

              <Search
                className="
                  absolute
                  left-3
                  top-1/2
                  size-4
                  -translate-y-1/2
                  text-muted-foreground
                "
              />


              <Input
                placeholder="Rechercher un organisme, un dossier, une année…"
                value={search}
                onChange={
                  (
                    event,
                  ) =>
                    setSearch(
                      event.target
                        .value,
                    )
                }
                className="
                  h-11
                  rounded-xl
                  pl-10
                "
              />

            </div>


            {/* organisme */}

            <select
              value={
                organizationFilter
              }
              onChange={
                (
                  event,
                ) =>
                  setOrganizationFilter(
                    event.target
                      .value,
                  )
              }
              className="
                h-11
                min-w-0
                rounded-xl
                border
                border-border
                bg-background
                px-3
                text-sm
                outline-none
                transition
                focus:border-brand/40
                focus:ring-2
                focus:ring-brand/10
              "
            >

              <option value="all">
                Tous les organismes
              </option>


              {organizationNames.map(
                (
                  organization,
                ) => (

                  <option
                    key={
                      organization
                    }
                    value={
                      organization
                    }
                  >

                    {
                      organization
                    }

                  </option>

                ),
              )}

            </select>


            {/* statut */}

            <select
              value={
                statusFilter
              }
              onChange={
                (
                  event,
                ) =>
                  setStatusFilter(
                    event.target
                      .value,
                  )
              }
              className="
                h-11
                min-w-0
                rounded-xl
                border
                border-border
                bg-background
                px-3
                text-sm
                outline-none
                transition
                focus:border-brand/40
                focus:ring-2
                focus:ring-brand/10
              "
            >

              <option value="all">
                Tous les statuts
              </option>


              {statuses.map(
                (
                  status,
                ) => (

                  <option
                    key={
                      status
                    }
                    value={
                      status
                    }
                  >

                    {status}

                  </option>

                ),
              )}

            </select>


            {/* année */}

            <select
              value={
                yearFilter
              }
              onChange={
                (
                  event,
                ) =>
                  setYearFilter(
                    event.target
                      .value,
                  )
              }
              className="
                h-11
                min-w-0
                rounded-xl
                border
                border-border
                bg-background
                px-3
                text-sm
                outline-none
                transition
                focus:border-brand/40
                focus:ring-2
                focus:ring-brand/10
              "
            >

              <option value="all">
                Toutes les années
              </option>


              {years.map(
                (
                  year,
                ) => (

                  <option
                    key={
                      year
                    }
                    value={
                      year
                    }
                  >

                    {year}

                  </option>

                ),
              )}

            </select>


            <Button
              variant="outline"
              className="
                h-11
                rounded-xl
              "
              disabled={
                !hasActiveFilters
              }
              onClick={
                clearFilters
              }
            >

              {hasActiveFilters
                ? (
                  <X className="size-4" />
                )
                : (
                  <Filter className="size-4" />
                )
              }

              Effacer

            </Button>

          </div>

        </section>

      )}


      {/* ================================================================== */}
      {/* Chargement / erreurs                                                */}
      {/* ================================================================== */}

      {loading && (

        <LoadingState
          label="Chargement des projets…"
        />

      )}


      {error && (

        <StatusNotice
          state="failed"
          title="Impossible de charger les projets"
          description={error}
          action={

            <Button
              size="sm"
              variant="outline"
              onClick={
                loadProjects
              }
            >

              Réessayer

            </Button>

          }
        />

      )}


      {!loading &&
        !error &&
        projects.length ===
          0 && (

        <EmptyState
          icon={Building2}
          title="Aucun projet"
          description="Créez un dossier pour commencer à centraliser les sources CIR."
          action={

            <Button
              onClick={() =>
                navigateTo(
                  "new-project",
                )
              }
            >

              <PlusCircle className="size-4" />

              Nouveau dossier

            </Button>

          }
        />

      )}


      {/* ================================================================== */}
      {/* Aucun résultat filtré                                               */}
      {/* ================================================================== */}

      {!loading &&
        !error &&
        projects.length >
          0 &&
        filteredProjects.length ===
          0 && (

        <section
          className="
            rounded-2xl
            border
            border-dashed
            border-border
            bg-card
            px-6
            py-14
            text-center
          "
        >

          <div
            className="
              mx-auto
              grid
              size-12
              place-items-center
              rounded-2xl
              bg-muted
              text-muted-foreground
            "
          >

            <Search className="size-5" />

          </div>


          <h2
            className="
              mt-4
              text-base
              font-semibold
              text-foreground
            "
          >
            Aucun dossier trouvé
          </h2>


          <p
            className="
              mx-auto
              mt-1
              max-w-md
              text-sm
              text-muted-foreground
            "
          >
            Modifiez votre recherche ou retirez certains filtres.
          </p>


          <Button
            variant="outline"
            className="mt-5 rounded-xl"
            onClick={
              clearFilters
            }
          >

            <X className="size-4" />

            Effacer les filtres

          </Button>

        </section>

      )}


      {/* ================================================================== */}
      {/* Liste organismes / dossiers                                        */}
      {/* ================================================================== */}

      {!loading &&
        !error &&
        filteredProjects.length >
          0 && (

        <div className="space-y-3">

          {paginatedOrganizations.map(
            (
              organization,
            ) => {

              const expanded =
                expandedOrgs.includes(
                  organization.id,
                )


              return (

                <section
                  key={
                    organization.id
                  }
                  className="
                    overflow-hidden
                    rounded-2xl
                    border
                    border-border/80
                    bg-card
                    shadow-sm
                  "
                >

                  {/* Header organisme */}

                  <div
                    className="
                      flex
                      min-h-[68px]
                      items-center
                      justify-between
                      gap-4
                      border-b
                      border-border/70
                      px-4
                      sm:px-5
                    "
                  >

                    <button
                      type="button"
                      onClick={() =>
                        toggleOrg(
                          organization.id,
                        )
                      }
                      className="
                        flex
                        min-w-0
                        flex-1
                        items-center
                        gap-3
                        text-left
                      "
                    >

                      <div
                        className="
                          grid
                          size-9
                          shrink-0
                          place-items-center
                          rounded-xl
                          bg-brand/[0.07]
                          text-brand
                        "
                      >

                        <Building2 className="size-4" />

                      </div>


                      <div className="min-w-0">

                        <p
                          className="
                            truncate
                            text-sm
                            font-semibold
                            text-foreground
                          "
                        >

                          {
                            organization.name
                          }

                        </p>


                        <p
                          className="
                            mt-0.5
                            text-[11px]
                            text-muted-foreground
                          "
                        >

                          {
                            organization.projects.length
                          }

                          {" dossier"}

                          {
                            organization.projects.length >
                            1
                              ? "s"
                              : ""
                          }

                          {" sur cette page"}

                        </p>

                      </div>

                    </button>


                    <div
                      className="
                        flex
                        shrink-0
                        items-center
                        gap-2
                      "
                    >

                      <Button
                        size="sm"
                        variant="outline"
                        className="
                          hidden
                          rounded-xl
                          border-brand/20
                          text-brand
                          sm:inline-flex
                        "
                        onClick={() =>
                          addProjectForOrganization(
                            organization.name,
                          )
                        }
                      >

                        <PlusCircle className="size-4" />

                        Ajouter un projet

                      </Button>


                      <button
                        type="button"
                        onClick={() =>
                          toggleOrg(
                            organization.id,
                          )
                        }
                        className="
                          grid
                          size-9
                          place-items-center
                          rounded-xl
                          text-muted-foreground
                          transition
                          hover:bg-muted
                          hover:text-foreground
                        "
                        aria-label={
                          expanded
                            ? "Réduire l'organisme"
                            : "Afficher les dossiers"
                        }
                      >

                        <ChevronDown
                          className={`size-4 transition-transform duration-200 ${
                            expanded
                              ? "rotate-180"
                              : ""
                          }`}
                        />

                      </button>

                    </div>

                  </div>


                  {/* Dossiers */}

                  {expanded && (

                    <div className="animate-fadeIn">

                      {/* table header desktop */}

                      <div
                        className="
                          hidden
                          grid-cols-[minmax(220px,1.3fr)_minmax(180px,1fr)_90px_190px_100px_48px]
                          gap-4
                          border-b
                          border-border/70
                          bg-muted/[0.18]
                          px-5
                          py-3
                          text-[10px]
                          font-semibold
                          uppercase
                          tracking-[0.08em]
                          text-muted-foreground
                          lg:grid
                        "
                      >

                        <span>
                          Dossier
                        </span>

                        <span>
                          Activité
                        </span>

                        <span>
                          Année
                        </span>

                        <span>
                          Statut
                        </span>

                        <span>
                          ID
                        </span>

                        <span />

                      </div>


                      {organization.projects.map(
                        (
                          project,
                        ) => (

                          <div
                            key={
                              project.id
                            }
                            role="button"
                            tabIndex={0}
                            onClick={() =>
                              openProjectDetail(
                                project.id,
                              )
                            }
                            onKeyDown={
                              (
                                event,
                              ) => {

                                if (
                                  event.key ===
                                    "Enter" ||
                                  event.key ===
                                    " "
                                ) {

                                  openProjectDetail(
                                    project.id,
                                  )
                                }

                              }
                            }
                            className="
                              group
                              border-b
                              border-border/60
                              px-4
                              py-4
                              transition
                              last:border-b-0
                              hover:bg-brand/[0.022]
                              sm:px-5
                              lg:grid
                              lg:grid-cols-[minmax(220px,1.3fr)_minmax(180px,1fr)_90px_190px_100px_48px]
                              lg:items-center
                              lg:gap-4
                            "
                          >

                            {/* projet */}

                            <div
                              className="
                                flex
                                min-w-0
                                items-center
                                gap-3
                              "
                            >

                              <div
                                className="
                                  grid
                                  size-10
                                  shrink-0
                                  place-items-center
                                  rounded-xl
                                  bg-brand/[0.065]
                                  text-xs
                                  font-semibold
                                  text-brand
                                "
                              >

                                {
                                  project.project_name
                                    ?.slice(
                                      0,
                                      2,
                                    )
                                    .toUpperCase()
                                }

                              </div>


                              <div className="min-w-0">

                                <p
                                  className="
                                    truncate
                                    text-sm
                                    font-semibold
                                    text-foreground
                                  "
                                >

                                  {
                                    project.project_name
                                  }

                                </p>


                                <p
                                  className="
                                    mt-0.5
                                    truncate
                                    text-[11px]
                                    text-muted-foreground
                                  "
                                >

                                  {
                                    project.domain_label ||
                                    "Domaine non renseigné"
                                  }

                                </p>

                              </div>

                            </div>


                            {/* activité / domaine */}

                            <div
                              className="
                                mt-3
                                min-w-0
                                lg:mt-0
                              "
                            >

                              <p
                                className="
                                  text-[10px]
                                  font-medium
                                  uppercase
                                  tracking-[0.08em]
                                  text-muted-foreground
                                  lg:hidden
                                "
                              >
                                Activité
                              </p>


                              <p
                                className="
                                  mt-0.5
                                  truncate
                                  text-xs
                                  text-muted-foreground
                                  lg:mt-0
                                "
                              >

                                {
                                  project.domain_label ||
                                  "Non renseigné"
                                }

                              </p>

                            </div>


                            {/* année */}

                            <div
                              className="
                                mt-3
                                lg:mt-0
                              "
                            >

                              <p
                                className="
                                  text-[10px]
                                  uppercase
                                  tracking-[0.08em]
                                  text-muted-foreground
                                  lg:hidden
                                "
                              >
                                Année
                              </p>


                              <p
                                className="
                                  mt-0.5
                                  text-sm
                                  font-medium
                                  text-foreground
                                  lg:mt-0
                                "
                              >

                                {
                                  project.year
                                }

                              </p>

                            </div>


                            {/* statut */}

                            <div
                              className="
                                mt-3
                                lg:mt-0
                              "
                            >

                              <p
                                className="
                                  mb-1
                                  text-[10px]
                                  uppercase
                                  tracking-[0.08em]
                                  text-muted-foreground
                                  lg:hidden
                                "
                              >
                                Statut
                              </p>


                              <Badge
                                variant="outline"
                                className={`
                                  rounded-full
                                  px-2.5
                                  py-1
                                  text-[10px]
                                  font-medium
                                  ${statusColor(
                                    project.status,
                                  )}
                                `}
                              >

                                {
                                  project.status
                                }

                              </Badge>

                            </div>


                            {/* ID */}

                            <div
                              className="
                                mt-3
                                lg:mt-0
                              "
                            >

                              <p
                                className="
                                  text-[10px]
                                  uppercase
                                  tracking-[0.08em]
                                  text-muted-foreground
                                  lg:hidden
                                "
                              >
                                Dossier ID
                              </p>


                              <p
                                className="
                                  mt-0.5
                                  text-sm
                                  font-medium
                                  text-foreground
                                  lg:mt-0
                                "
                              >

                                #
                                {
                                  project.id
                                }

                              </p>

                            </div>


                            {/* action */}

                            <div
                              className="
                                mt-3
                                flex
                                justify-end
                                lg:mt-0
                              "
                            >

                              <Button
                                size="icon"
                                variant="ghost"
                                className="
                                  size-9
                                  rounded-xl
                                  text-brand
                                  transition
                                  group-hover:bg-brand/[0.07]
                                "
                                onClick={
                                  (
                                    event,
                                  ) => {

                                    event.stopPropagation()

                                    openProjectDetail(
                                      project.id,
                                    )
                                  }
                                }
                              >

                                <ArrowRight className="size-4" />

                                <span className="sr-only">
                                  Ouvrir le dossier
                                </span>

                              </Button>

                            </div>

                          </div>

                        ),
                      )}

                    </div>

                  )}

                </section>

              )
            },
          )}

        </div>

      )}


      {/* ================================================================== */}
      {/* Pagination                                                         */}
      {/* ================================================================== */}

      {!loading &&
        !error &&
        filteredProjects.length >
          0 && (

        <footer
          className="
            flex
            flex-col
            gap-4
            rounded-2xl
            border
            border-border/80
            bg-card
            px-4
            py-3
            shadow-sm
            sm:flex-row
            sm:items-center
            sm:justify-between
          "
        >

          {/* info */}

          <p
            className="
              text-xs
              text-muted-foreground
            "
          >

            Affichage de{" "}

            <span
              className="
                font-medium
                text-foreground
              "
            >
              {
                firstDisplayed
              }
            </span>

            {" à "}

            <span
              className="
                font-medium
                text-foreground
              "
            >
              {
                lastDisplayed
              }
            </span>

            {" sur "}

            <span
              className="
                font-medium
                text-foreground
              "
            >
              {
                filteredProjects.length
              }
            </span>

            {" dossier"}

            {
              filteredProjects.length >
              1
                ? "s"
                : ""
            }

          </p>


          {/* pages */}

          <div
            className="
              flex
              items-center
              justify-center
              gap-1
            "
          >

            <Button
              variant="ghost"
              size="icon"
              className="size-9 rounded-xl"
              disabled={
                currentPage ===
                1
              }
              onClick={() =>
                setPage(
                  Math.max(
                    1,
                    currentPage -
                      1,
                  ),
                )
              }
            >

              <ChevronLeft className="size-4" />

            </Button>


            {pagination.map(
              (
                pageNumber,
              ) => (

                <Button
                  key={
                    pageNumber
                  }
                  variant={
                    pageNumber ===
                    currentPage
                      ? "default"
                      : "ghost"
                  }
                  size="icon"
                  className="
                    size-9
                    rounded-xl
                    text-xs
                  "
                  onClick={() =>
                    setPage(
                      pageNumber,
                    )
                  }
                >

                  {
                    pageNumber
                  }

                </Button>

              ),
            )}


            <Button
              variant="ghost"
              size="icon"
              className="size-9 rounded-xl"
              disabled={
                currentPage ===
                totalPages
              }
              onClick={() =>
                setPage(
                  Math.min(
                    totalPages,
                    currentPage +
                      1,
                  ),
                )
              }
            >

              <ChevronRight className="size-4" />

            </Button>

          </div>


          {/* taille page */}

          <div
            className="
              flex
              items-center
              justify-end
              gap-2
            "
          >

            <span
              className="
                hidden
                text-xs
                text-muted-foreground
                sm:inline
              "
            >
              Par page
            </span>


            <select
              value={
                pageSize
              }
              onChange={
                (
                  event,
                ) =>
                  setPageSize(
                    Number(
                      event.target
                        .value,
                    ),
                  )
              }
              className="
                h-9
                rounded-xl
                border
                border-border
                bg-background
                px-3
                text-xs
                outline-none
              "
            >

              <option value={5}>
                5
              </option>

              <option value={10}>
                10
              </option>

              <option value={20}>
                20
              </option>

            </select>

          </div>

        </footer>

      )}

    </div>
  )
}
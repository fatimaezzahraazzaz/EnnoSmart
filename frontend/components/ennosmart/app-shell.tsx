"use client"

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"

import dynamic from "next/dynamic"

import { cn } from "@/lib/utils"

import {
  Bell,
  BookOpen,
  BrainCircuit,
  Database,
  FilePenLine,
  FolderKanban,
  LayoutDashboard,
  LogOut,
  Menu,
  MoreVertical,
  PanelLeftClose,
  PanelLeftOpen,
  PlusCircle,
  SlidersHorizontal,
  UserRound,
  UsersRound,
} from "lucide-react"

import { Button } from "@/components/ui/button"

import {
  Avatar,
  AvatarFallback,
} from "@/components/ui/avatar"

import { Separator } from "@/components/ui/separator"

import DashboardPage from "@/components/ennosmart/dashboard-page"
import ProjectListPage from "@/components/ennosmart/project-list-page"
import ProjectDetailPage from "@/components/ennosmart/project-detail-page"
import UploadPage from "@/components/ennosmart/upload-page"
import NewProjectPage from "@/components/ennosmart/new-project-page"
import ProfilePage from "@/components/ennosmart/profile-page"
import AdminPage from "@/components/ennosmart/admin-page"
import SystemSettingsPage from "@/components/ennosmart/system-settings-page"
import CirMemoryPage from "@/components/ennosmart/cir-memory-page"

import type { UserRead } from "@/lib/api"

import {
  getProjectAccessNotifications,
  getProjects,
  markProjectAccessSeen,
  respondToProjectAccess,
  type ProjectAccessNotifications,
  type ProjectRead,
} from "@/lib/api"

import {
  CURRENT_PROJECT_CHANGE_EVENT,
  getCurrentProjectId,
  setCurrentProjectId,
} from "@/lib/project-session"


/* -------------------------------------------------------------------------- */
/*                               Agents dynamiques                             */
/* -------------------------------------------------------------------------- */

const agentPageLoading = () => (
  <div className="grid min-h-[60vh] place-items-center text-sm text-muted-foreground">
    Chargement du module IA…
  </div>
)


const DiagnosisPage = dynamic(
  () =>
    import("@/components/ennosmart/diagnosis-page").then(
      (module) => module.DiagnosisPage,
    ),
  {
    ssr: false,
    loading: agentPageLoading,
  },
)


const EnnoScholarPage = dynamic(
  () =>
    import("@/components/ennosmart/ennoscholar-page").then(
      (module) => module.EnnoScholarPage,
    ),
  {
    ssr: false,
    loading: agentPageLoading,
  },
)


const EnnoAmeliorationPage = dynamic(
  () => import("@/components/ennosmart/ennoamelioration-page"),
  {
    ssr: false,
    loading: agentPageLoading,
  },
)


/* -------------------------------------------------------------------------- */
/*                                    Types                                   */
/* -------------------------------------------------------------------------- */

export type AppPage =
  | "dashboard"
  | "projects"
  | "project-detail"
  | "new-project"
  | "upload"
  | "diagnosis"
  | "scholar"
  | "improvement"
  | "profile"
  | "admin"
  | "cir-memory"
  | "system-settings"


export type NewProjectPreset = {
  organisme?: string
  lockOrganisme?: boolean
}


export type NavigateOptions = {
  newProjectPreset?: NewProjectPreset | null
  returnTo?: AppPage | null
}


interface AppShellProps {
  user: UserRead
  onLogout: () => void
  onUserUpdated: (user: UserRead) => void
}


/* -------------------------------------------------------------------------- */
/*                              Navigation principale                          */
/* -------------------------------------------------------------------------- */

const baseNavItems = [
  {
    id: "dashboard" as AppPage,
    label: "Tableau de bord",
    icon: LayoutDashboard,
  },
  {
    id: "projects" as AppPage,
    label: "Projets",
    icon: FolderKanban,
  },
  {
    id: "diagnosis" as AppPage,
    label: "EnnoDiagnostic",
    icon: BrainCircuit,
  },
  {
    id: "scholar" as AppPage,
    label: "EnnoScholar",
    icon: BookOpen,
  },
  {
    id: "improvement" as AppPage,
    label: "EnnoAmelioration",
    icon: FilePenLine,
  },
]


function navigationForRole(role: string) {
  const items = [...baseNavItems]

  if (
    role === "admin" ||
    role === "superadmin"
  ) {
    items.push({
      id: "admin" as AppPage,
      label: "Administration",
      icon: UsersRound,
    })
  }

  if (role === "superadmin") {
    items.push({
      id: "cir-memory" as AppPage,
      label: "CIR Memory",
      icon: Database,
    })

    items.push({
      id: "system-settings" as AppPage,
      label: "Modèles & système",
      icon: SlidersHorizontal,
    })
  }

  return items
}


/* -------------------------------------------------------------------------- */
/*                                   Helpers                                  */
/* -------------------------------------------------------------------------- */

function getInitials(fullName: string) {
  return (
    fullName
      .split(" ")
      .filter(Boolean)
      .slice(0, 2)
      .map((part) =>
        part[0]?.toUpperCase(),
      )
      .join("") || "ES"
  )
}


function roleLabel(role: string) {
  if (role === "superadmin") {
    return "Superadmin"
  }

  if (role === "admin") {
    return "Administrateur"
  }

  return "Consultant CIR"
}


/* -------------------------------------------------------------------------- */
/*                              Titres des pages                               */
/* -------------------------------------------------------------------------- */

const secondaryPageLabels:
  Partial<Record<AppPage, string>> = {
    profile: "Mon profil",
    "project-detail": "Détail du projet",
    upload: "Dépôt de documents",
  }


const pageDescriptions:
  Partial<Record<AppPage, string>> = {
    dashboard:
      "Vue d'ensemble de votre activité CIR",

    projects:
      "Portefeuille des dossiers clients",

    "project-detail":
      "Contexte, livrables et avancement du dossier",

    "new-project":
      "Création d'un nouveau contexte de travail",

    upload:
      "Sources documentaires du dossier actif",

    diagnosis:
      "Qualification des verrous scientifiques et techniques",

    scholar:
      "Recherche, sélection et validation des preuves",

    improvement:
      "Amélioration guidée des livrables CIR",

    admin:
      "Utilisateurs, rôles et supervision",

    "cir-memory":
      "Mémoire contrôlée des connaissances CIR",

    "system-settings":
      "Configuration des modèles et du système",

    profile:
      "Identité, informations et sécurité du compte",
  }


/* -------------------------------------------------------------------------- */
/*                                   AppShell                                  */
/* -------------------------------------------------------------------------- */

export default function AppShell({
  user,
  onLogout,
  onUserUpdated,
}: AppShellProps) {

  const [
    activePage,
    setActivePage,
  ] =
    useState<AppPage>("dashboard")


  const [
    sidebarOpen,
    setSidebarOpen,
  ] =
    useState(false)


  const [
    sidebarCollapsed,
    setSidebarCollapsed,
  ] =
    useState(false)


  const [
    accountMenuOpen,
    setAccountMenuOpen,
  ] =
    useState(false)


  const [
    notificationOpen,
    setNotificationOpen,
  ] = useState(false)


  const [
    accessNotifications,
    setAccessNotifications,
  ] = useState<ProjectAccessNotifications>({ unread_count: 0, items: [] })


  const [
    notificationBusyId,
    setNotificationBusyId,
  ] = useState<number | null>(null)


  const [
    notificationError,
    setNotificationError,
  ] = useState("")


  const [
    scholarImmersive,
    setScholarImmersive,
  ] =
    useState(false)


  const [
    currentProject,
    setCurrentProject,
  ] =
    useState<ProjectRead | null>(null)


  const [
    newProjectPreset,
    setNewProjectPreset,
  ] =
    useState<NewProjectPreset | null>(null)


  const [
    newProjectReturnTo,
    setNewProjectReturnTo,
  ] =
    useState<AppPage | null>(null)


  const workspaceImmersive =
    activePage === "improvement" ||
    scholarImmersive


  const navItems =
    useMemo(
      () =>
        navigationForRole(user.role),
      [user.role],
    )


  const refreshAccessNotifications = useCallback(async () => {
    try {
      setAccessNotifications(await getProjectAccessNotifications())
    } catch {
      // La navigation principale ne doit jamais être bloquée par les notifications.
    }
  }, [])


  useEffect(() => {
    void refreshAccessNotifications()
    const timer = window.setInterval(() => void refreshAccessNotifications(), 30_000)
    return () => window.clearInterval(timer)
  }, [refreshAccessNotifications])


  const toggleNotifications = async () => {
    const willOpen = !notificationOpen
    setNotificationOpen(willOpen)
    setNotificationError("")
    setAccountMenuOpen(false)
    if (!willOpen) return
    const unread = accessNotifications.items.filter((item) => item.unread)
    if (unread.length) {
      await Promise.allSettled(unread.map((item) => markProjectAccessSeen(item.id)))
    }
    await refreshAccessNotifications()
  }


  const respondToNotification = async (requestId: number, decision: "accepted" | "refused") => {
    setNotificationBusyId(requestId)
    setNotificationError("")
    try {
      await respondToProjectAccess(requestId, decision)
      await refreshAccessNotifications()
    } catch (error) {
      setNotificationError(error instanceof Error ? error.message : "Impossible d’enregistrer la réponse.")
    } finally {
      setNotificationBusyId(null)
    }
  }


  /* ------------------------------------------------------------------------ */
  /*                             Groupes sidebar                              */
  /* ------------------------------------------------------------------------ */

  const navGroups =
    useMemo(() => {

      const select =
        (ids: AppPage[]) =>
          navItems.filter(
            (item) =>
              ids.includes(item.id),
          )


      if (user.role === "superadmin") {

        return [
          {
            label: "Pilotage",
            items: select([
              "dashboard",
              "projects",
              "admin",
            ]),
          },
          {
            label: "Production",
            items: select([
              "diagnosis",
              "scholar",
              "improvement",
            ]),
          },
          {
            label: "Ressources",
            items: select([
              "cir-memory",
              "system-settings",
            ]),
          },
        ]
      }


      if (user.role === "admin") {

        return [
          {
            label: "Pilotage",
            items: select([
              "dashboard",
              "projects",
              "admin",
            ]),
          },
          {
            label: "Production",
            items: select([
              "diagnosis",
              "scholar",
              "improvement",
            ]),
          },
        ]
      }


      return [
        {
          label: "Mes dossiers",
          items: select([
            "dashboard",
            "projects",
          ]),
        },
        {
          label: "Workflow IA",
          items: select([
            "diagnosis",
            "scholar",
            "improvement",
          ]),
        },
      ]

    }, [
      navItems,
      user.role,
    ])


  /* ------------------------------------------------------------------------ */
  /*                         Action principale sidebar                        */
  /* ------------------------------------------------------------------------ */

  const primarySidebarAction =
    user.role === "consultant"
      ? {
          label: "Nouveau dossier",
          page: "new-project" as AppPage,
          icon: PlusCircle,
        }
      : user.role === "admin"
        ? {
            label: "Piloter l'équipe",
            page: "admin" as AppPage,
            icon: UsersRound,
          }
        : {
            label:
              "Configurer la plateforme",
            page:
              "system-settings" as AppPage,
            icon: SlidersHorizontal,
          }


  /* ------------------------------------------------------------------------ */
  /*                            Projet actuellement actif                     */
  /* ------------------------------------------------------------------------ */

  useEffect(() => {

    let active = true


    const refreshProject =
      async () => {

        const projectId =
          getCurrentProjectId()


        if (!projectId) {

          if (active) {
            setCurrentProject(null)
          }

          return
        }


        try {

          const projects =
            await getProjects()


          if (active) {

            setCurrentProject(
              projects.find(
                (project) =>
                  project.id === projectId,
              ) ?? null,
            )
          }

        } catch {

          if (active) {
            setCurrentProject(null)
          }

        }

      }


    void refreshProject()


    window.addEventListener(
      CURRENT_PROJECT_CHANGE_EVENT,
      refreshProject,
    )


    return () => {

      active = false

      window.removeEventListener(
        CURRENT_PROJECT_CHANGE_EVENT,
        refreshProject,
      )

    }

  }, [])


  /* ------------------------------------------------------------------------ */
  /*                             Mode immersif agents                         */
  /* ------------------------------------------------------------------------ */

  const handleScholarImmersiveMode =
    useCallback(
      (
        immersive: boolean,
      ) => {

        setScholarImmersive(
          immersive,
        )

        if (immersive) {
          setSidebarCollapsed(true)
        }

      },
      [],
    )


  /* ------------------------------------------------------------------------ */
  /*                                Navigation                                */
  /* ------------------------------------------------------------------------ */

  const navigateTo =
    (
      page: AppPage,
      options?: NavigateOptions,
    ) => {

      if (
        page === "new-project"
      ) {

        setNewProjectPreset(
          options?.newProjectPreset ??
            null,
        )

        setNewProjectReturnTo(
          options?.returnTo ??
            null,
        )

      } else {

        setNewProjectPreset(null)
        setNewProjectReturnTo(null)

      }


      setActivePage(page)
      setSidebarOpen(false)
      setAccountMenuOpen(false)
      setNotificationOpen(false)
    }


  /* ------------------------------------------------------------------------ */
  /*                               Rendu pages                                */
  /* ------------------------------------------------------------------------ */

  const renderPage = () => {

    switch (activePage) {

      case "dashboard":

        return (
          <DashboardPage
            navigateTo={navigateTo}
            user={user}
          />
        )


      case "projects":

        return (
          <ProjectListPage
            navigateTo={navigateTo}
          />
        )


      case "project-detail":

        return (
          <ProjectDetailPage
            navigateTo={navigateTo}
          />
        )


      case "new-project":

        return (
          <NewProjectPage
            navigateTo={navigateTo}
            preset={
              newProjectPreset
            }
            returnTo={
              newProjectReturnTo
            }
          />
        )


      case "upload":

        return (
          <UploadPage
            navigateTo={navigateTo}
          />
        )


      case "diagnosis":

        return (
          <DiagnosisPage
            onOpenScholar={() =>
              navigateTo("scholar")
            }
          />
        )


      case "scholar":

        return (
          <EnnoScholarPage
            onImmersiveModeChange={
              handleScholarImmersiveMode
            }
          />
        )


      case "improvement":

        return (
          <EnnoAmeliorationPage
            onImmersiveModeChange={
              handleScholarImmersiveMode
            }
            onCreateProject={() =>
              navigateTo(
                "new-project",
                {
                  returnTo:
                    "improvement",
                },
              )
            }
          />
        )


      case "profile":

        return (
          <ProfilePage
            user={user}
            onUserUpdated={
              onUserUpdated
            }
          />
        )


      case "admin":

        return (
          user.role === "admin" ||
          user.role === "superadmin"
        )
          ? (
            <AdminPage
              user={user}
            />
          )
          : (
            <DashboardPage
              navigateTo={
                navigateTo
              }
              user={user}
            />
          )


      case "cir-memory":

        return (
          user.role === "superadmin"
        )
          ? (
            <CirMemoryPage />
          )
          : (
            <DashboardPage
              navigateTo={
                navigateTo
              }
              user={user}
            />
          )


      case "system-settings":

        return (
          user.role === "superadmin"
        )
          ? (
            <SystemSettingsPage />
          )
          : (
            <DashboardPage
              navigateTo={
                navigateTo
              }
              user={user}
            />
          )


      default:

        return (
          <DashboardPage
            navigateTo={navigateTo}
            user={user}
          />
        )
    }
  }


  const initials =
    getInitials(
      user.full_name,
    )


  /* ------------------------------------------------------------------------ */
  /*                              Sidebar content                             */
  /* ------------------------------------------------------------------------ */

  const SidebarContent = ({
    collapsed = false,
  }: {
    collapsed?: boolean
  }) => {

    const PrimaryActionIcon =
      primarySidebarAction.icon


    return (

      <div className="relative flex h-full flex-col">

        {/* ---------------------------------------------------------------- */}
        {/* Branding                                                         */}
        {/* ---------------------------------------------------------------- */}

        <div
          className={cn(
            "relative flex min-h-[88px] items-center border-b border-sidebar-border/70",
            collapsed
              ? "justify-center px-2"
              : "px-5",
          )}
        >

          <button
            type="button"
            onClick={() =>
              navigateTo("dashboard")
            }
            className={cn(
              "group flex min-w-0 items-center rounded-2xl transition-all duration-200 hover:bg-brand/[0.04]",
              collapsed
                ? "justify-center p-2"
                : "w-full gap-3 px-2 py-2",
            )}
          >

            <div className="relative shrink-0">

              <img
                src="/ennoma-logo.png"
                alt="Logo Ennoma"
                className="size-11 rounded-[13px] shadow-sm ring-1 ring-border/50"
              />

              <span
                className="
                  absolute
                  -bottom-0.5
                  -right-0.5
                  size-3
                  rounded-full
                  border-2
                  border-sidebar
                  bg-emerald-500
                "
              />

            </div>


            {!collapsed && (

              <div className="min-w-0 text-left">

                <p className="truncate text-base font-bold tracking-[-0.025em] text-foreground">
                  Ennoma
                </p>

                <p className="mt-0.5 truncate text-[11px] font-medium text-muted-foreground">
                  CIR Intelligence
                </p>

              </div>

            )}

          </button>


          {/* Contrôle collapse */}

          <button
            type="button"
            onClick={() =>
              setSidebarCollapsed(
                (current) =>
                  !current,
              )
            }
            className="
              absolute
              -right-3.5
              top-[30px]
              z-20
              hidden
              size-7
              items-center
              justify-center
              rounded-full
              border
              border-border
              bg-background
              text-muted-foreground
              shadow-sm
              transition-all
              duration-200
              hover:border-brand/30
              hover:text-brand
              hover:shadow-md
              xl:inline-flex
            "
            title={
              collapsed
                ? "Ouvrir le menu"
                : "Réduire le menu"
            }
            aria-label={
              collapsed
                ? "Ouvrir le menu"
                : "Réduire le menu"
            }
          >

            {collapsed ? (
              <PanelLeftOpen className="size-3.5" />
            ) : (
              <PanelLeftClose className="size-3.5" />
            )}

          </button>

        </div>


        {/* ---------------------------------------------------------------- */}
        {/* Navigation                                                       */}
        {/* ---------------------------------------------------------------- */}

        <nav className="min-h-0 flex-1 overflow-y-auto px-3 py-5">

          {/* Action principale */}

          <div
            className={cn(
              "mb-6",
              collapsed
                ? "px-0"
                : "px-1",
            )}
          >

            <Button
              size={
                collapsed
                  ? "icon"
                  : "default"
              }
              className={cn(
                "min-h-11 rounded-xl bg-brand text-white shadow-[0_8px_22px_rgba(87,50,150,0.18)] transition-all hover:-translate-y-0.5 hover:bg-brand/90 hover:shadow-[0_12px_28px_rgba(87,50,150,0.22)]",
                collapsed
                  ? "mx-auto flex"
                  : "w-full justify-center gap-2 px-4",
              )}
              title={
                collapsed
                  ? primarySidebarAction.label
                  : undefined
              }
              onClick={() =>
                navigateTo(
                  primarySidebarAction.page,
                )
              }
            >

              <PrimaryActionIcon className="size-4" />

              {!collapsed && (

                <span className="truncate font-semibold">
                  {
                    primarySidebarAction.label
                  }
                </span>

              )}

            </Button>

          </div>


          {/* Sections */}

          <div className="space-y-6">

            {navGroups.map(
              (group) => (

                <section
                  key={group.label}
                >

                  {!collapsed && (

                    <p
                      className="
                        mb-2
                        px-3
                        text-[10px]
                        font-semibold
                        uppercase
                        tracking-[0.15em]
                        text-brand/70
                      "
                    >
                      {group.label}
                    </p>

                  )}


                  <div className="space-y-1">

                    {group.items.map(
                      (item) => {

                        const Icon =
                          item.icon


                        const isActive =
                          activePage ===
                          item.id


                        return (

                          <button
                            key={
                              item.id
                            }
                            type="button"
                            onClick={() =>
                              navigateTo(
                                item.id,
                              )
                            }
                            title={
                              collapsed
                                ? item.label
                                : undefined
                            }
                            aria-current={
                              isActive
                                ? "page"
                                : undefined
                            }
                            className={cn(
                              `
                                group
                                relative
                                flex
                                min-h-11
                                w-full
                                items-center
                                rounded-xl
                                text-sm
                                font-medium
                                transition-all
                                duration-150
                              `,
                              collapsed
                                ? "justify-center px-2"
                                : "gap-3 px-3",
                              isActive
                                ? "bg-brand/[0.09] text-brand"
                                : "text-muted-foreground hover:bg-accent/65 hover:text-foreground",
                            )}
                          >

                            <Icon
                              className={cn(
                                "size-[18px] shrink-0 transition-colors",
                                isActive
                                  ? "text-brand"
                                  : "text-muted-foreground group-hover:text-foreground",
                              )}
                            />


                            {!collapsed && (

                              <span className="min-w-0 flex-1 truncate text-left">
                                {
                                  item.label
                                }
                              </span>

                            )}


                            {!collapsed &&
                              isActive && (

                                <span className="size-1.5 shrink-0 rounded-full bg-brand" />

                              )}

                          </button>

                        )
                      },
                    )}

                  </div>

                </section>

              ),
            )}

          </div>

        </nav>


        {/* ---------------------------------------------------------------- */}
        {/* Footer utilisateur                                               */}
        {/* ---------------------------------------------------------------- */}

        <div className="relative border-t border-sidebar-border/70 bg-sidebar px-3 py-4">

          {/* Menu utilisateur */}

          {!collapsed &&
            accountMenuOpen && (

              <div
                className="
                  absolute
                  bottom-[90px]
                  left-3
                  right-3
                  z-40
                  overflow-hidden
                  rounded-2xl
                  border
                  border-border
                  bg-popover
                  p-1.5
                  shadow-[0_16px_45px_rgba(35,20,55,0.14)]
                "
              >

                <button
                  type="button"
                  onClick={() =>
                    navigateTo(
                      "profile",
                    )
                  }
                  className="
                    flex
                    min-h-10
                    w-full
                    items-center
                    gap-3
                    rounded-xl
                    px-3
                    text-sm
                    font-medium
                    text-foreground
                    transition
                    hover:bg-accent
                  "
                >

                  <UserRound className="size-4 text-muted-foreground" />

                  <span>
                    Mon profil
                  </span>

                </button>


                <Separator className="my-1" />


                <button
                  type="button"
                  onClick={onLogout}
                  className="
                    flex
                    min-h-10
                    w-full
                    items-center
                    gap-3
                    rounded-xl
                    px-3
                    text-sm
                    font-medium
                    text-destructive
                    transition
                    hover:bg-destructive/5
                  "
                >

                  <LogOut className="size-4" />

                  <span>
                    Déconnexion
                  </span>

                </button>

              </div>

            )}


          <div
            className={cn(
              `
                rounded-2xl
                border
                border-border/75
                bg-background
                shadow-[0_8px_24px_rgba(45,30,70,0.045)]
              `,
              collapsed
                ? "p-2"
                : "p-2.5",
            )}
          >

            <div
              className={cn(
                "flex items-center",
                collapsed
                  ? "justify-center"
                  : "gap-2",
              )}
            >

              {/* User */}

              <button
                type="button"
                onClick={() => {

                  if (collapsed) {

                    navigateTo(
                      "profile",
                    )

                    return
                  }

                  setAccountMenuOpen(
                    (current) =>
                      !current,
                  )

                }}
                title={
                  collapsed
                    ? "Mon profil"
                    : undefined
                }
                className={cn(
                  `
                    group
                    flex
                    min-w-0
                    flex-1
                    items-center
                    rounded-xl
                    transition
                    hover:bg-accent/60
                  `,
                  collapsed
                    ? "justify-center p-1"
                    : "gap-2.5 p-1.5",
                )}
              >

                <div className="relative shrink-0">

                  <Avatar className="size-10 ring-1 ring-border">

                    <AvatarFallback className="bg-brand text-xs font-semibold text-brand-foreground">

                      {initials}

                    </AvatarFallback>

                  </Avatar>


                  <span
                    className="
                      absolute
                      bottom-0
                      right-0
                      size-2.5
                      rounded-full
                      border-2
                      border-background
                      bg-emerald-500
                    "
                  />

                </div>


                {!collapsed && (

                  <div className="min-w-0 flex-1 text-left">

                    <p className="truncate text-sm font-semibold text-foreground">
                      {user.full_name}
                    </p>

                    <p className="truncate text-[11px] text-muted-foreground">
                      {
                        roleLabel(
                          user.role,
                        )
                      }
                    </p>

                  </div>

                )}

              </button>


              {!collapsed && (

                <button
                  type="button"
                  onClick={() =>
                    setAccountMenuOpen(
                      (current) =>
                        !current,
                    )
                  }
                  className="
                    grid
                    size-9
                    shrink-0
                    place-items-center
                    rounded-xl
                    text-muted-foreground
                    transition
                    hover:bg-accent
                    hover:text-foreground
                  "
                  aria-label="Menu du compte"
                >

                  <MoreVertical className="size-4" />

                </button>

              )}

            </div>


            {/* Sidebar réduite */}

            {collapsed && (

              <>

                <Separator className="my-2" />


                <button
                  type="button"
                  onClick={onLogout}
                  title="Déconnexion"
                  aria-label="Déconnexion"
                  className="
                    mx-auto
                    grid
                    size-9
                    place-items-center
                    rounded-xl
                    text-muted-foreground
                    transition
                    hover:bg-destructive/5
                    hover:text-destructive
                  "
                >

                  <LogOut className="size-4" />

                </button>

              </>

            )}

          </div>

        </div>

      </div>
    )
  }


  /* ------------------------------------------------------------------------ */
  /*                             Header courant                               */
  /* ------------------------------------------------------------------------ */

  const currentPageTitle =
    navItems.find(
      (item) =>
        item.id === activePage,
    )?.label ??
    secondaryPageLabels[
      activePage
    ] ??
    "Ennoma"


  const projectLabel =
    currentProject
      ? `${currentProject.organisme} / ${currentProject.project_name}${currentProject.subproject_name ? ` / ${currentProject.subproject_name}` : ""}${currentProject.year ? ` / ${currentProject.year}` : ""}`
      : ""


  /* ------------------------------------------------------------------------ */
  /*                                     UI                                   */
  /* ------------------------------------------------------------------------ */

  return (

    <div className="flex h-screen overflow-hidden bg-background">

      {/* Accessibilité */}

      <a
        href="#main-content"
        className="
          fixed
          left-3
          top-3
          z-[100]
          -translate-y-20
          rounded-lg
          bg-primary
          px-4
          py-2
          text-sm
          font-semibold
          text-primary-foreground
          focus:translate-y-0
        "
      >
        Aller au contenu principal
      </a>


      {/* ------------------------------------------------------------------ */}
      {/* Sidebar desktop                                                   */}
      {/* ------------------------------------------------------------------ */}

      <aside
        className={cn(
          `
            relative
            hidden
            shrink-0
            flex-col
            border-r
            border-sidebar-border/75
            bg-sidebar
            shadow-[6px_0_26px_rgba(45,30,70,0.035)]
            transition-[width]
            duration-200
            xl:flex
          `,
          sidebarCollapsed
            ? "w-[76px]"
            : "w-[280px]",
        )}
      >

        {/* léger halo supérieur */}

        <div
          className="
            pointer-events-none
            absolute
            inset-x-0
            top-0
            h-40
            bg-[radial-gradient(circle_at_30%_0%,rgba(109,70,178,.06),transparent_68%)]
          "
          aria-hidden="true"
        />


        <SidebarContent
          collapsed={
            sidebarCollapsed
          }
        />

      </aside>


      {/* ------------------------------------------------------------------ */}
      {/* Sidebar mobile                                                    */}
      {/* ------------------------------------------------------------------ */}

      {sidebarOpen && (

        <div className="fixed inset-0 z-50 xl:hidden">

          <div
            className="absolute inset-0 bg-slate-950/35 backdrop-blur-[2px]"
            onClick={() =>
              setSidebarOpen(false)
            }
          />


          <aside
            className="
              absolute
              bottom-0
              left-0
              top-0
              z-10
              w-[290px]
              border-r
              border-sidebar-border
              bg-sidebar
              shadow-2xl
            "
          >

            <SidebarContent />

          </aside>

        </div>

      )}


      {/* ------------------------------------------------------------------ */}
      {/* Main                                                              */}
      {/* ------------------------------------------------------------------ */}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">


        {/* ---------------------------------------------------------------- */}
        {/* Header                                                          */}
        {/* ---------------------------------------------------------------- */}

        <header
          className={cn(
            `
              relative
              z-40
              flex
              h-[76px]
              shrink-0
              items-center
              gap-4
              border-b
              border-border/75
              bg-background/95
              px-4
              shadow-[0_1px_0_rgba(45,30,70,0.025)]
              backdrop-blur-xl
              sm:px-6
              lg:px-8
            `,
            workspaceImmersive &&
              "lg:hidden",
          )}
        >

          {/* Menu mobile */}

          <Button
            variant="outline"
            size="sm"
            className="size-10 rounded-xl p-0 xl:hidden"
            onClick={() =>
              setSidebarOpen(true)
            }
          >

            <Menu className="size-4" />

            <span className="sr-only">
              Menu
            </span>

          </Button>


          {/* Titre */}

          <div className="min-w-0 flex-1">

            <h1
              className="
                truncate
                text-[19px]
                font-semibold
                tracking-[-0.025em]
                text-foreground
              "
            >

              {currentPageTitle}

            </h1>


            <p
              className="
                mt-0.5
                hidden
                truncate
                text-xs
                text-muted-foreground
                sm:block
              "
            >

              {
                pageDescriptions[
                  activePage
                ]
              }

            </p>

          </div>


          {/* Projet + utilisateur */}

          <div className="relative flex shrink-0 items-center gap-3">

            <button
              type="button"
              onClick={() => void toggleNotifications()}
              className="relative grid size-11 place-items-center rounded-xl border border-border bg-card text-muted-foreground shadow-sm transition hover:border-brand/30 hover:text-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand/30"
              aria-label={`Notifications de projets${accessNotifications.unread_count ? `, ${accessNotifications.unread_count} non lue(s)` : ""}`}
              aria-expanded={notificationOpen}
            >
              <Bell className="size-[18px]" />
              {accessNotifications.unread_count > 0 && (
                <span className="absolute -right-1 -top-1 grid min-h-5 min-w-5 place-items-center rounded-full bg-destructive px-1 text-[10px] font-bold text-destructive-foreground ring-2 ring-background">
                  {accessNotifications.unread_count > 9 ? "9+" : accessNotifications.unread_count}
                </span>
              )}
            </button>

            {notificationOpen && (
              <div className="absolute right-0 top-[52px] z-50 w-[min(92vw,390px)] overflow-hidden rounded-2xl border border-border bg-popover shadow-[0_18px_55px_rgba(35,20,55,0.18)]">
                <div className="border-b border-border px-4 py-3">
                  <p className="text-sm font-semibold text-foreground">Demandes d’accès</p>
                  <p className="mt-0.5 text-xs text-muted-foreground">Partage des projets entre consultants</p>
                </div>

                <div className="max-h-[430px] overflow-y-auto p-2">
                  {notificationError && (
                    <p className="m-2 rounded-lg bg-destructive/5 px-3 py-2 text-xs leading-5 text-destructive" role="alert">
                      {notificationError}
                    </p>
                  )}
                  {accessNotifications.items.length === 0 ? (
                    <div className="px-4 py-8 text-center">
                      <Bell className="mx-auto size-6 text-muted-foreground/50" />
                      <p className="mt-2 text-sm font-medium text-foreground">Aucune notification</p>
                      <p className="mt-1 text-xs text-muted-foreground">Les demandes et réponses apparaîtront ici.</p>
                    </div>
                  ) : accessNotifications.items.map((item) => {
                    const identity = [item.organisme, item.project_name, item.subproject_name, item.year]
                      .filter(Boolean)
                      .join(" · ")
                    const pendingIncoming = item.direction === "incoming" && item.status === "pending"
                    const title = pendingIncoming
                      ? `${item.requester_name} demande l’accès`
                      : item.direction === "outgoing" && item.status === "pending"
                        ? `Demande envoyée à ${item.owner_name}`
                        : item.status === "accepted"
                          ? "Projet déverrouillé"
                          : "Accès refusé"

                    return (
                      <article key={item.id} className="rounded-xl border border-transparent p-3 transition hover:border-border hover:bg-accent/40">
                        <div className="flex items-start gap-3">
                          <span className={`mt-0.5 size-2.5 shrink-0 rounded-full ${
                            item.status === "accepted" ? "bg-emerald-500" :
                              item.status === "refused" ? "bg-destructive" : "bg-amber-500"
                          }`} aria-hidden="true" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-semibold text-foreground">{title}</p>
                            <p className="mt-1 text-xs leading-5 text-muted-foreground">{identity}</p>

                            {pendingIncoming && (
                              <div className="mt-3 flex gap-2">
                                <Button
                                  type="button"
                                  size="sm"
                                  className="min-h-9 rounded-lg"
                                  disabled={notificationBusyId === item.id}
                                  onClick={() => void respondToNotification(item.id, "accepted")}
                                >
                                  Accepter
                                </Button>
                                <Button
                                  type="button"
                                  size="sm"
                                  variant="outline"
                                  className="min-h-9 rounded-lg"
                                  disabled={notificationBusyId === item.id}
                                  onClick={() => void respondToNotification(item.id, "refused")}
                                >
                                  Refuser
                                </Button>
                              </div>
                            )}

                            {item.direction === "outgoing" && item.status === "accepted" && (
                              <Button
                                type="button"
                                size="sm"
                                variant="outline"
                                className="mt-3 min-h-9 rounded-lg"
                                onClick={() => {
                                  setCurrentProjectId(item.project_id)
                                  navigateTo("project-detail")
                                }}
                              >
                                Ouvrir le projet
                              </Button>
                            )}
                          </div>
                        </div>
                      </article>
                    )
                  })}
                </div>
              </div>
            )}

            {currentProject && (

              <div
                className="
                  hidden
                  min-h-10
                  max-w-[420px]
                  items-center
                  rounded-xl
                  border
                  border-border
                  bg-card
                  px-4
                  shadow-sm
                  md:flex
                "
              >

                <p className="truncate text-xs font-medium text-muted-foreground">

                  {projectLabel}

                </p>

              </div>

            )}


          </div>

        </header>


        {/* ---------------------------------------------------------------- */}
        {/* Content                                                         */}
        {/* ---------------------------------------------------------------- */}

        <main
          id="main-content"
          tabIndex={-1}
          className={cn(
            `
              ennoma-workspace
              relative
              min-h-0
              min-w-0
              flex-1
              bg-background
            `,
            workspaceImmersive
              ? "overflow-hidden"
              : "overflow-y-auto",
          )}
        >

          <div
            key={activePage}
            className={cn(
              `
                w-full
                min-w-0
                animate-fadeIn
              `,
              workspaceImmersive
                ? "h-full min-h-0 overflow-hidden"
                : "min-h-full",
            )}
          >

            {renderPage()}

          </div>

        </main>

      </div>

    </div>
  )
}

"use client"

import { useCallback, useState } from "react"
import dynamic from "next/dynamic"
import { cn } from "@/lib/utils"
import {
  LayoutDashboard,
  FolderKanban,
  Upload,
  BrainCircuit,
  BookOpen,
  Settings,
  LogOut,
  ChevronRight,
  Menu,
  Bell,
  PlusCircle,
  PanelLeftClose,
  PanelLeftOpen,
  FilePenLine,
  UsersRound,
  Database,
  SlidersHorizontal,
  UserRound,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import DashboardPage from "@/components/ennosmart/dashboard-page"
import ProjectListPage from "@/components/ennosmart/project-list-page"
import ProjectDetailPage from "@/components/ennosmart/project-detail-page"
import UploadPage from "@/components/ennosmart/upload-page"
import NewProjectPage from "@/components/ennosmart/new-project-page"
import ProfilePage from "@/components/ennosmart/profile-page"
import SettingsPage from "@/components/ennosmart/settings-page"
import AdminPage from "@/components/ennosmart/admin-page"
import SystemSettingsPage from "@/components/ennosmart/system-settings-page"
import CirMemoryPage from "@/components/ennosmart/cir-memory-page"
import type { UserRead } from "@/lib/api"

const agentPageLoading = () => (
  <div className="grid min-h-[60vh] place-items-center text-sm text-muted-foreground">
    Chargement du module IA…
  </div>
)

const DiagnosisPage = dynamic(
  () => import("@/components/ennosmart/diagnosis-page").then((module) => module.DiagnosisPage),
  { ssr: false, loading: agentPageLoading },
)
const EnnoScholarPage = dynamic(
  () => import("@/components/ennosmart/ennoscholar-page").then((module) => module.EnnoScholarPage),
  { ssr: false, loading: agentPageLoading },
)
const EnnoAmeliorationPage = dynamic(
  () => import("@/components/ennosmart/ennoamelioration-page"),
  { ssr: false, loading: agentPageLoading },
)

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
  | "settings"
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

const baseNavItems = [
  { id: "dashboard" as AppPage, label: "Tableau de bord", icon: LayoutDashboard },
  { id: "projects" as AppPage, label: "Projets", icon: FolderKanban },
  { id: "new-project" as AppPage, label: "Nouveau dossier", icon: PlusCircle },
  { id: "upload" as AppPage, label: "Dépôt de documents", icon: Upload },
  { id: "diagnosis" as AppPage, label: "EnnoDiagnostic", icon: BrainCircuit },
  { id: "scholar" as AppPage, label: "EnnoScholar", icon: BookOpen },
  { id: "improvement" as AppPage, label: "EnnoAmelioration", icon: FilePenLine },
]

function navigationForRole(role: string) {
  const items = [...baseNavItems]
  if (role === "admin" || role === "superadmin") {
    items.push({ id: "admin" as AppPage, label: "Administration", icon: UsersRound })
  }
  if (role === "superadmin") {
    items.push({ id: "cir-memory" as AppPage, label: "CIR Memory", icon: Database })
    items.push({ id: "system-settings" as AppPage, label: "Modèles & système", icon: SlidersHorizontal })
  }
  return items
}

function getInitials(fullName: string) {
  return fullName
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "ES"
}

function roleLabel(role: string) {
  if (role === "superadmin") return "Superadmin"
  if (role === "admin") return "Administrateur"
  return "Consultant CIR"
}

const secondaryPageLabels: Partial<Record<AppPage, string>> = {
  profile: "Mon profil",
  settings: "Paramètres",
  "project-detail": "Détail du projet",
}

export default function AppShell({ user, onLogout, onUserUpdated }: AppShellProps) {
  const [activePage, setActivePage] = useState<AppPage>("dashboard")
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [scholarImmersive, setScholarImmersive] = useState(false)
  const [newProjectPreset, setNewProjectPreset] =
    useState<NewProjectPreset | null>(null)
  const [newProjectReturnTo, setNewProjectReturnTo] =
    useState<AppPage | null>(null)
  const workspaceImmersive = activePage === "improvement" || scholarImmersive
  const navItems = navigationForRole(user.role)
  const navGroups = [
    { label: "Espace", items: navItems.filter((item) => ["dashboard", "projects", "new-project", "upload"].includes(item.id)) },
    { label: "Agents IA", items: navItems.filter((item) => ["diagnosis", "scholar", "improvement"].includes(item.id)) },
    { label: "Pilotage", items: navItems.filter((item) => ["admin", "cir-memory", "system-settings"].includes(item.id)) },
  ].filter((group) => group.items.length > 0)

  const handleScholarImmersiveMode = useCallback((immersive: boolean) => {
    setScholarImmersive(immersive)
    if (immersive) setSidebarCollapsed(true)
  }, [])

  const navigateTo = (page: AppPage, options?: NavigateOptions) => {
    if (page === "new-project") {
      setNewProjectPreset(options?.newProjectPreset ?? null)
      setNewProjectReturnTo(options?.returnTo ?? null)
    } else {
      setNewProjectPreset(null)
      setNewProjectReturnTo(null)
    }

    setActivePage(page)
    setSidebarOpen(false)
  }

  const renderPage = () => {
    switch (activePage) {
      case "dashboard":
        return <DashboardPage navigateTo={navigateTo} user={user} />
      case "projects":
        return <ProjectListPage navigateTo={navigateTo} />
      case "project-detail":
        return <ProjectDetailPage navigateTo={navigateTo} />
      case "new-project":
        return (
          <NewProjectPage
            navigateTo={navigateTo}
            preset={newProjectPreset}
            returnTo={newProjectReturnTo}
          />
        )
      case "upload":
        return <UploadPage navigateTo={navigateTo} />
      case "diagnosis":
        return <DiagnosisPage />
      case "scholar":
        return (
          <EnnoScholarPage
            onImmersiveModeChange={handleScholarImmersiveMode}
          />
        )
      case "improvement":
        return (
          <EnnoAmeliorationPage
            onImmersiveModeChange={handleScholarImmersiveMode}
            onCreateProject={() => navigateTo("new-project", { returnTo: "improvement" })}
          />
        )
      case "profile":
        return <ProfilePage user={user} onUserUpdated={onUserUpdated} />
      case "settings":
        return <SettingsPage />
      case "admin":
        return user.role === "admin" || user.role === "superadmin"
          ? <AdminPage user={user} />
          : <DashboardPage navigateTo={navigateTo} user={user} />
      case "cir-memory":
        return user.role === "superadmin"
          ? <CirMemoryPage />
          : <DashboardPage navigateTo={navigateTo} user={user} />
      case "system-settings":
        return user.role === "superadmin"
          ? <SystemSettingsPage />
          : <DashboardPage navigateTo={navigateTo} user={user} />
      default:
        return <DashboardPage navigateTo={navigateTo} user={user} />
    }
  }

  const initials = getInitials(user.full_name)

  const SidebarContent = ({ collapsed = false }: { collapsed?: boolean }) => (
    <div className="flex h-full flex-col">
      <div
        className={cn(
          "flex items-center border-b border-border py-5",
          collapsed ? "justify-center px-2" : "gap-3 px-5",
        )}
      >
        <div className={cn("flex min-w-0 flex-1 items-center", collapsed ? "justify-center" : "gap-3")}>
          <img src="/ennoma-logo.png" alt="Logo Ennoma" className="size-9 flex-shrink-0 rounded-[11px] shadow-sm" />
          {!collapsed && (
            <>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-foreground tracking-tight">
                Ennoma
              </p>
              <p className="text-[10px] text-muted-foreground leading-none mt-0.5">
                Plateforme CIR IA
              </p>
            </div>
            </>
          )}
        </div>
        <button
          type="button"
          onClick={() => setSidebarCollapsed((current) => !current)}
          className="hidden size-8 items-center justify-center rounded-lg text-muted-foreground hover:bg-accent hover:text-foreground lg:inline-flex"
          title={collapsed ? "Ouvrir le menu principal" : "Fermer le menu principal"}
          aria-label={collapsed ? "Ouvrir le menu principal" : "Fermer le menu principal"}
        >
          {collapsed ? (
            <PanelLeftOpen className="size-4" />
          ) : (
            <PanelLeftClose className="size-4" />
          )}
        </button>
      </div>

      <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4">
        {navGroups.map((group) => (
          <div key={group.label} className="space-y-1">
            {!collapsed && <p className="px-3 pb-1 text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground/70">{group.label}</p>}
            {group.items.map((item) => {
              const Icon = item.icon
              const isActive = activePage === item.id
              return (
                <button
                  key={item.id}
                  onClick={() => navigateTo(item.id)}
                  title={collapsed ? item.label : undefined}
                  className={cn(
                    "relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-200",
                    collapsed && "justify-center px-2",
                    isActive
                      ? "bg-primary text-primary-foreground shadow-md shadow-primary/15"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground",
                  )}
                >
                  <Icon className="size-4 flex-shrink-0" />
                  {!collapsed && <><span className="flex-1 text-left">{item.label}</span>{isActive && <ChevronRight className="size-3 opacity-60" />}</>}
                </button>
              )
            })}
          </div>
        ))}
      </nav>

      <Separator />

      <div className="px-3 py-4 space-y-1">
        <button
          onClick={() => navigateTo("profile")}
          title={collapsed ? "Mon profil" : undefined}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
            collapsed && "justify-center px-2",
            activePage === "profile" ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground hover:bg-accent",
          )}
        >
          <UserRound className="size-4" />
          {!collapsed && <span>Mon profil</span>}
        </button>
        <button
          onClick={() => navigateTo("settings")}
          title={collapsed ? "Paramètres" : undefined}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
            collapsed && "justify-center px-2",
            activePage === "settings" ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:text-foreground hover:bg-accent",
          )}
        >
          <Settings className="size-4" />
          {!collapsed && <span>Paramètres</span>}
        </button>
        <button
          onClick={onLogout}
          title={collapsed ? "Déconnexion" : undefined}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-muted-foreground hover:text-destructive hover:bg-destructive/5 transition-all",
            collapsed && "justify-center px-2",
          )}
        >
          <LogOut className="size-4" />
          {!collapsed && <span>Déconnexion</span>}
        </button>
      </div>

      <div className="px-4 py-4 border-t border-border">
        <div className={cn("flex items-center gap-3", collapsed && "justify-center")}>
          <Avatar className="size-8">
            <AvatarFallback className="bg-brand text-brand-foreground text-xs font-semibold">
              {initials}
            </AvatarFallback>
          </Avatar>
          {!collapsed && (
            <div className="flex-1 min-w-0">
              <p className="text-sm font-semibold text-foreground truncate">
                {user.full_name}
              </p>
              <p className="text-xs text-muted-foreground truncate">
                {roleLabel(user.role)}
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  )

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      <aside
        className={cn(
          "hidden lg:flex flex-col border-r border-border bg-card flex-shrink-0 transition-[width] duration-200",
          sidebarCollapsed ? "w-[72px]" : "w-60",
        )}
      >
        <SidebarContent collapsed={sidebarCollapsed} />
      </aside>

      {sidebarOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <div
            className="absolute inset-0 bg-foreground/30 backdrop-blur-sm"
            onClick={() => setSidebarOpen(false)}
          />
          <aside className="absolute left-0 top-0 bottom-0 w-64 bg-card border-r border-border z-10">
            <SidebarContent />
          </aside>
        </div>
      )}

      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        <header
          className={cn(
            "h-16 border-b border-border/70 bg-card/80 backdrop-blur-xl flex items-center px-4 sm:px-6 gap-4 flex-shrink-0",
            workspaceImmersive && "lg:hidden",
          )}
        >
          <Button
            variant="ghost"
            size="sm"
            className="lg:hidden size-8 p-0"
            onClick={() => setSidebarOpen(true)}
          >
            <Menu className="size-4" />
            <span className="sr-only">Menu</span>
          </Button>

          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold text-foreground">
              {navItems.find((n) => n.id === activePage)?.label ?? secondaryPageLabels[activePage] ?? "Ennoma"}
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" className="size-8 p-0 relative">
              <Bell className="size-4 text-muted-foreground" />
              <span className="absolute top-1.5 right-1.5 size-1.5 rounded-full bg-brand" />
              <span className="sr-only">Notifications</span>
            </Button>
            <Avatar className="size-7 lg:hidden">
              <AvatarFallback className="bg-brand text-brand-foreground text-[10px] font-semibold">
                {initials}
              </AvatarFallback>
            </Avatar>
          </div>
        </header>

        <main
          className={cn(
            "ennoma-workspace relative min-h-0 flex-1 bg-background",
            workspaceImmersive ? "overflow-hidden" : "overflow-y-auto",
          )}
        >
          <div
            key={activePage}
            className={cn(
              "animate-fadeIn",
              workspaceImmersive ? "h-full min-h-0 overflow-hidden" : "min-h-full",
            )}
          >
            {renderPage()}
          </div>
        </main>
      </div>
    </div>
  )
}



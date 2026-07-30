"use client"

import { useCallback, useState } from "react"
import { cn } from "@/lib/utils"
import {
  LayoutDashboard,
  FolderKanban,
  Upload,
  BrainCircuit,
  MessageSquareText,
  BookOpen,
  Settings,
  LogOut,
  ChevronRight,
  Menu,
  Bell,
  PlusCircle,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Separator } from "@/components/ui/separator"
import DashboardPage from "@/components/ennosmart/dashboard-page"
import ProjectListPage from "@/components/ennosmart/project-list-page"
import ProjectDetailPage from "@/components/ennosmart/project-detail-page"
import UploadPage from "@/components/ennosmart/upload-page"
import NewProjectPage from "@/components/ennosmart/new-project-page"
import { DiagnosisPage } from "@/components/ennosmart/diagnosis-page"
import { EnnoScholarPage } from "@/components/ennosmart/ennoscholar-page"
import ChatPage from "@/components/ennosmart/chat-page"
import type { UserRead } from "@/lib/api"

export type AppPage =
  | "dashboard"
  | "projects"
  | "project-detail"
  | "new-project"
  | "upload"
  | "diagnosis"
  | "scholar"
  | "chat"

export type NewProjectPreset = {
  organisme?: string
  lockOrganisme?: boolean
}

export type NavigateOptions = {
  newProjectPreset?: NewProjectPreset | null
}

interface AppShellProps {
  user: UserRead
  onLogout: () => void
}

const navItems = [
  { id: "dashboard" as AppPage, label: "Tableau de bord", icon: LayoutDashboard },
  { id: "projects" as AppPage, label: "Projets", icon: FolderKanban },
  { id: "new-project" as AppPage, label: "Nouveau dossier", icon: PlusCircle },
  { id: "upload" as AppPage, label: "Dépôt de documents", icon: Upload },
  { id: "diagnosis" as AppPage, label: "EnnoDiagnostic", icon: BrainCircuit },
  { id: "scholar" as AppPage, label: "EnnoScholar", icon: BookOpen },
  { id: "chat" as AppPage, label: "Assistant RAG", icon: MessageSquareText },
]

function getInitials(fullName: string) {
  return fullName
    .split(" ")
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "ES"
}

export default function AppShell({ user, onLogout }: AppShellProps) {
  const [activePage, setActivePage] = useState<AppPage>("dashboard")
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [scholarImmersive, setScholarImmersive] = useState(false)
  const [newProjectPreset, setNewProjectPreset] =
    useState<NewProjectPreset | null>(null)

  const handleScholarImmersiveMode = useCallback((immersive: boolean) => {
    setScholarImmersive(immersive)
    if (immersive) setSidebarCollapsed(true)
  }, [])

  const navigateTo = (page: AppPage, options?: NavigateOptions) => {
    if (page === "new-project") {
      setNewProjectPreset(options?.newProjectPreset ?? null)
    } else {
      setNewProjectPreset(null)
    }

    setActivePage(page)
    setSidebarOpen(false)
  }

  const renderPage = () => {
    switch (activePage) {
      case "dashboard":
        return <DashboardPage navigateTo={navigateTo} />
      case "projects":
        return <ProjectListPage navigateTo={navigateTo} />
      case "project-detail":
        return <ProjectDetailPage navigateTo={navigateTo} />
      case "new-project":
        return (
          <NewProjectPage
            navigateTo={navigateTo}
            preset={newProjectPreset}
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
      case "chat":
        return <ChatPage />
      default:
        return <DashboardPage navigateTo={navigateTo} />
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
        {!collapsed && (
          <>
            <div className="size-8 rounded-lg bg-brand flex items-center justify-center flex-shrink-0">
              <BrainCircuit className="size-4 text-primary-foreground" />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-bold text-foreground tracking-tight">
                EnnoSmart
              </p>
              <p className="text-[10px] text-muted-foreground leading-none mt-0.5">
                Plateforme CIR IA
              </p>
            </div>
          </>
        )}
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

      <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
        {navItems.map((item) => {
          const Icon = item.icon
          const isActive = activePage === item.id

          return (
            <button
              key={item.id}
              onClick={() => navigateTo(item.id)}
              title={collapsed ? item.label : undefined}
              className={cn(
                "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-all",
                collapsed && "justify-center px-2",
                isActive
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent"
              )}
            >
              <Icon className="size-4 flex-shrink-0" />
              {!collapsed && (
                <>
                  <span className="flex-1 text-left">{item.label}</span>
                  {isActive && <ChevronRight className="size-3 opacity-60" />}
                </>
              )}
            </button>
          )
        })}
      </nav>

      <Separator />

      <div className="px-3 py-4 space-y-1">
        <button
          title={collapsed ? "Paramètres" : undefined}
          className={cn(
            "w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium text-muted-foreground hover:text-foreground hover:bg-accent transition-all",
            collapsed && "justify-center px-2",
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
                {user.role}
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
            "h-14 border-b border-border bg-card flex items-center px-4 gap-4 flex-shrink-0",
            scholarImmersive && "lg:hidden",
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
              {navItems.find((n) => n.id === activePage)?.label ?? "EnnoSmart"}
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
            "flex-1 bg-background",
            scholarImmersive ? "overflow-hidden" : "overflow-y-auto",
          )}
        >
          {renderPage()}
        </main>
      </div>
    </div>
  )
}

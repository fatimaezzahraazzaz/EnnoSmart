"use client"

import { useEffect, useMemo, useState } from "react"
import {
  Activity,
  Building2,
  CheckCircle2,
  FolderKanban,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  UserCheck,
  Users,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  assignAdminProject,
  createAdminUser,
  getAdminOverview,
  getAdminProjects,
  getAdminUsers,
  updateAdminProjectWorkflow,
  updateAdminUser,
  type AdminOverview,
  type AdminProject,
  type AdminUser,
  type UserRead,
} from "@/lib/api"

const stages = [
  ["collecte", "Collecte des documents", 10],
  ["diagnostic", "Diagnostic IA", 25],
  ["validation_verrous", "Validation des verrous", 40],
  ["recherche_scientifique", "Recherche scientifique", 58],
  ["redaction", "Rédaction CIR", 75],
  ["revue_consultant", "Revue consultant", 90],
  ["finalise", "Finalisé", 100],
] as const

const stageLabels = Object.fromEntries(stages.map(([id, label]) => [id, label]))

type WorkflowEdit = {
  stage: string
  progress_percent: number
  priority: string
  due_date: string
  notes: string
}

function StatCard({ label, value, detail, icon: Icon }: { label: string; value: number; detail: string; icon: typeof Users }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-4 p-5">
        <div className="flex size-11 items-center justify-center rounded-xl bg-brand/10 text-brand"><Icon className="size-5" /></div>
        <div><p className="text-2xl font-semibold tracking-tight">{value}</p><p className="text-sm font-medium">{label}</p><p className="text-xs text-muted-foreground">{detail}</p></div>
      </CardContent>
    </Card>
  )
}

export default function AdminPage({ user }: { user: UserRead }) {
  const [tab, setTab] = useState<"team" | "projects">("team")
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [users, setUsers] = useState<AdminUser[]>([])
  const [projects, setProjects] = useState<AdminProject[]>([])
  const [edits, setEdits] = useState<Record<number, WorkflowEdit>>({})
  const [search, setSearch] = useState("")
  const [showCreate, setShowCreate] = useState(false)
  const [createForm, setCreateForm] = useState({ full_name: "", email: "", password: "", company: "", job_title: "Consultant CIR", role: "consultant" as AdminUser["role"] })
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  const load = async () => {
    setLoading(true); setError("")
    try {
      const [summary, team, dossiers] = await Promise.all([getAdminOverview(), getAdminUsers(), getAdminProjects()])
      setOverview(summary); setUsers(team); setProjects(dossiers)
      setEdits(Object.fromEntries(dossiers.map((project) => [project.id, {
        stage: project.workflow.stage,
        progress_percent: project.workflow.progress_percent,
        priority: project.workflow.priority,
        due_date: project.workflow.due_date?.slice(0, 10) || "",
        notes: project.workflow.notes || "",
      }])))
    } catch (err) { setError(err instanceof Error ? err.message : "Administration indisponible.") } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  const filteredUsers = useMemo(() => users.filter((item) => `${item.full_name} ${item.email} ${item.company || ""}`.toLowerCase().includes(search.toLowerCase())), [users, search])
  const filteredProjects = useMemo(() => projects.filter((item) => `${item.organisme} ${item.project_name} ${item.consultant?.full_name || ""}`.toLowerCase().includes(search.toLowerCase())), [projects, search])

  const createUser = async (event: React.FormEvent) => {
    event.preventDefault(); setBusy("create"); setError(""); setMessage("")
    try {
      await createAdminUser({ ...createForm, company: createForm.company || undefined })
      setCreateForm({ full_name: "", email: "", password: "", company: "", job_title: "Consultant CIR", role: "consultant" })
      setShowCreate(false); setMessage("Le compte a été créé et peut se connecter."); await load()
    } catch (err) { setError(err instanceof Error ? err.message : "Création impossible.") } finally { setBusy(null) }
  }

  const toggleUser = async (target: AdminUser) => {
    setBusy(`user-${target.id}`); setError("");
    try { await updateAdminUser(target.id, { is_active: !target.is_active }); setMessage(`Compte ${target.is_active ? "désactivé" : "activé"}.`); await load() } catch (err) { setError(err instanceof Error ? err.message : "Action impossible.") } finally { setBusy(null) }
  }

  const assign = async (projectId: number, consultantId: number) => {
    setBusy(`project-${projectId}`); setError("")
    try { const updated = await assignAdminProject(projectId, consultantId); setProjects((items) => items.map((item) => item.id === projectId ? updated : item)); setMessage("Affectation mise à jour.") } catch (err) { setError(err instanceof Error ? err.message : "Affectation impossible.") } finally { setBusy(null) }
  }

  const updateEdit = (projectId: number, patch: Partial<WorkflowEdit>) => setEdits((current) => ({ ...current, [projectId]: { ...current[projectId], ...patch } }))

  const saveWorkflow = async (projectId: number) => {
    const edit = edits[projectId]; if (!edit) return
    setBusy(`workflow-${projectId}`); setError("")
    try {
      const updated = await updateAdminProjectWorkflow(projectId, { ...edit, due_date: edit.due_date ? new Date(`${edit.due_date}T12:00:00`).toISOString() : null, notes: edit.notes || null })
      setProjects((items) => items.map((item) => item.id === projectId ? updated : item)); setMessage("Étape du projet enregistrée.")
    } catch (err) { setError(err instanceof Error ? err.message : "Mise à jour impossible.") } finally { setBusy(null) }
  }

  if (loading && !overview) return <div className="flex min-h-[60vh] items-center justify-center"><Loader2 className="size-6 animate-spin text-brand" /></div>

  return (
    <div className="mx-auto max-w-7xl space-y-6 p-5 sm:p-7 lg:p-9">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div><div className="mb-2 flex items-center gap-2 text-sm font-medium text-brand"><ShieldCheck className="size-4" />Pilotage administratif</div><h1 className="text-3xl font-semibold tracking-tight">Équipe & portefeuille</h1><p className="mt-2 text-sm text-muted-foreground">Affectez les consultants et suivez chaque dossier jusqu’à sa finalisation.</p></div>
        <Button variant="outline" onClick={load} disabled={loading}><RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />Actualiser</Button>
      </div>

      {message && <div className="flex items-center gap-2 rounded-xl border border-success/25 bg-success/10 p-3 text-sm text-success"><CheckCircle2 className="size-4" />{message}</div>}
      {error && <div className="rounded-xl border border-destructive/25 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Users} label="Consultants" value={overview?.users.consultants || 0} detail={`${overview?.users.active || 0} comptes actifs`} />
        <StatCard icon={FolderKanban} label="Projets" value={overview?.projects.total || 0} detail="toutes années confondues" />
        <StatCard icon={Activity} label="En production" value={(overview?.projects.total || 0) - (overview?.projects.completed || 0)} detail="dossiers non finalisés" />
        <StatCard icon={CheckCircle2} label="Finalisés" value={overview?.projects.completed || 0} detail="workflow terminé" />
      </div>

      <Card>
        <CardHeader className="border-b">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex gap-1 rounded-xl bg-muted p-1">
              <button onClick={() => setTab("team")} className={`rounded-lg px-4 py-2 text-sm font-medium transition ${tab === "team" ? "bg-card shadow-sm" : "text-muted-foreground"}`}><Users className="mr-2 inline size-4" />Consultants</button>
              <button onClick={() => setTab("projects")} className={`rounded-lg px-4 py-2 text-sm font-medium transition ${tab === "projects" ? "bg-card shadow-sm" : "text-muted-foreground"}`}><FolderKanban className="mr-2 inline size-4" />Projets</button>
            </div>
            <div className="flex gap-2"><div className="relative"><Search className="absolute left-3 top-2.5 size-4 text-muted-foreground" /><Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Rechercher…" className="w-full pl-9 md:w-64" /></div>{tab === "team" && <Button onClick={() => setShowCreate((current) => !current)}><Plus className="size-4" />Nouveau compte</Button>}</div>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {tab === "team" ? (
            <>
              {showCreate && (
                <form onSubmit={createUser} className="grid gap-4 border-b bg-muted/30 p-5 md:grid-cols-3">
                  <div className="space-y-2"><Label>Nom complet</Label><Input value={createForm.full_name} onChange={(e) => setCreateForm({ ...createForm, full_name: e.target.value })} required /></div>
                  <div className="space-y-2"><Label>E-mail</Label><Input type="email" value={createForm.email} onChange={(e) => setCreateForm({ ...createForm, email: e.target.value })} required /></div>
                  <div className="space-y-2"><Label>Mot de passe temporaire</Label><Input type="password" minLength={8} value={createForm.password} onChange={(e) => setCreateForm({ ...createForm, password: e.target.value })} required /></div>
                  <div className="space-y-2"><Label>Entreprise</Label><Input value={createForm.company} onChange={(e) => setCreateForm({ ...createForm, company: e.target.value })} /></div>
                  <div className="space-y-2"><Label>Fonction</Label><Input value={createForm.job_title} onChange={(e) => setCreateForm({ ...createForm, job_title: e.target.value })} /></div>
                  {user.role === "superadmin" && <div className="space-y-2"><Label>Rôle</Label><select value={createForm.role} onChange={(e) => setCreateForm({ ...createForm, role: e.target.value as AdminUser["role"] })} className="h-9 w-full rounded-lg border bg-background px-3 text-sm"><option value="consultant">Consultant</option><option value="admin">Administrateur</option><option value="superadmin">Superadmin</option></select></div>}
                  <div className="flex gap-2 md:col-span-3"><Button type="submit" disabled={busy === "create"}>{busy === "create" && <Loader2 className="size-4 animate-spin" />}Créer le compte</Button><Button type="button" variant="outline" onClick={() => setShowCreate(false)}>Annuler</Button></div>
                </form>
              )}
              <div className="overflow-x-auto">
                <table className="w-full min-w-[760px] text-sm"><thead><tr className="border-b bg-muted/30 text-left text-xs uppercase tracking-wide text-muted-foreground"><th className="px-5 py-3">Collaborateur</th><th className="px-5 py-3">Rôle</th><th className="px-5 py-3">Projets</th><th className="px-5 py-3">Statut</th><th className="px-5 py-3 text-right">Action</th></tr></thead><tbody>{filteredUsers.map((item) => <tr key={item.id} className="border-b last:border-0 hover:bg-muted/20"><td className="px-5 py-4"><p className="font-medium">{item.full_name}</p><p className="text-xs text-muted-foreground">{item.email}{item.company ? ` · ${item.company}` : ""}</p></td><td className="px-5 py-4"><Badge variant="secondary">{item.role}</Badge></td><td className="px-5 py-4 font-medium">{item.project_count}</td><td className="px-5 py-4"><span className={`inline-flex items-center gap-1.5 text-xs font-medium ${item.is_active ? "text-success" : "text-muted-foreground"}`}><span className={`size-2 rounded-full ${item.is_active ? "bg-success" : "bg-muted-foreground"}`} />{item.is_active ? "Actif" : "Désactivé"}</span></td><td className="px-5 py-4 text-right"><Button size="sm" variant="outline" disabled={busy === `user-${item.id}` || item.id === user.id || (item.role === "superadmin" && user.role !== "superadmin")} onClick={() => toggleUser(item)}>{item.is_active ? "Désactiver" : "Activer"}</Button></td></tr>)}</tbody></table>
              </div>
            </>
          ) : (
            <div className="divide-y">
              {filteredProjects.map((project) => {
                const edit = edits[project.id]
                return <article key={project.id} className="p-5 transition hover:bg-muted/15"><div className="grid gap-5 xl:grid-cols-[1.15fr_0.85fr_1.25fr_auto] xl:items-center">
                  <div><div className="flex flex-wrap items-center gap-2"><Building2 className="size-4 text-brand" /><h3 className="font-semibold">{project.organisme} · {project.project_name}</h3><Badge variant="outline">{project.year}</Badge></div><p className="mt-2 text-xs text-muted-foreground">{project.counts.documents} documents · {project.counts.diagnostics} diagnostics · {project.counts.scholar_runs} recherches</p></div>
                  <div className="space-y-1.5"><Label className="text-xs text-muted-foreground">Consultant affecté</Label><select value={project.consultant?.id || ""} onChange={(e) => assign(project.id, Number(e.target.value))} className="h-9 w-full rounded-lg border bg-background px-3 text-sm" disabled={busy === `project-${project.id}`}><option value="" disabled>Choisir</option>{users.filter((item) => item.is_active && item.role !== "superadmin").map((item) => <option key={item.id} value={item.id}>{item.full_name}</option>)}</select></div>
                  {edit && <div className="grid gap-2 sm:grid-cols-[1fr_120px]"><select value={edit.stage} onChange={(e) => { const stage = stages.find(([id]) => id === e.target.value); updateEdit(project.id, { stage: e.target.value, progress_percent: stage?.[2] || edit.progress_percent }) }} className="h-9 rounded-lg border bg-background px-3 text-sm">{stages.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select><select value={edit.priority} onChange={(e) => updateEdit(project.id, { priority: e.target.value })} className="h-9 rounded-lg border bg-background px-2 text-sm"><option value="basse">Basse</option><option value="normale">Normale</option><option value="haute">Haute</option><option value="urgente">Urgente</option></select><div className="flex items-center gap-3 sm:col-span-2"><input type="range" min={0} max={100} value={edit.progress_percent} onChange={(e) => updateEdit(project.id, { progress_percent: Number(e.target.value) })} className="h-2 flex-1 accent-violet-700" /><span className="w-10 text-right text-xs font-semibold">{edit.progress_percent}%</span></div></div>}
                  <Button size="sm" onClick={() => saveWorkflow(project.id)} disabled={busy === `workflow-${project.id}`}><UserCheck className="size-4" />Enregistrer</Button>
                </div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-[linear-gradient(90deg,#5b21b6,#9333ea)] transition-all" style={{ width: `${edit?.progress_percent || 0}%` }} /></div><p className="mt-2 text-xs text-muted-foreground">Étape : {stageLabels[edit?.stage || project.workflow.stage] || project.workflow.stage}</p></article>
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

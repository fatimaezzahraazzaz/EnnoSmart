"use client"

import { useEffect, useState } from "react"
import { Building2, Loader2, Mail, Phone, Save, ShieldCheck, UserRound } from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { getAccount, getProjects, updateProfile, type AccountRead, type UserRead } from "@/lib/api"
import { LoadingState, PageHeader, StatusNotice } from "@/components/ennosmart/workspace-ui"

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "EN"
}

function roleLabel(role: string) {
  if (role === "superadmin") return "Super administrateur"
  if (role === "admin") return "Administrateur"
  return "Consultant CIR"
}

export default function ProfilePage({
  user,
  onUserUpdated,
}: {
  user: UserRead
  onUserUpdated: (user: UserRead) => void
}) {
  const [account, setAccount] = useState<AccountRead | null>(null)
  const [projectCount, setProjectCount] = useState(0)
  const [form, setForm] = useState({
    full_name: user.full_name,
    email: user.email,
    job_title: "",
    company: "",
    phone: "",
    bio: "",
  })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    Promise.all([getAccount(), getProjects()])
      .then(([data, projects]) => {
        setAccount(data)
        setProjectCount(projects.length)
        setForm({
          full_name: data.user.full_name,
          email: data.user.email,
          job_title: data.profile.job_title || "",
          company: data.profile.company || "",
          phone: data.profile.phone || "",
          bio: data.profile.bio || "",
        })
      })
      .catch((err) => setError(err instanceof Error ? err.message : "Profil indisponible."))
      .finally(() => setLoading(false))
  }, [])

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setMessage("")
    setError("")
    try {
      const updated = await updateProfile(form)
      setAccount(updated)
      onUserUpdated(updated.user)
      setMessage("Votre profil a été mis à jour.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible d’enregistrer le profil.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return <LoadingState label="Chargement du profil…" />
  }

  return (
    <div className="workspace-page space-y-6">
      <PageHeader eyebrow="Compte" title="Mon profil" description="Gérez les informations utilisées dans votre espace et vos affectations." />
      <section className="overflow-hidden rounded-xl border bg-card shadow-xs">
        <div className="h-10 border-b bg-[linear-gradient(120deg,rgba(107,72,135,.10),rgba(107,72,135,.025)_55%,transparent)]" />
        <div className="flex flex-col gap-5 px-6 pb-6 sm:flex-row sm:items-end">
          <Avatar className="-mt-7 size-20 border-4 border-card shadow-md">
            <AvatarFallback className="bg-brand text-2xl font-semibold text-white">{initials(account?.user.full_name || user.full_name)}</AvatarFallback>
          </Avatar>
          <div className="flex-1 sm:pb-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-2xl font-semibold tracking-tight">{account?.user.full_name}</h1>
              <Badge className="bg-brand/10 text-brand hover:bg-brand/10">{roleLabel(user.role)}</Badge>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">{form.job_title || "Expertise CIR"}{form.company ? ` · ${form.company}` : ""}</p>
          </div>
          <div className="flex gap-6 text-center sm:pb-1">
            <div><p className="text-xl font-semibold">{projectCount}</p><p className="text-xs text-muted-foreground">projets accessibles</p></div>
            <div><p className="text-xl font-semibold text-success">Actif</p><p className="text-xs text-muted-foreground">statut du compte</p></div>
          </div>
        </div>
      </section>

      <div className="grid gap-6 lg:grid-cols-[1fr_300px]">
        <Card>
          <CardHeader>
            <CardTitle>Informations personnelles</CardTitle>
            <CardDescription>Ces informations personnalisent votre espace et les affectations de projet.</CardDescription>
          </CardHeader>
          <CardContent>
            {message && <StatusNotice className="mb-5" state="validated" title={message} />}
            {error && <StatusNotice className="mb-5" state="failed" title="Enregistrement impossible" description={error} />}
            <form onSubmit={save} className="space-y-5">
              <div className="grid gap-5 sm:grid-cols-2">
                <div className="space-y-2"><Label htmlFor="profile-name">Nom complet</Label><div className="relative"><UserRound className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input id="profile-name" className="h-10 pl-9" value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} required /></div></div>
                <div className="space-y-2"><Label htmlFor="profile-email">E-mail</Label><div className="relative"><Mail className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input id="profile-email" type="email" className="h-10 pl-9" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required /></div></div>
                <div className="space-y-2"><Label htmlFor="profile-job">Fonction</Label><Input id="profile-job" value={form.job_title} onChange={(e) => setForm({ ...form, job_title: e.target.value })} placeholder="Consultant CIR senior" /></div>
                <div className="space-y-2"><Label htmlFor="profile-company">Entreprise</Label><div className="relative"><Building2 className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input id="profile-company" className="pl-9" value={form.company} onChange={(e) => setForm({ ...form, company: e.target.value })} placeholder="Cabinet ou organisation" /></div></div>
                <div className="space-y-2 sm:col-span-2"><Label htmlFor="profile-phone">Téléphone</Label><div className="relative"><Phone className="absolute left-3 top-3 size-4 text-muted-foreground" /><Input id="profile-phone" className="pl-9" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} placeholder="+33…" /></div></div>
                <div className="space-y-2 sm:col-span-2"><Label htmlFor="profile-bio">Présentation</Label><Textarea id="profile-bio" value={form.bio} onChange={(e) => setForm({ ...form, bio: e.target.value })} placeholder="Domaines d’expertise, spécialités techniques…" className="min-h-28 resize-y" /></div>
              </div>
              <div className="flex justify-end"><Button type="submit" disabled={saving}>{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Enregistrer</Button></div>
            </form>
          </CardContent>
        </Card>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="size-4 text-brand" />Accès</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Rôle</span><span className="font-medium">{roleLabel(user.role)}</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Compte</span><span className="font-medium text-success">Actif</span></div>
              <div className="flex justify-between gap-3"><span className="text-muted-foreground">Membre depuis</span><span className="font-medium">{new Date(user.created_at).toLocaleDateString("fr-FR")}</span></div>
            </CardContent>
          </Card>
          <Card className="bg-brand/5">
            <CardHeader><CardTitle>Confidentialité</CardTitle><CardDescription>Votre rôle et vos affectations sont contrôlés par l’administration Ennoma.</CardDescription></CardHeader>
          </Card>
        </div>
      </div>
    </div>
  )
}

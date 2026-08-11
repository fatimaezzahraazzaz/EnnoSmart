"use client"

import { useEffect, useState } from "react"
import { Bell, CheckCircle2, KeyRound, Languages, Loader2, MonitorCog, Save } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { changePassword, getAccount, updatePreferences, type UserPreferences } from "@/lib/api"

const defaultPreferences: UserPreferences = {
  language: "fr",
  timezone: "Africa/Casablanca",
  theme: "system",
  compact_sidebar: false,
  email_notifications: true,
  project_notifications: true,
  weekly_summary: true,
}

function Toggle({ checked, onChange, label, description }: { checked: boolean; onChange: (checked: boolean) => void; label: string; description: string }) {
  return (
    <label className="flex cursor-pointer items-center justify-between gap-5 rounded-xl border p-4 transition hover:bg-muted/40">
      <span><span className="block text-sm font-medium">{label}</span><span className="mt-1 block text-xs leading-5 text-muted-foreground">{description}</span></span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} className="peer sr-only" />
      <span className="relative h-6 w-11 shrink-0 rounded-full bg-muted transition peer-checked:bg-brand after:absolute after:left-1 after:top-1 after:size-4 after:rounded-full after:bg-white after:shadow after:transition peer-checked:after:translate-x-5" />
    </label>
  )
}

export default function SettingsPage() {
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences)
  const [currentPassword, setCurrentPassword] = useState("")
  const [newPassword, setNewPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  useEffect(() => {
    getAccount().then((account) => setPreferences(account.preferences)).catch((err) => setError(err instanceof Error ? err.message : "Paramètres indisponibles.")).finally(() => setLoading(false))
  }, [])

  const savePreferences = async () => {
    setSaving(true); setError(""); setMessage("")
    try {
      const account = await updatePreferences(preferences)
      setPreferences(account.preferences)
      document.documentElement.classList.toggle("dark", account.preferences.theme === "dark")
      setMessage("Préférences enregistrées.")
    } catch (err) { setError(err instanceof Error ? err.message : "Enregistrement impossible.") } finally { setSaving(false) }
  }

  const savePassword = async (event: React.FormEvent) => {
    event.preventDefault(); setError(""); setMessage("")
    if (newPassword !== confirmPassword) { setError("Les nouveaux mots de passe ne correspondent pas."); return }
    setSaving(true)
    try {
      const result = await changePassword(currentPassword, newPassword)
      setCurrentPassword(""); setNewPassword(""); setConfirmPassword(""); setMessage(result.message)
    } catch (err) { setError(err instanceof Error ? err.message : "Modification impossible.") } finally { setSaving(false) }
  }

  if (loading) return <div className="flex min-h-[60vh] items-center justify-center"><Loader2 className="size-6 animate-spin text-brand" /></div>

  return (
    <div className="mx-auto max-w-5xl space-y-6 p-5 sm:p-7 lg:p-9">
      <div><p className="text-sm font-medium text-brand">Votre espace</p><h1 className="mt-1 text-3xl font-semibold tracking-tight">Paramètres</h1><p className="mt-2 text-sm text-muted-foreground">Adaptez Ennoma à votre manière de travailler.</p></div>
      {message && <div className="flex items-center gap-2 rounded-xl border border-success/25 bg-success/10 p-3 text-sm text-success"><CheckCircle2 className="size-4" />{message}</div>}
      {error && <div className="rounded-xl border border-destructive/25 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><MonitorCog className="size-4 text-brand" />Interface</CardTitle><CardDescription>Apparence et organisation de votre navigation.</CardDescription></CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2"><Label htmlFor="theme">Thème</Label><select id="theme" value={preferences.theme} onChange={(e) => setPreferences({ ...preferences, theme: e.target.value as UserPreferences["theme"] })} className="h-10 w-full rounded-lg border bg-background px-3 text-sm"><option value="system">Système</option><option value="light">Clair</option><option value="dark">Sombre</option></select></div>
            <div className="space-y-2"><Label htmlFor="language" className="flex items-center gap-2"><Languages className="size-4" />Langue</Label><select id="language" value={preferences.language} onChange={(e) => setPreferences({ ...preferences, language: e.target.value })} className="h-10 w-full rounded-lg border bg-background px-3 text-sm"><option value="fr">Français</option><option value="en">English</option></select></div>
            <div className="space-y-2"><Label htmlFor="timezone">Fuseau horaire</Label><Input id="timezone" value={preferences.timezone} onChange={(e) => setPreferences({ ...preferences, timezone: e.target.value })} /></div>
            <Toggle checked={preferences.compact_sidebar} onChange={(value) => setPreferences({ ...preferences, compact_sidebar: value })} label="Menu compact" description="Réduire automatiquement la barre latérale." />
            <Button onClick={savePreferences} disabled={saving}>{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Enregistrer</Button>
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle className="flex items-center gap-2"><Bell className="size-4 text-brand" />Notifications</CardTitle><CardDescription>Choisissez les informations importantes pour vous.</CardDescription></CardHeader>
          <CardContent className="space-y-3">
            <Toggle checked={preferences.email_notifications} onChange={(value) => setPreferences({ ...preferences, email_notifications: value })} label="Notifications e-mail" description="Recevoir les alertes importantes liées au compte." />
            <Toggle checked={preferences.project_notifications} onChange={(value) => setPreferences({ ...preferences, project_notifications: value })} label="Activité des projets" description="Être averti lors d’un changement d’étape ou d’affectation." />
            <Toggle checked={preferences.weekly_summary} onChange={(value) => setPreferences({ ...preferences, weekly_summary: value })} label="Résumé hebdomadaire" description="Un récapitulatif synthétique des dossiers en cours." />
            <Button onClick={savePreferences} disabled={saving} variant="outline"><Save className="size-4" />Enregistrer</Button>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="size-4 text-brand" />Sécurité du compte</CardTitle><CardDescription>Le changement de mot de passe déconnectera les anciennes sessions à leur expiration.</CardDescription></CardHeader>
        <CardContent>
          <form onSubmit={savePassword} className="grid gap-4 md:grid-cols-3 md:items-end">
            <div className="space-y-2"><Label htmlFor="current-password">Mot de passe actuel</Label><Input id="current-password" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} required /></div>
            <div className="space-y-2"><Label htmlFor="new-password">Nouveau mot de passe</Label><Input id="new-password" type="password" minLength={8} value={newPassword} onChange={(e) => setNewPassword(e.target.value)} required /></div>
            <div className="space-y-2"><Label htmlFor="confirm-new-password">Confirmation</Label><Input id="confirm-new-password" type="password" minLength={8} value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required /></div>
            <Button type="submit" disabled={saving} className="md:col-start-3"><KeyRound className="size-4" />Mettre à jour</Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}

"use client"

import { useEffect, useMemo, useState } from "react"
import {
  Building2,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  Loader2,
  LockKeyhole,
  Mail,
  Phone,
  Save,
  ShieldCheck,
  UserRound,
} from "lucide-react"

import { Avatar, AvatarFallback } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  changePassword,
  getAccount,
  getProjects,
  updateProfile,
  type AccountRead,
  type UserRead,
} from "@/lib/api"
import {
  LoadingState,
  PageHeader,
  StatusNotice,
} from "@/components/ennosmart/workspace-ui"

function initials(name: string) {
  return (
    name
      .split(/\s+/)
      .filter(Boolean)
      .slice(0, 2)
      .map((part) => part[0])
      .join("")
      .toUpperCase() || "EN"
  )
}

function roleLabel(role: string) {
  if (role === "superadmin") return "Super administrateur"
  if (role === "admin") return "Administrateur"
  return "Consultant CIR"
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  autoComplete,
  placeholder,
}: {
  id: string
  label: string
  value: string
  onChange: (value: string) => void
  autoComplete: string
  placeholder?: string
}) {
  const [visible, setVisible] = useState(false)

  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      <div className="relative">
        <LockKeyhole className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <Input
          id={id}
          type={visible ? "text" : "password"}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          autoComplete={autoComplete}
          minLength={id === "current-password" ? undefined : 8}
          required
          placeholder={placeholder}
          className="h-11 rounded-xl pl-10 pr-10"
        />
        <button
          type="button"
          onClick={() => setVisible((current) => !current)}
          className="absolute right-2.5 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-lg text-muted-foreground transition hover:bg-muted hover:text-foreground"
          aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
        >
          {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
        </button>
      </div>
    </div>
  )
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

  const [passwordForm, setPasswordForm] = useState({
    current: "",
    next: "",
    confirm: "",
  })

  const [loading, setLoading] = useState(true)
  const [savingProfile, setSavingProfile] = useState(false)
  const [savingPassword, setSavingPassword] = useState(false)

  const [profileMessage, setProfileMessage] = useState("")
  const [profileError, setProfileError] = useState("")
  const [passwordMessage, setPasswordMessage] = useState("")
  const [passwordError, setPasswordError] = useState("")

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
      .catch((err) =>
        setProfileError(
          err instanceof Error ? err.message : "Profil indisponible.",
        ),
      )
      .finally(() => setLoading(false))
  }, [])

  const memberSince = useMemo(() => {
    const date = new Date(account?.user.created_at || user.created_at)
    if (Number.isNaN(date.getTime())) return "—"
    return date.toLocaleDateString("fr-FR")
  }, [account?.user.created_at, user.created_at])

  const saveProfile = async (event: React.FormEvent) => {
    event.preventDefault()
    setSavingProfile(true)
    setProfileMessage("")
    setProfileError("")

    try {
      const updated = await updateProfile(form)
      setAccount(updated)
      onUserUpdated(updated.user)
      setProfileMessage("Votre profil a été mis à jour.")
    } catch (err) {
      setProfileError(
        err instanceof Error
          ? err.message
          : "Impossible d’enregistrer le profil.",
      )
    } finally {
      setSavingProfile(false)
    }
  }

  const savePassword = async (event: React.FormEvent) => {
    event.preventDefault()
    setPasswordMessage("")
    setPasswordError("")

    if (!passwordForm.current.trim()) {
      setPasswordError("Saisissez votre mot de passe actuel.")
      return
    }

    if (passwordForm.next.length < 8) {
      setPasswordError(
        "Le nouveau mot de passe doit contenir au moins 8 caractères.",
      )
      return
    }

    if (passwordForm.next !== passwordForm.confirm) {
      setPasswordError("Les nouveaux mots de passe ne correspondent pas.")
      return
    }

    setSavingPassword(true)

    try {
      const result = await changePassword(
        passwordForm.current,
        passwordForm.next,
      )
      setPasswordForm({
        current: "",
        next: "",
        confirm: "",
      })
      setPasswordMessage(result.message || "Mot de passe mis à jour.")
    } catch (err) {
      setPasswordError(
        err instanceof Error
          ? err.message
          : "Impossible de modifier le mot de passe.",
      )
    } finally {
      setSavingPassword(false)
    }
  }

  if (loading) {
    return <LoadingState label="Chargement du profil…" />
  }

  const displayName = account?.user.full_name || user.full_name
  const displayRole = roleLabel(account?.user.role || user.role)

  return (
    <div className="workspace-page space-y-5 pb-10">
      <PageHeader
        eyebrow="Compte"
        title="Mon profil"
        description="Gérez votre identité, vos informations professionnelles et la sécurité de votre compte."
      />

      <section className="relative overflow-hidden rounded-2xl border border-brand/15 bg-card shadow-sm">
        <div
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(circle_at_8%_0%,rgba(110,69,180,.12),transparent_30%),radial-gradient(circle_at_90%_20%,rgba(110,69,180,.055),transparent_26%)]"
          aria-hidden="true"
        />

        <div className="relative flex flex-col gap-5 px-5 py-6 sm:px-7 lg:flex-row lg:items-center">
          <Avatar className="size-20 border-4 border-background shadow-lg sm:size-24">
            <AvatarFallback className="bg-brand text-2xl font-semibold text-white sm:text-3xl">
              {initials(displayName)}
            </AvatarFallback>
          </Avatar>

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="truncate text-2xl font-semibold tracking-[-0.025em] text-foreground sm:text-3xl">
                {displayName}
              </h1>
              <Badge className="border border-brand/15 bg-brand/8 text-brand hover:bg-brand/8">
                {displayRole}
              </Badge>
            </div>

            <p className="mt-1.5 text-sm text-muted-foreground">
              {form.job_title || "Expertise CIR"}
              {form.company ? ` · ${form.company}` : ""}
            </p>

            <div className="mt-4 flex flex-wrap gap-2">
              <Badge variant="outline" className="rounded-full bg-background/80">
                {projectCount} projet{projectCount > 1 ? "s" : ""} accessible{projectCount > 1 ? "s" : ""}
              </Badge>
              <Badge
                variant="outline"
                className="rounded-full border-emerald-200 bg-emerald-50 text-emerald-700"
              >
                <CheckCircle2 className="size-3.5" />
                Compte actif
              </Badge>
            </div>
          </div>

          <div className="grid min-w-[280px] grid-cols-2 gap-3">
            <div className="rounded-xl border border-border/70 bg-background/75 px-4 py-3 backdrop-blur">
              <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                Rôle
              </p>
              <p className="mt-1 text-sm font-semibold text-foreground">
                {displayRole}
              </p>
            </div>
            <div className="rounded-xl border border-border/70 bg-background/75 px-4 py-3 backdrop-blur">
              <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                Membre depuis
              </p>
              <p className="mt-1 text-sm font-semibold text-foreground">
                {memberSince}
              </p>
            </div>
          </div>
        </div>
      </section>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="space-y-5">
          <Card className="overflow-hidden rounded-2xl border-border/80 shadow-sm">
            <CardHeader className="border-b bg-muted/[0.10] px-5 py-4 sm:px-6">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/8 text-brand">
                  <UserRound className="size-4" />
                </span>
                <div>
                  <CardTitle>Informations personnelles</CardTitle>
                  <CardDescription className="mt-1">
                    Informations utilisées dans votre espace et vos affectations.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="p-5 sm:p-6">
              {profileMessage && (
                <StatusNotice
                  className="mb-5"
                  state="validated"
                  title={profileMessage}
                />
              )}
              {profileError && (
                <StatusNotice
                  className="mb-5"
                  state="failed"
                  title="Enregistrement impossible"
                  description={profileError}
                />
              )}

              <form onSubmit={saveProfile} className="space-y-5">
                <div className="grid gap-5 md:grid-cols-2">
                  <div className="space-y-2">
                    <Label htmlFor="profile-name">Nom complet</Label>
                    <div className="relative">
                      <UserRound className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="profile-name"
                        className="h-11 rounded-xl pl-10"
                        value={form.full_name}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            full_name: event.target.value,
                          })
                        }
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="profile-email">E-mail</Label>
                    <div className="relative">
                      <Mail className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="profile-email"
                        type="email"
                        className="h-11 rounded-xl pl-10"
                        value={form.email}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            email: event.target.value,
                          })
                        }
                        required
                      />
                    </div>
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="profile-job">Fonction</Label>
                    <Input
                      id="profile-job"
                      className="h-11 rounded-xl"
                      value={form.job_title}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          job_title: event.target.value,
                        })
                      }
                      placeholder="Consultant CIR senior"
                    />
                  </div>

                  <div className="space-y-2">
                    <Label htmlFor="profile-company">Entreprise</Label>
                    <div className="relative">
                      <Building2 className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="profile-company"
                        className="h-11 rounded-xl pl-10"
                        value={form.company}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            company: event.target.value,
                          })
                        }
                        placeholder="Cabinet ou organisation"
                      />
                    </div>
                  </div>

                  <div className="space-y-2 md:col-span-2">
                    <Label htmlFor="profile-phone">Téléphone</Label>
                    <div className="relative">
                      <Phone className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                      <Input
                        id="profile-phone"
                        className="h-11 rounded-xl pl-10"
                        value={form.phone}
                        onChange={(event) =>
                          setForm({
                            ...form,
                            phone: event.target.value,
                          })
                        }
                        placeholder="+33…"
                      />
                    </div>
                  </div>

                  <div className="space-y-2 md:col-span-2">
                    <Label htmlFor="profile-bio">Présentation</Label>
                    <Textarea
                      id="profile-bio"
                      value={form.bio}
                      onChange={(event) =>
                        setForm({
                          ...form,
                          bio: event.target.value,
                        })
                      }
                      placeholder="Domaines d’expertise, spécialités techniques…"
                      className="min-h-28 resize-y rounded-xl"
                    />
                  </div>
                </div>

                <div className="flex justify-end border-t pt-5">
                  <Button
                    type="submit"
                    className="min-h-10 rounded-xl px-5"
                    disabled={savingProfile}
                  >
                    {savingProfile ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Save className="size-4" />
                    )}
                    Enregistrer les informations
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>

          <Card className="overflow-hidden rounded-2xl border-brand/15 shadow-sm">
            <CardHeader className="border-b bg-brand/[0.025] px-5 py-4 sm:px-6">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-brand/8 text-brand">
                  <KeyRound className="size-4" />
                </span>
                <div>
                  <CardTitle>Sécurité du compte</CardTitle>
                  <CardDescription className="mt-1">
                    Modifiez votre mot de passe directement depuis votre profil.
                  </CardDescription>
                </div>
              </div>
            </CardHeader>

            <CardContent className="p-5 sm:p-6">
              {passwordMessage && (
                <StatusNotice
                  className="mb-5"
                  state="validated"
                  title={passwordMessage}
                />
              )}
              {passwordError && (
                <StatusNotice
                  className="mb-5"
                  state="failed"
                  title="Mot de passe non modifié"
                  description={passwordError}
                />
              )}

              <form onSubmit={savePassword} className="space-y-5">
                <div className="grid gap-5 lg:grid-cols-3">
                  <PasswordField
                    id="current-password"
                    label="Mot de passe actuel"
                    value={passwordForm.current}
                    onChange={(value) =>
                      setPasswordForm({
                        ...passwordForm,
                        current: value,
                      })
                    }
                    autoComplete="current-password"
                    placeholder="Votre mot de passe actuel"
                  />

                  <PasswordField
                    id="new-password"
                    label="Nouveau mot de passe"
                    value={passwordForm.next}
                    onChange={(value) =>
                      setPasswordForm({
                        ...passwordForm,
                        next: value,
                      })
                    }
                    autoComplete="new-password"
                    placeholder="8 caractères minimum"
                  />

                  <PasswordField
                    id="confirm-new-password"
                    label="Confirmer le mot de passe"
                    value={passwordForm.confirm}
                    onChange={(value) =>
                      setPasswordForm({
                        ...passwordForm,
                        confirm: value,
                      })
                    }
                    autoComplete="new-password"
                    placeholder="Répétez le nouveau mot de passe"
                  />
                </div>

                <div className="flex flex-col gap-3 rounded-xl border border-brand/10 bg-brand/[0.025] px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="flex items-start gap-2.5">
                    <ShieldCheck className="mt-0.5 size-4 shrink-0 text-brand" />
                    <p className="text-xs leading-5 text-muted-foreground">
                      Le nouveau mot de passe doit contenir au moins 8 caractères.
                    </p>
                  </div>

                  <Button
                    type="submit"
                    variant="outline"
                    className="min-h-10 shrink-0 rounded-xl border-brand/20"
                    disabled={savingPassword}
                  >
                    {savingPassword ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <KeyRound className="size-4" />
                    )}
                    Mettre à jour le mot de passe
                  </Button>
                </div>
              </form>
            </CardContent>
          </Card>
        </div>

        <aside className="space-y-5">
          <Card className="rounded-2xl border-border/80 shadow-sm">
            <CardHeader className="pb-3">
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="size-4 text-brand" />
                Accès
              </CardTitle>
              <CardDescription>
                Informations de votre compte Ennoma.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-1">
              <div className="flex items-center justify-between gap-4 border-b py-3 text-sm">
                <span className="text-muted-foreground">Rôle</span>
                <span className="text-right font-medium">{displayRole}</span>
              </div>

              <div className="flex items-center justify-between gap-4 border-b py-3 text-sm">
                <span className="text-muted-foreground">Compte</span>
                <span className="flex items-center gap-1.5 font-medium text-emerald-700">
                  <span className="size-2 rounded-full bg-emerald-500" />
                  Actif
                </span>
              </div>

              <div className="flex items-center justify-between gap-4 py-3 text-sm">
                <span className="text-muted-foreground">Membre depuis</span>
                <span className="font-medium">{memberSince}</span>
              </div>
            </CardContent>
          </Card>

          <Card className="rounded-2xl border-brand/15 bg-brand/[0.035] shadow-sm">
            <CardContent className="p-5">
              <div className="flex items-start gap-3">
                <span className="grid size-9 shrink-0 place-items-center rounded-xl bg-background text-brand shadow-sm ring-1 ring-brand/10">
                  <ShieldCheck className="size-4" />
                </span>
                <div>
                  <p className="text-sm font-semibold text-foreground">
                    Confidentialité
                  </p>
                  <p className="mt-1.5 text-xs leading-5 text-muted-foreground">
                    Votre rôle et vos affectations sont contrôlés par l’administration Ennoma.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          <Card className="rounded-2xl border-border/80 shadow-sm">
            <CardContent className="p-5">
              <p className="text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                E-mail du compte
              </p>
              <div className="mt-2 flex items-center gap-2">
                <Mail className="size-4 shrink-0 text-brand" />
                <p className="min-w-0 truncate text-sm font-medium">
                  {form.email || user.email}
                </p>
              </div>
            </CardContent>
          </Card>
        </aside>
      </div>
    </div>
  )
}

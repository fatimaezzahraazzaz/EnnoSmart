"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  ArrowLeft,
  CheckCircle2,
  Eye,
  EyeOff,
  Loader2,
  LockKeyhole,
  ShieldCheck,
  Sparkles,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  forgotPassword,
  getMe,
  login,
  register,
  resetPassword,
  type UserRead,
} from "@/lib/api"

type AuthMode = "login" | "register" | "forgot" | "reset"

interface LoginPageProps {
  onLogin: (user: UserRead) => void
}

const modeCopy: Record<AuthMode, { title: string; description: string }> = {
  login: {
    title: "Ravi de vous revoir",
    description: "Connectez-vous à votre espace de travail Ennoma.",
  },
  register: {
    title: "Créer votre compte",
    description: "Rejoignez l’espace sécurisé des consultants CIR.",
  },
  forgot: {
    title: "Mot de passe oublié",
    description: "Nous vous envoyons un lien de récupération sécurisé.",
  },
  reset: {
    title: "Nouveau mot de passe",
    description: "Choisissez un mot de passe robuste pour votre compte.",
  },
}

function PasswordInput({
  id,
  value,
  onChange,
  placeholder = "8 caractères minimum",
  autoComplete,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  placeholder?: string
  autoComplete?: string
}) {
  const [visible, setVisible] = useState(false)
  return (
    <div className="relative">
      <Input
        id={id}
        type={visible ? "text" : "password"}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        autoComplete={autoComplete}
        className="h-11 pr-11"
        minLength={8}
        required
      />
      <button
        type="button"
        onClick={() => setVisible((current) => !current)}
        className="absolute inset-y-0 right-0 flex w-11 items-center justify-center text-muted-foreground transition hover:text-foreground"
        aria-label={visible ? "Masquer le mot de passe" : "Afficher le mot de passe"}
      >
        {visible ? <EyeOff className="size-4" /> : <Eye className="size-4" />}
      </button>
    </div>
  )
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [mode, setMode] = useState<AuthMode>("login")
  const [fullName, setFullName] = useState("")
  const [company, setCompany] = useState("")
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [confirmPassword, setConfirmPassword] = useState("")
  const [resetToken, setResetToken] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")

  useEffect(() => {
    const token = new URLSearchParams(window.location.search).get("reset_token")
    if (token) {
      setResetToken(token)
      setMode("reset")
    }
  }, [])

  const passwordScore = useMemo(() => {
    let score = 0
    if (password.length >= 8) score += 1
    if (/[A-Z]/.test(password) && /[a-z]/.test(password)) score += 1
    if (/\d/.test(password)) score += 1
    if (/[^A-Za-z0-9]/.test(password)) score += 1
    return score
  }, [password])

  const switchMode = (next: AuthMode) => {
    setMode(next)
    setError("")
    setSuccess("")
    setPassword("")
    setConfirmPassword("")
  }

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    setError("")
    setSuccess("")
    setLoading(true)
    try {
      if (mode === "login") {
        await login({ email, password })
        onLogin(await getMe())
        return
      }

      if (mode === "register") {
        if (password !== confirmPassword) throw new Error("Les mots de passe ne correspondent pas.")
        await register({
          full_name: fullName,
          email,
          password,
          company: company || undefined,
          job_title: "Consultant CIR",
        })
        await login({ email, password })
        onLogin(await getMe())
        return
      }

      if (mode === "forgot") {
        const response = await forgotPassword(email)
        setSuccess(response.message)
        if (response.preview_token) {
          setResetToken(response.preview_token)
          setTimeout(() => switchMode("reset"), 900)
        }
        return
      }

      if (!resetToken) throw new Error("Le jeton de réinitialisation est absent.")
      if (password !== confirmPassword) throw new Error("Les mots de passe ne correspondent pas.")
      const response = await resetPassword(resetToken, password)
      window.history.replaceState({}, "", window.location.pathname)
      setSuccess(response.message)
      setTimeout(() => switchMode("login"), 1000)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Une erreur inattendue est survenue.")
    } finally {
      setLoading(false)
    }
  }

  const copy = modeCopy[mode]

  return (
    <div className="min-h-screen bg-background lg:grid lg:grid-cols-[minmax(420px,0.92fr)_1.08fr]">
      <aside className="relative hidden min-h-screen overflow-hidden bg-[#2a0b63] p-12 text-white lg:flex lg:flex-col lg:justify-between">
        <div className="absolute -right-36 -top-32 size-[430px] rounded-full bg-fuchsia-400/20 blur-3xl" />
        <div className="absolute -bottom-48 -left-20 size-[520px] rounded-full bg-violet-400/20 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <img src="/ennoma-logo.png" alt="Logo Ennoma" className="size-12 rounded-[14px] shadow-xl shadow-black/20" />
          <div>
            <p className="text-xl font-bold tracking-tight">Ennoma</p>
            <p className="text-xs text-violet-200">Intelligence CIR multi-agents</p>
          </div>
        </div>

        <div className="relative max-w-xl space-y-8">
          <div className="inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/10 px-3 py-1.5 text-xs font-medium text-violet-100 backdrop-blur">
            <Sparkles className="size-3.5" /> Une expertise augmentée, jamais remplacée
          </div>
          <div className="space-y-4">
            <h1 className="max-w-lg text-5xl font-semibold leading-[1.08] tracking-[-0.04em]">
              Pilotez chaque dossier CIR avec précision.
            </h1>
            <p className="max-w-lg text-base leading-7 text-violet-100/75">
              Diagnostic, recherche scientifique, amélioration rédactionnelle et mémoire d’entreprise réunis dans un espace clair, traçable et sécurisé.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-3">
            {[
              ["3", "agents spécialisés"],
              ["100%", "traçable"],
              ["CIR", "de bout en bout"],
            ].map(([value, label]) => (
              <div key={label} className="rounded-2xl border border-white/10 bg-white/[0.07] p-4 backdrop-blur-sm">
                <p className="text-xl font-semibold">{value}</p>
                <p className="mt-1 text-xs text-violet-200/75">{label}</p>
              </div>
            ))}
          </div>
        </div>

        <div className="relative flex items-center gap-2 text-xs text-violet-200/70">
          <ShieldCheck className="size-4" /> Accès chiffré et contrôlé par rôle
        </div>
      </aside>

      <main className="flex min-h-screen items-center justify-center px-5 py-10 sm:px-10">
        <div className="w-full max-w-[430px] animate-slideUp">
          <div className="mb-9 flex items-center justify-between lg:hidden">
            <div className="flex items-center gap-3">
              <img src="/ennoma-logo.png" alt="Logo Ennoma" className="size-11 rounded-[13px] shadow-md" />
              <div>
                <p className="font-bold">Ennoma</p>
                <p className="text-[11px] text-muted-foreground">Plateforme CIR IA</p>
              </div>
            </div>
          </div>

          {(mode === "login" || mode === "register") && (
            <div className="mb-8 grid grid-cols-2 rounded-xl bg-muted p-1">
              <button
                type="button"
                onClick={() => switchMode("login")}
                className={`rounded-lg px-4 py-2.5 text-sm font-medium transition ${mode === "login" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                Connexion
              </button>
              <button
                type="button"
                onClick={() => switchMode("register")}
                className={`rounded-lg px-4 py-2.5 text-sm font-medium transition ${mode === "register" ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground"}`}
              >
                Inscription
              </button>
            </div>
          )}

          {(mode === "forgot" || mode === "reset") && (
            <button
              type="button"
              onClick={() => switchMode("login")}
              className="mb-7 inline-flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
            >
              <ArrowLeft className="size-4" /> Retour à la connexion
            </button>
          )}

          <div className="mb-7 space-y-2">
            <h2 className="text-3xl font-semibold tracking-[-0.03em] text-foreground">{copy.title}</h2>
            <p className="text-sm leading-6 text-muted-foreground">{copy.description}</p>
          </div>

          {error && (
            <div className="mb-5 flex gap-2 rounded-xl border border-destructive/25 bg-destructive/8 p-3 text-sm text-destructive">
              <AlertCircle className="mt-0.5 size-4 shrink-0" /> <p>{error}</p>
            </div>
          )}
          {success && (
            <div className="mb-5 flex gap-2 rounded-xl border border-success/25 bg-success/8 p-3 text-sm text-success">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0" /> <p>{success}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-4">
            {mode === "register" && (
              <>
                <div className="space-y-2">
                  <Label htmlFor="full-name">Nom complet</Label>
                  <Input id="full-name" value={fullName} onChange={(event) => setFullName(event.target.value)} placeholder="Prénom Nom" autoComplete="name" className="h-11" required />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="company">Cabinet ou entreprise <span className="font-normal text-muted-foreground">(optionnel)</span></Label>
                  <Input id="company" value={company} onChange={(event) => setCompany(event.target.value)} placeholder="Votre organisation" autoComplete="organization" className="h-11" />
                </div>
              </>
            )}

            {mode !== "reset" && (
              <div className="space-y-2">
                <Label htmlFor="email">Adresse e-mail</Label>
                <Input id="email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder="vous@cabinet.fr" autoComplete="email" className="h-11" required />
              </div>
            )}

            {(mode === "login" || mode === "register" || mode === "reset") && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label htmlFor="password">{mode === "reset" ? "Nouveau mot de passe" : "Mot de passe"}</Label>
                  {mode === "login" && (
                    <button type="button" onClick={() => switchMode("forgot")} className="text-xs font-medium text-brand hover:underline">
                      Mot de passe oublié ?
                    </button>
                  )}
                </div>
                <PasswordInput id="password" value={password} onChange={setPassword} autoComplete={mode === "login" ? "current-password" : "new-password"} />
                {(mode === "register" || mode === "reset") && password && (
                  <div className="flex gap-1 pt-1" aria-label={`Robustesse ${passwordScore} sur 4`}>
                    {[1, 2, 3, 4].map((level) => (
                      <span key={level} className={`h-1 flex-1 rounded-full ${passwordScore >= level ? "bg-brand" : "bg-muted"}`} />
                    ))}
                  </div>
                )}
              </div>
            )}

            {(mode === "register" || mode === "reset") && (
              <div className="space-y-2">
                <Label htmlFor="confirm-password">Confirmer le mot de passe</Label>
                <PasswordInput id="confirm-password" value={confirmPassword} onChange={setConfirmPassword} autoComplete="new-password" />
              </div>
            )}

            {mode === "reset" && !resetToken && (
              <div className="space-y-2">
                <Label htmlFor="reset-token">Jeton de récupération</Label>
                <Input id="reset-token" value={resetToken} onChange={(event) => setResetToken(event.target.value)} placeholder="Collez le jeton reçu" className="h-11 font-mono text-xs" required />
              </div>
            )}

            <Button type="submit" className="mt-2 h-11 w-full bg-primary font-semibold text-primary-foreground shadow-sm hover:bg-primary/90" disabled={loading}>
              {loading ? <><Loader2 className="size-4 animate-spin" /> Traitement en cours…</> : mode === "login" ? "Se connecter" : mode === "register" ? "Créer mon compte" : mode === "forgot" ? "Envoyer le lien" : "Enregistrer le mot de passe"}
            </Button>
          </form>

          <div className="mt-8 flex items-center justify-center gap-2 text-xs text-muted-foreground">
            <LockKeyhole className="size-3.5" /> Connexion sécurisée Ennoma
          </div>
        </div>
      </main>
    </div>
  )
}

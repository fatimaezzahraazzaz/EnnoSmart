"use client"

import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Loader2, BrainCircuit, AlertCircle } from "lucide-react"
import { getMe, login, type UserRead } from "@/lib/api"

interface LoginPageProps {
  onLogin: (user: UserRead) => void
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError("")
    setLoading(true)

    try {
      await login({
        email,
        password,
      })

      const currentUser = await getMe()
      onLogin(currentUser)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de se connecter au backend."
      )
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel — branding */}
      <div className="hidden lg:flex lg:w-1/2 bg-brand flex-col justify-between p-12">
        <div className="flex items-center gap-3">
          <div className="size-9 rounded-lg bg-brand flex items-center justify-center">
            <BrainCircuit className="size-5 text-primary-foreground" />
          </div>
          <span className="text-xl font-semibold text-primary-foreground tracking-tight">
            EnnoSmart
          </span>
        </div>

        <div className="space-y-6">
          <div className="space-y-3">
            <p className="text-xs font-semibold uppercase tracking-widest text-primary-foreground/50">
              Plateforme CIR IA
            </p>
            <h1 className="text-4xl font-bold text-primary-foreground leading-tight text-balance">
              Analysez vos dossiers CIR avec l&apos;intelligence artificielle
            </h1>
            <p className="text-base text-primary-foreground/60 leading-relaxed">
              Diagnostic automatisé, extraction documentaire et assistance RAG
              pour les consultants spécialisés.
            </p>
          </div>

          <div className="grid grid-cols-3 gap-4 pt-4">
            {[
              { value: "IA", label: "Multi-agents" },
              { value: "CIR", label: "Traçabilité" },
              { value: "RAG", label: "Sources" },
            ].map((stat) => (
              <div
                key={stat.label}
                className="rounded-xl bg-primary-foreground/5 border border-primary-foreground/10 p-4"
              >
                <p className="text-2xl font-bold text-primary-foreground">
                  {stat.value}
                </p>
                <p className="text-xs text-primary-foreground/50 mt-1">
                  {stat.label}
                </p>
              </div>
            ))}
          </div>
        </div>

        <div className="flex items-center gap-3">
          <p className="text-sm text-primary-foreground/50">
            Espace sécurisé pour les consultants CIR
          </p>
        </div>
      </div>

      {/* Right panel — login form */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-sm space-y-8">
          {/* Mobile logo */}
          <div className="flex items-center gap-3 lg:hidden">
            <div className="size-9 rounded-lg bg-primary flex items-center justify-center">
              <BrainCircuit className="size-5 text-primary-foreground" />
            </div>
            <span className="text-xl font-semibold text-foreground tracking-tight">
              EnnoSmart
            </span>
          </div>

          <div className="space-y-2">
            <h2 className="text-2xl font-bold text-foreground tracking-tight">
              Connexion
            </h2>
            <p className="text-sm text-muted-foreground">
              Accédez à votre espace consultant
            </p>
          </div>

          {error && (
            <div className="flex gap-2 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
              <AlertCircle className="size-4 mt-0.5 flex-shrink-0" />
              <p>{error}</p>
            </div>
          )}

          <form onSubmit={handleSubmit} className="space-y-5">
            <div className="space-y-2">
              <Label htmlFor="email" className="text-sm font-medium text-foreground">
                Adresse e-mail
              </Label>
              <Input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="vous@cabinet.fr"
                className="h-11"
                required
              />
            </div>

            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <Label htmlFor="password" className="text-sm font-medium text-foreground">
                  Mot de passe
                </Label>
                <button
                  type="button"
                  className="text-xs text-[color:var(--color-brand)] hover:underline font-medium"
                >
                  Mot de passe oublié ?
                </button>
              </div>
              <Input
                id="password"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
                className="h-11"
                required
              />
            </div>

            <Button
              type="submit"
              className="w-full h-11 bg-primary hover:bg-primary/90 text-primary-foreground font-semibold"
              disabled={loading}
            >
              {loading ? (
                <>
                  <Loader2 className="size-4 animate-spin" data-icon="inline-start" />
                  Connexion en cours…
                </>
              ) : (
                "Se connecter"
              )}
            </Button>
          </form>

          <p className="text-center text-xs text-muted-foreground">
            Backend attendu :{" "}
            <span className="text-foreground font-medium">
              http://127.0.0.1:8000
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}

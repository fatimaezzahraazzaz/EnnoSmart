"use client"

import { useEffect, useState } from "react"
import {
  Bot,
  CheckCircle2,
  Cpu,
  Loader2,
  Save,
  ShieldCheck,
} from "lucide-react"

import { LoadingState, PageHeader, StatusNotice } from "@/components/ennosmart/workspace-ui"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  AGENT_AVAILABILITY_CHANGE_EVENT,
  getAISettings,
  getEffectiveAgentModels,
  updateAISettings,
  type AIModelSettings,
  type EffectiveAgentModels,
} from "@/lib/api"

const defaults: AIModelSettings = {
  provider: "ollama",
  primary_model: "qwen2.5:7b-instruct",
  writer_model: null,
  fallback_models: [],
  allow_cross_provider_fallback: false,
  default_temperature: 0.1,
  max_output_tokens_cap: 16000,
  max_prompt_chars: 30000,
  writer_max_prompt_chars: 180000,
  monthly_budget_eur: 500,
  enabled_agents: { diagnostic: true, scholar: true, improvement: true, cir_memory: true },
}

const agentLabels: Record<string, string> = {
  diagnostic: "EnnoDiagnostic",
  scholar: "EnnoScholar",
  improvement: "EnnoAmelioration",
  cir_memory: "CIR Memory",
}

export default function SystemSettingsPage() {
  const [settings, setSettings] = useState<AIModelSettings>(defaults)
  const [effectiveModels, setEffectiveModels] = useState<EffectiveAgentModels | null>(null)
  const [fallbacks, setFallbacks] = useState("")
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  const load = async () => {
    setLoading(true)
    setError("")
    try {
      const [configuration, models] = await Promise.all([
        getAISettings(),
        getEffectiveAgentModels(),
      ])
      const provider = ["openai", "ollama", "openrouter", "gemini"].includes(models.provider)
        ? models.provider as AIModelSettings["provider"]
        : configuration.provider
      const resolvedConfiguration = {
        ...configuration,
        provider,
        primary_model: models.primary_model,
        writer_model: models.writer_model || null,
        fallback_models: models.fallback_models,
      }
      setSettings(resolvedConfiguration)
      setFallbacks(resolvedConfiguration.fallback_models.join(", "))
      setEffectiveModels(models)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Configuration indisponible.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void load()
  }, [])

  const save = async (event: React.FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError("")
    setMessage("")
    try {
      const updated = await updateAISettings({
        ...settings,
        fallback_models: fallbacks
          .split(",")
          .map((item) => item.trim())
          .filter(Boolean),
      })
      setSettings(updated)
      setFallbacks(updated.fallback_models.join(", "))
      setEffectiveModels(await getEffectiveAgentModels())
      window.dispatchEvent(new CustomEvent(AGENT_AVAILABILITY_CHANGE_EVENT, {
        detail: updated.enabled_agents,
      }))
      setMessage("Configuration appliquée. Les modèles affichés et la visibilité des agents sont à jour.")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Enregistrement impossible.")
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <LoadingState label="Chargement de la configuration système…" />

  return (
    <div className="workspace-page space-y-6">
      <PageHeader
        eyebrow="Super administration"
        title="Modèles & plateforme"
        description="Consultez les modèles réellement chargés et contrôlez la disponibilité des agents."
        icon={ShieldCheck}
      />

      {message && <StatusNotice state="validated" title={message} />}
      {error && <StatusNotice state="failed" title="Configuration indisponible" description={error} />}

      <Card className="border-brand/20 bg-[linear-gradient(135deg,rgba(109,70,178,.045),rgba(255,255,255,.98))]">
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle className="flex items-center gap-2">
                <CheckCircle2 className="size-4 text-success" />
                Modèles effectivement chargés
              </CardTitle>
              <CardDescription className="mt-1">
                Lecture directe du routage utilisé par le code, après fusion du .env, de la configuration runtime et de l’environnement.
              </CardDescription>
            </div>
            {effectiveModels && (
              <Badge variant="outline" className="border-success/25 bg-success/10 text-success">
                Fournisseur : {effectiveModels.provider}
              </Badge>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {!effectiveModels ? (
            <p className="text-sm text-muted-foreground">Résolution des modèles indisponible.</p>
          ) : (
            <div className="grid gap-4 lg:grid-cols-3">
              {Object.entries(effectiveModels.agents).map(([key, agent]) => (
                <div key={key} className="rounded-2xl border border-border/80 bg-background/90 p-4 shadow-sm">
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-sm font-semibold">{agent.label}</p>
                    <Badge variant="secondary" className="font-mono text-[10px]">
                      {agent.primary_model}
                    </Badge>
                  </div>
                  <div className="mt-4 space-y-2.5">
                    {agent.models.map((entry) => (
                      <div key={`${entry.role}-${entry.model}`} className="flex items-start justify-between gap-3 text-xs">
                        <span className="text-muted-foreground">{entry.role}</span>
                        <span className="max-w-[55%] break-words text-right font-mono font-medium text-foreground">
                          {entry.model}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <form onSubmit={save} className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Cpu className="size-4 text-brand" />
                Configuration demandée
              </CardTitle>
              <CardDescription>
                Ces valeurs sont publiées vers le client LLM central après validation. Les cartes ci-dessus indiquent le résultat réellement résolu.
              </CardDescription>
            </CardHeader>
            <CardContent className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-2">
                <Label htmlFor="provider">Fournisseur actif</Label>
                <select
                  id="provider"
                  value={settings.provider}
                  onChange={(event) => setSettings({ ...settings, provider: event.target.value as AIModelSettings["provider"] })}
                  className="h-10 w-full rounded-lg border bg-background px-3 text-sm"
                >
                  <option value="openai">OpenAI</option>
                  <option value="ollama">Ollama local</option>
                  <option value="openrouter">OpenRouter</option>
                  <option value="gemini">Google Gemini</option>
                </select>
              </div>
              <div className="space-y-2">
                <Label htmlFor="primary-model">Modèle principal</Label>
                <Input id="primary-model" value={settings.primary_model} onChange={(event) => setSettings({ ...settings, primary_model: event.target.value })} required />
              </div>
              <div className="space-y-2">
                <Label htmlFor="writer-model">Modèle de rédaction</Label>
                <Input id="writer-model" value={settings.writer_model || ""} onChange={(event) => setSettings({ ...settings, writer_model: event.target.value || null })} placeholder="Même modèle si vide" />
              </div>
              <div className="space-y-2">
                <Label htmlFor="fallback-models">Fallbacks, séparés par une virgule</Label>
                <Input id="fallback-models" value={fallbacks} onChange={(event) => setFallbacks(event.target.value)} placeholder="model-a, model-b" />
              </div>
              <label className="flex items-center justify-between gap-4 rounded-xl border p-4 sm:col-span-2">
                <span>
                  <span className="block text-sm font-medium">Fallback entre fournisseurs</span>
                  <span className="text-xs text-muted-foreground">Autoriser un fournisseur secondaire si le principal échoue.</span>
                </span>
                <input type="checkbox" checked={settings.allow_cross_provider_fallback} onChange={(event) => setSettings({ ...settings, allow_cross_provider_fallback: event.target.checked })} className="size-4 accent-violet-700" />
              </label>
            </CardContent>
          </Card>

        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Bot className="size-4 text-brand" />
                Agents disponibles
              </CardTitle>
              <CardDescription>Une case décochée masque l’agent dans la navigation et les actions de toute la plateforme.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {Object.entries(settings.enabled_agents).map(([key, enabled]) => (
                <label key={key} className={`flex items-center justify-between rounded-xl border p-3 transition ${enabled ? "border-success/20 bg-success/[0.035]" : "bg-muted/40 text-muted-foreground"}`}>
                  <span>
                    <span className="block text-sm font-medium">{agentLabels[key] || key}</span>
                    <span className="text-[11px]">{enabled ? "Visible et accessible" : "Masqué et bloqué"}</span>
                  </span>
                  <input type="checkbox" checked={enabled} onChange={(event) => setSettings({ ...settings, enabled_agents: { ...settings.enabled_agents, [key]: event.target.checked } })} className="size-4 accent-violet-700" />
                </label>
              ))}
            </CardContent>
          </Card>

          <Button type="submit" className="h-11 w-full" disabled={saving}>
            {saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}
            {saving ? "Application…" : "Appliquer la configuration"}
          </Button>

        </div>
      </form>
    </div>
  )
}

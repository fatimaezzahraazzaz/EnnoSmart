"use client"

import { useEffect, useState } from "react"
import { Activity, Bot, Cpu, Gauge, Loader2, Save, ShieldCheck } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { getAdminAuditLog, getAISettings, updateAISettings, type AIModelSettings } from "@/lib/api"
import { LoadingState, PageHeader, StatusNotice } from "@/components/ennosmart/workspace-ui"

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
  const [fallbacks, setFallbacks] = useState("")
  const [logs, setLogs] = useState<Array<{ id: number; action: string; entity_type: string; entity_id: string | null; created_at: string }>>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")

  const load = async () => {
    setLoading(true); setError("")
    try {
      const [configuration, audit] = await Promise.all([getAISettings(), getAdminAuditLog()])
      setSettings(configuration); setFallbacks(configuration.fallback_models.join(", ")); setLogs(audit)
    } catch (err) { setError(err instanceof Error ? err.message : "Configuration indisponible.") } finally { setLoading(false) }
  }

  useEffect(() => { void load() }, [])

  const save = async (event: React.FormEvent) => {
    event.preventDefault(); setSaving(true); setError(""); setMessage("")
    try {
      const updated = await updateAISettings({ ...settings, fallback_models: fallbacks.split(",").map((item) => item.trim()).filter(Boolean) })
      setSettings(updated); setFallbacks(updated.fallback_models.join(", ")); setMessage("Configuration enregistrée et appliquée aux prochains appels IA.");
      setLogs(await getAdminAuditLog())
    } catch (err) { setError(err instanceof Error ? err.message : "Enregistrement impossible.") } finally { setSaving(false) }
  }

  if (loading) return <LoadingState label="Chargement de la configuration système…" />

  return (
    <div className="workspace-page space-y-6">
      <PageHeader eyebrow="Super administration" title="Modèles & plateforme" description="Configurez le routage IA global sans exposer les clés API." icon={ShieldCheck} />
      {message && <StatusNotice state="validated" title={message} />}
      {error && <StatusNotice state="failed" title="Configuration indisponible" description={error} />}

      <form onSubmit={save} className="grid gap-6 lg:grid-cols-[1fr_340px]">
        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Cpu className="size-4 text-brand" />Routage des modèles</CardTitle><CardDescription>La configuration est persistée en base et rechargée par le client LLM central.</CardDescription></CardHeader>
            <CardContent className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-2"><Label htmlFor="provider">Fournisseur actif</Label><select id="provider" value={settings.provider} onChange={(e) => setSettings({ ...settings, provider: e.target.value as AIModelSettings["provider"] })} className="h-10 w-full rounded-lg border bg-background px-3 text-sm"><option value="openai">OpenAI</option><option value="ollama">Ollama local</option><option value="openrouter">OpenRouter</option><option value="gemini">Google Gemini</option></select></div>
              <div className="space-y-2"><Label htmlFor="primary-model">Modèle principal</Label><Input id="primary-model" value={settings.primary_model} onChange={(e) => setSettings({ ...settings, primary_model: e.target.value })} required /></div>
              <div className="space-y-2"><Label htmlFor="writer-model">Modèle de rédaction</Label><Input id="writer-model" value={settings.writer_model || ""} onChange={(e) => setSettings({ ...settings, writer_model: e.target.value || null })} placeholder="Même modèle si vide" /></div>
              <div className="space-y-2"><Label htmlFor="fallback-models">Fallbacks, séparés par une virgule</Label><Input id="fallback-models" value={fallbacks} onChange={(e) => setFallbacks(e.target.value)} placeholder="model-a, model-b" /></div>
              <label className="flex items-center justify-between gap-4 rounded-xl border p-4 sm:col-span-2"><span><span className="block text-sm font-medium">Fallback entre fournisseurs</span><span className="text-xs text-muted-foreground">Autoriser un fournisseur secondaire si le principal échoue.</span></span><input type="checkbox" checked={settings.allow_cross_provider_fallback} onChange={(e) => setSettings({ ...settings, allow_cross_provider_fallback: e.target.checked })} className="size-4 accent-violet-700" /></label>
            </CardContent>
          </Card>

          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Gauge className="size-4 text-brand" />Garde-fous</CardTitle><CardDescription>Les plafonds réduisent les requêtes excessives sans augmenter celles définies par les agents.</CardDescription></CardHeader>
            <CardContent className="grid gap-5 sm:grid-cols-2">
              <div className="space-y-2"><Label>Température globale ({settings.default_temperature})</Label><input type="range" min={0} max={2} step={0.05} value={settings.default_temperature} onChange={(e) => setSettings({ ...settings, default_temperature: Number(e.target.value) })} className="mt-3 w-full accent-violet-700" /></div>
              <div className="space-y-2"><Label htmlFor="tokens-cap">Plafond de tokens de sortie</Label><Input id="tokens-cap" type="number" min={256} max={200000} value={settings.max_output_tokens_cap} onChange={(e) => setSettings({ ...settings, max_output_tokens_cap: Number(e.target.value) })} /></div>
              <div className="space-y-2"><Label htmlFor="prompt-cap">Contexte standard (caractères)</Label><Input id="prompt-cap" type="number" value={settings.max_prompt_chars} onChange={(e) => setSettings({ ...settings, max_prompt_chars: Number(e.target.value) })} /></div>
              <div className="space-y-2"><Label htmlFor="writer-prompt-cap">Contexte rédaction (caractères)</Label><Input id="writer-prompt-cap" type="number" value={settings.writer_max_prompt_chars} onChange={(e) => setSettings({ ...settings, writer_max_prompt_chars: Number(e.target.value) })} /></div>
              <div className="space-y-2"><Label htmlFor="budget">Budget mensuel indicatif (€)</Label><Input id="budget" type="number" min={0} value={settings.monthly_budget_eur} onChange={(e) => setSettings({ ...settings, monthly_budget_eur: Number(e.target.value) })} /></div>
            </CardContent>
          </Card>
        </div>

        <div className="space-y-6">
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Bot className="size-4 text-brand" />Agents disponibles</CardTitle><CardDescription>Contrôles fonctionnels persistés pour la plateforme.</CardDescription></CardHeader>
            <CardContent className="space-y-3">{Object.entries(settings.enabled_agents).map(([key, enabled]) => <label key={key} className="flex items-center justify-between rounded-xl border p-3"><span className="text-sm font-medium">{agentLabels[key] || key}</span><input type="checkbox" checked={enabled} onChange={(e) => setSettings({ ...settings, enabled_agents: { ...settings.enabled_agents, [key]: e.target.checked } })} className="size-4 accent-violet-700" /></label>)}</CardContent>
          </Card>
          <Button type="submit" className="h-11 w-full" disabled={saving}>{saving ? <Loader2 className="size-4 animate-spin" /> : <Save className="size-4" />}Appliquer la configuration</Button>
          <Card>
            <CardHeader><CardTitle className="flex items-center gap-2"><Activity className="size-4 text-brand" />Journal récent</CardTitle></CardHeader>
            <CardContent className="max-h-[360px] space-y-3 overflow-auto">{logs.length === 0 ? <p className="text-sm text-muted-foreground">Aucune action enregistrée.</p> : logs.slice(0, 20).map((log) => <div key={log.id} className="border-b pb-3 last:border-0"><div className="flex items-center justify-between gap-2"><Badge variant="secondary">{log.entity_type}</Badge><span className="text-[11px] text-muted-foreground">{new Date(log.created_at).toLocaleString("fr-FR")}</span></div><p className="mt-1 text-xs font-medium">{log.action}</p></div>)}</CardContent>
          </Card>
        </div>
      </form>
    </div>
  )
}

"use client"

import { useEffect, useMemo, useState } from "react"
import type React from "react"
import {
  AlertCircle,
  CheckCircle2,
  Copy,
  FileText,
  Layers3,
  Loader2,
  RefreshCw,
  Sparkles,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  getLatestStateOfArt,
  runFullStateOfArt,
  type StateOfArtGenerationOptions,
  type StateOfArtLatestResponse,
  type StateOfArtView,
} from "@/lib/api"

type Props = {
  projectId: number
  initialLatest?: StateOfArtLatestResponse | null
  onGenerated?: (result: unknown) => void
  onReadyForStateOfArt?: () => void
}

type StateOfArtLooseView = (StateOfArtView & Record<string, any>) | null

function pickView(value: any): StateOfArtLooseView {
  if (!value) return null
  return (
    value.state_of_art_view ||
    value.report ||
    value?.data?.state_of_art_view ||
    value?.data?.report ||
    null
  )
}

function asBoolLabel(value: any) {
  return value ? "Oui" : "Non"
}

function n(value: any, fallback = "—") {
  if (value === null || value === undefined || value === "") return fallback
  return String(value)
}

function statusBadge(ok: any, labelOk = "OK", labelKo = "À vérifier") {
  return ok ? (
    <Badge variant="outline" className="border-emerald-500/30 bg-emerald-500/10 text-emerald-700">
      <CheckCircle2 className="mr-1 size-3" />
      {labelOk}
    </Badge>
  ) : (
    <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-amber-700">
      <AlertCircle className="mr-1 size-3" />
      {labelKo}
    </Badge>
  )
}

function copyText(text: string) {
  if (typeof navigator !== "undefined" && navigator.clipboard) {
    navigator.clipboard.writeText(text || "").catch(() => undefined)
  }
}

function MetricCard({
  icon,
  label,
  value,
  hint,
  ok,
}: {
  icon: React.ReactNode
  label: string
  value: React.ReactNode
  hint?: string
  ok?: boolean
}) {
  return (
    <Card className="overflow-hidden border-border/80 bg-gradient-to-br from-background to-muted/30">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1">
            <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{label}</p>
            <div className="text-2xl font-semibold text-foreground">{value}</div>
            {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
          </div>
          <div className={`rounded-xl border p-2 ${ok ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700" : "border-border bg-background text-muted-foreground"}`}>
            {icon}
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function MarkdownViewer({ markdown }: { markdown: string }) {
  const blocks = useMemo(() => markdown.split(/\n/), [markdown])

  if (!markdown?.trim()) {
    return (
      <Card>
        <CardContent className="p-8 text-center text-sm text-muted-foreground">
          Aucun markdown généré pour le moment.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="rounded-2xl border bg-white p-6 shadow-sm">
      <div className="mx-auto max-w-5xl space-y-2 text-[15px] leading-8 text-slate-800">
        {blocks.map((line, index) => {
          const text = line.trim()
          if (!text) return <div key={index} className="h-2" />

          if (text.startsWith("# ")) {
            return (
              <h1 key={index} className="mb-4 mt-2 text-3xl font-bold tracking-tight text-slate-950">
                {text.replace(/^#\s+/, "")}
              </h1>
            )
          }
          if (text.startsWith("## ")) {
            return (
              <h2 key={index} className="mb-3 mt-8 border-b pb-2 text-2xl font-semibold tracking-tight text-slate-950">
                {text.replace(/^##\s+/, "")}
              </h2>
            )
          }
          if (text.startsWith("### ")) {
            return (
              <h3 key={index} className="mb-2 mt-6 rounded-xl bg-slate-50 px-4 py-3 text-lg font-semibold text-slate-900">
                {text.replace(/^###\s+/, "")}
              </h3>
            )
          }
          if (text.startsWith("- ")) {
            return (
              <div key={index} className="ml-3 rounded-lg border-l-2 border-slate-200 pl-4 text-sm leading-7">
                {renderCitations(text.replace(/^[-]\s+/, "• "))}
              </div>
            )
          }
          return (
            <p key={index} className="text-justify">
              {renderCitations(text)}
            </p>
          )
        })}
      </div>
    </div>
  )
}

function renderCitations(text: string) {
  const parts = text.split(/(\[A\d+\])/g)
  return parts.map((part, idx) => {
    if (/^\[A\d+\]$/.test(part)) {
      return (
        <span key={idx} className="mx-0.5 rounded-md bg-blue-50 px-1.5 py-0.5 text-xs font-semibold text-blue-700">
          {part}
        </span>
      )
    }
    return <span key={idx}>{part}</span>
  })
}

function VerrousCoverage({ view }: { view: StateOfArtLooseView }) {
  const verrous = view?.verrous || []

  if (!verrous.length) {
    return (
      <Card>
        <CardContent className="p-6 text-sm text-muted-foreground">
          Aucun détail de couverture par verrou trouvé dans le payload.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="grid gap-3">
      {verrous.map((v, idx) => (
        <Card key={`${v.verrou_id || idx}-${idx}`} className="border-border/80">
          <CardContent className="p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  {statusBadge(v.ok, "Couverture OK", "Manque citations")}
                  <Badge variant="outline">Verrou {v.index ?? idx + 1}</Badge>
                  <Badge variant="outline">{v.detected_count ?? v.detected_citations?.length ?? 0}/{v.required_count ?? v.required_citations?.length ?? 0} citations</Badge>
                </div>
                <h3 className="font-semibold text-foreground">{v.verrou_title || `Verrou ${idx + 1}`}</h3>
              </div>
              {Array.isArray(v.missing_citations) && v.missing_citations.length > 0 && (
                <div className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                  Manquantes : {v.missing_citations.join(", ")}
                </div>
              )}
            </div>
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(v.detected_citations || v.required_citations || []).map((c: string) => (
                <span key={c} className="rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground">
                  [{c.replace(/[\[\]]/g, "")}]
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  )
}

export default function EnnoScholarStateOfArtWorkspace({ projectId, initialLatest, onGenerated, onReadyForStateOfArt }: Props) {
  const [latest, setLatest] = useState<StateOfArtLatestResponse | null>(initialLatest || null)
  const [loading, setLoading] = useState(!initialLatest)
  const [generating, setGenerating] = useState(false)
  const [error, setError] = useState("")

  const view = useMemo(() => pickView(latest), [latest])
  const markdown = latest?.markdown || view?.markdown || ""
  const summary: any = view?.summary || {}
  const citationsDetected = Number(summary.citations_detected_count ?? 0)
  const coverageRequired = Number(summary.coverage_required_count ?? 0)
  const verrouSectionsCount = Number(summary.verrou_sections_count ?? summary.verrous_written ?? 0)

  const loadLatest = async () => {
    if (!projectId) return
    setLoading(true)
    setError("")
    try {
      const data = await getLatestStateOfArt(projectId)
      setLatest(data)
    } catch (err: any) {
      setError(err?.message || "Impossible de charger le dernier état de l’art.")
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!initialLatest) loadLatest()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const launchGeneration = async () => {
    setGenerating(true)
    setError("")
    const options: StateOfArtGenerationOptions = {
      // Les phases 1 à 3 déjà préparées restent en lecture seule. Le modèle
      // et le fournisseur viennent exclusivement du client central et du .env.
      forcePhase3: false,
      forceArticleCards: false,
    }

    try {
      const result = await runFullStateOfArt(projectId, options)
      const normalized: StateOfArtLatestResponse = {
        ok: Boolean(result?.ok),
        report: result?.state_of_art_view || result?.report,
        state_of_art_view: result?.state_of_art_view || result?.report,
        markdown: result?.markdown || result?.state_of_art_view?.markdown || "",
        payload: result?.state_of_art_view?.raw_payload || result?.payload,
        paths: result?.paths,
      }
      setLatest(normalized)
      onGenerated?.(result)
      onReadyForStateOfArt?.()
    } catch (err: any) {
      setError(err?.message || "Impossible de générer l’état de l’art.")
    } finally {
      setGenerating(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="flex items-center justify-center gap-3 p-8 text-muted-foreground">
          <Loader2 className="size-5 animate-spin" />
          Chargement du dernier état de l’art.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-6">
      <Card className="overflow-hidden border-brand/20 bg-gradient-to-br from-brand/10 via-background to-blue-500/10">
        <CardHeader className="pb-4">
          <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
            <div className="space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge className="bg-brand text-white">EnnoScholar</Badge>
                {statusBadge(view?.ok, "Markdown disponible", "Pas encore généré")}
                {statusBadge(summary.llm_used_in_final, "Rédaction enrichie", "Draft standard")}
              </div>
              <CardTitle className="text-2xl">État de l’art CIR — génération & livrable</CardTitle>
              <CardDescription className="max-w-3xl">
                Lance la génération finale depuis l’interface, puis consulte le markdown validé, la couverture des citations et la couverture des verrous.
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" onClick={loadLatest} disabled={loading || generating}>
                <RefreshCw className="mr-2 size-4" />
                Recharger
              </Button>
              <Button onClick={() => copyText(markdown)} variant="outline" disabled={!markdown}>
                <Copy className="mr-2 size-4" />
                Copier markdown
              </Button>
              <Button onClick={launchGeneration} disabled={generating} className="bg-brand hover:bg-brand/90">
                {generating ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Sparkles className="mr-2 size-4" />}
                {generating ? "Génération en cours..." : "Lancer la génération"}
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          <div className="rounded-2xl border bg-background/80 p-4">
            <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-medium text-foreground">Génération automatique</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Les paramètres techniques sont gérés automatiquement par le système. Le consultant lance la génération et consulte uniquement le livrable contrôlé.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Badge variant="outline">État de l’art final</Badge>
                <Badge variant="outline">Citations contrôlées</Badge>
                <Badge variant="outline">Verrous contrôlés</Badge>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="flex items-start gap-3 p-4 text-destructive">
            <AlertCircle className="mt-0.5 size-5" />
            <div>
              <p className="font-medium">Erreur</p>
              <p className="text-sm">{error}</p>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <MetricCard
          icon={<FileText className="size-5" />}
          label="Citations"
          value={`${n(citationsDetected)} / ${n(coverageRequired)}`}
          hint="Détectées / requises"
          ok={coverageRequired > 0 && citationsDetected === coverageRequired}
        />
        <MetricCard
          icon={<Layers3 className="size-5" />}
          label="Verrous"
          value={`${n(verrouSectionsCount)} / 7`}
          hint="Couverture par verrou"
          ok={summary.verrou_coverage_ok}
        />
      </div>

      <Tabs defaultValue="markdown" className="space-y-4">
        <TabsList className="grid h-auto grid-cols-2">
          <TabsTrigger value="markdown">Etat de L'art</TabsTrigger>
          <TabsTrigger value="verrous">Couverture verrous</TabsTrigger>
        </TabsList>

        <TabsContent value="markdown">
          <MarkdownViewer markdown={markdown} />
        </TabsContent>

        <TabsContent value="verrous">
          <VerrousCoverage view={view} />
        </TabsContent>

      </Tabs>
    </div>
  )
}

export { EnnoScholarStateOfArtWorkspace }

"use client"

import { useEffect, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { Accordion, AccordionContent, AccordionItem, AccordionTrigger } from "@/components/ui/accordion"
import { getAccessToken } from "@/lib/api"

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"

function formatScore(score: number | string | null | undefined) {
  if (score === null || score === undefined || score === "") return "—"
  const value = Number(score)
  if (!Number.isFinite(value)) return "—"
  const normalized = value <= 1 ? value * 100 : value
  return `${Math.round(normalized)}%`
}

function tagClass(tag: string | null | undefined) {
  const value = String(tag || "").toLowerCase()
  if (value.includes("direct")) return "bg-success/10 text-success border-success/30"
  if (value.includes("connexe")) return "bg-warning/10 text-warning border-warning/30"
  return "bg-muted text-muted-foreground border-border"
}

async function apiJson(path: string, init?: RequestInit) {
  const token = getAccessToken()
  if (!token) throw new Error("Utilisateur non authentifié.")
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...(init || {}),
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : "Erreur API")
  return data
}

export function EnnoScholarStateOfArtPanel({ projectId }: { projectId: number }) {
  const [articles, setArticles] = useState<any[]>([])
  const [preview, setPreview] = useState<any>(null)
  const [report, setReport] = useState<any>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  const load = async () => {
    setError("")
    const [articlesData, previewData, latestData] = await Promise.all([
      apiJson(`/projects/${projectId}/articles`).catch(() => []),
      apiJson(`/projects/${projectId}/scholar/state-of-art/selection-preview`).catch(() => null),
      apiJson(`/projects/${projectId}/scholar/state-of-art/latest`).catch(() => null),
    ])
    setArticles(Array.isArray(articlesData) ? articlesData : [])
    setPreview(previewData)
    setReport(latestData?.report || null)
  }

  useEffect(() => {
    if (projectId) load().catch((err) => setError(err instanceof Error ? err.message : String(err)))
  }, [projectId])

  const keptCount = useMemo(() => articles.filter((a) => a.consultant_status === "garde").length, [articles])

  const updateArticle = async (articleId: number, status: "garde" | "rejete" | "en_attente") => {
    setLoading(true)
    try {
      await apiJson(`/projects/${projectId}/articles/${articleId}/decision`, {
        method: "PATCH",
        body: JSON.stringify({ consultant_status: status }),
      })
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const writeStateOfArt = async (writerMode: "template" | "auto" | "llm") => {
    setLoading(true)
    setError("")
    try {
      const data = await apiJson(`/projects/${projectId}/scholar/state-of-art/write?writer_mode=${writerMode}&force=false`, {
        method: "POST",
      })
      setReport(data)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      {error && <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">{error}</div>}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Sélection des articles pour l’état de l’art</CardTitle>
          <CardDescription className="text-xs">
            Garde uniquement les articles Direct ou Connexe vraiment utiles. Les articles Fondamentaux seuls ne suffisent pas pour un état de l’art CIR solide.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">Articles synchronisés</p>
              <p className="text-2xl font-bold">{articles.length}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">Articles gardés</p>
              <p className="text-2xl font-bold">{keptCount}</p>
            </div>
            <div className="rounded-md border p-3">
              <p className="text-xs text-muted-foreground">Verrous prêts</p>
              <p className="text-2xl font-bold">{preview?.summary?.verrous_ready ?? 0}</p>
            </div>
          </div>

          {articles.length === 0 ? (
            <p className="text-sm text-muted-foreground">Aucun article synchronisé. Lance EnnoScholar puis synchronise les articles.</p>
          ) : (
            <div className="space-y-2">
              {articles.map((article) => (
                <div key={article.id} className="rounded-md border p-3">
                  <div className="flex items-start gap-3">
                    <Checkbox
                      checked={article.consultant_status === "garde"}
                      disabled={loading}
                      onCheckedChange={(checked: boolean) => updateArticle(article.id, checked ? "garde" : "en_attente")}
                    />
                    <div className="flex-1">
                      <div className="flex gap-2 flex-wrap mb-1">
                        <Badge variant="outline" className={tagClass(article.tag_article)}>{article.tag_article || "Article"}</Badge>
                        <Badge variant="outline" className="text-xs">Score {formatScore(article.score)}</Badge>
                        {article.year && <Badge variant="outline" className="text-xs">{article.year}</Badge>}
                        <Badge variant="outline" className="text-xs">Verrou {article.verrou_id || "—"}</Badge>
                      </div>
                      <p className="text-sm font-medium">{article.title}</p>
                      {article.url && <a className="text-xs text-brand underline" href={article.url} target="_blank" rel="noreferrer">Ouvrir l’article</a>}
                    </div>
                    <Button variant="ghost" size="sm" onClick={() => updateArticle(article.id, "rejete")} disabled={loading}>
                      Rejeter
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Rédaction contrôlée de l’état de l’art</CardTitle>
          <CardDescription className="text-xs">
            Rédaction par verrou avec citations [A1], [A2]. Utilise template pour tester, auto pour LLM si disponible.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2 flex-wrap">
            <Button onClick={() => writeStateOfArt("template")} disabled={loading || keptCount === 0}>Rédiger sans LLM</Button>
            <Button variant="secondary" onClick={() => writeStateOfArt("auto")} disabled={loading || keptCount === 0}>Rédiger avec LLM auto</Button>
          </div>

          {preview?.verrous?.length > 0 && (
            <Accordion>
              <AccordionItem value="readiness">
                <AccordionTrigger>Contrôle avant rédaction</AccordionTrigger>
                <AccordionContent className="space-y-2">
                  {preview.verrous.map((v: any, i: number) => (
                    <div key={i} className="rounded-md border p-2">
                      <p className="text-sm font-medium">{v.verrou_title || `Verrou ${i + 1}`}</p>
                      <p className="text-xs text-muted-foreground">Articles gardés : {v.selected_articles_count} | Direct/Connexe : {v.direct_connexe_count}</p>
                      <p className="text-xs">{v.readiness_reason}</p>
                    </div>
                  ))}
                </AccordionContent>
              </AccordionItem>
            </Accordion>
          )}
        </CardContent>
      </Card>

      {report?.results?.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">État de l’art généré</CardTitle>
            <CardDescription className="text-xs">Résultat prêt à relire par le consultant.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {report.results.map((r: any, i: number) => {
              const soa = r.state_of_art || {}
              return (
                <div key={i} className="rounded-md border p-4">
                  <p className="text-sm font-semibold mb-2">V{i + 1} — {r.verrou_title}</p>
                  <div className="prose prose-sm max-w-none whitespace-pre-wrap">{soa.draft || "Aucun texte généré."}</div>
                  {soa.references?.length > 0 && (
                    <Accordion className="mt-3">
                      <AccordionItem value="refs">
                        <AccordionTrigger>Références</AccordionTrigger>
                        <AccordionContent>
                          {soa.references.map((ref: any) => (
                            <p key={ref.token} className="text-xs mb-2">{ref.token} — {ref.label} — {ref.title}</p>
                          ))}
                        </AccordionContent>
                      </AccordionItem>
                    </Accordion>
                  )}
                </div>
              )
            })}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

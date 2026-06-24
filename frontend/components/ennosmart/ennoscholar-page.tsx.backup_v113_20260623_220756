"use client"

import { useEffect, useMemo, useState } from "react"
import {
  AlertCircle,
  BookOpen,
  CheckCircle2,
  ExternalLink,
  Eye,
  EyeOff,
  FileText,
  Loader2,
  RefreshCw,
  Search,
  XCircle,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"

import {
  getArticles,
  getProjects,
  getScholarLatest,
  updateArticleDecision,
  type ArticleRead,
  type ProjectRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { EnnoScholarStructuredStateArtPanel } from "./ennoscholar-structured-state-of-art-panel"

type ArticleDecision = "garde" | "rejete" | "en_attente"

function formatScore(score: number | null) {
  if (score === null || score === undefined) return "—"

  const normalized = score <= 1 ? score * 100 : score
  return `${Math.round(normalized)}%`
}

function normalizeTag(tag: string | null) {
  const value = (tag || "Non classé").trim()

  if (value.toLowerCase().includes("direct")) return "Direct"
  if (value.toLowerCase().includes("fondamental")) return "Fondamental"
  if (value.toLowerCase().includes("connexe")) return "Connexe"
  if (value.toLowerCase().includes("hors")) return "Hors sujet"

  return value
}

function tagClass(tag: string | null) {
  const value = normalizeTag(tag)

  switch (value) {
    case "Direct":
      return "bg-success/10 text-success border-success/30"
    case "Connexe":
      return "bg-brand/10 text-brand border-brand/30"
    case "Fondamental":
      return "bg-blue-500/10 text-blue-700 border-blue-500/30"
    case "Hors sujet":
      return "bg-muted text-muted-foreground border-border"
    default:
      return "bg-warning/10 text-warning border-warning/30"
  }
}

function decisionClass(status: string) {
  switch (status) {
    case "garde":
      return "bg-success/10 text-success border-success/30"
    case "rejete":
      return "bg-destructive/10 text-destructive border-destructive/30"
    default:
      return "bg-muted text-muted-foreground border-border"
  }
}

function decisionLabel(status: string) {
  switch (status) {
    case "garde":
      return "Gardé"
    case "rejete":
      return "Rejeté"
    default:
      return "En attente"
  }
}

function getArticleReason(article: ArticleRead) {
  return (
    article.source_json?.reason ||
    article.source_json?.alignment_reason ||
    article.source_json?.justification ||
    article.source_json?.tag_consultant ||
    "Aucune justification disponible."
  )
}

function getArticleAbstract(article: ArticleRead) {
  const abstract =
    article.source_json?.abstract ||
    article.source_json?.summary ||
    article.source_json?.tldr?.text ||
    ""

  if (!abstract) return "Aucun résumé disponible."

  return abstract.length > 600 ? `${abstract.slice(0, 600)}...` : abstract
}

function getAuthors(article: ArticleRead) {
  const authors = article.source_json?.authors

  if (!Array.isArray(authors)) return ""

  const names = authors
    .map((author: any) => {
      if (typeof author === "string") return author
      return author?.name || author?.author_name
    })
    .filter(Boolean)

  if (names.length === 0) return ""

  return names.slice(0, 4).join(", ") + (names.length > 4 ? " et al." : "")
}

function groupArticles(articles: ArticleRead[]) {
  return {
    direct: articles.filter((article) => normalizeTag(article.tag_article) === "Direct"),
    fondamental: articles.filter((article) => normalizeTag(article.tag_article) === "Fondamental"),
    connexe: articles.filter((article) => normalizeTag(article.tag_article) === "Connexe"),
    horsSujet: articles.filter((article) => normalizeTag(article.tag_article) === "Hors sujet"),
    autres: articles.filter((article) => {
      const tag = normalizeTag(article.tag_article)
      return !["Direct", "Fondamental", "Connexe", "Hors sujet"].includes(tag)
    }),
  }
}

function filterArticles(articles: ArticleRead[], query: string) {
  const search = query.trim().toLowerCase()

  if (!search) return articles

  return articles.filter((article) => {
    const haystack = [
      article.title,
      article.source,
      article.doi,
      article.year,
      article.tag_article,
      article.source_json?.abstract,
      article.source_json?.query,
      article.source_json?.reason,
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase()

    return haystack.includes(search)
  })
}

function sortArticles(articles: ArticleRead[]) {
  return [...articles].sort((a, b) => {
    const scoreA = a.score ?? 0
    const scoreB = b.score ?? 0

    if (scoreB !== scoreA) return scoreB - scoreA

    const yearA = a.year ?? 0
    const yearB = b.year ?? 0

    return yearB - yearA
  })
}

function ArticleCard({
  article,
  projectId,
  onUpdated,
}: {
  article: ArticleRead
  projectId: number
  onUpdated: (article: ArticleRead) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [loading, setLoading] = useState(false)

  const updateDecision = async (decision: ArticleDecision) => {
    setLoading(true)

    try {
      const updated = await updateArticleDecision(projectId, article.id, decision)
      onUpdated(updated)
    } finally {
      setLoading(false)
    }
  }

  const authors = getAuthors(article)

  return (
    <Card className="border border-border hover-lift">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div className="space-y-1 flex-1 min-w-0">
            <p className="text-sm font-semibold text-foreground leading-relaxed">
              {article.title}
            </p>

            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className={`text-xs ${tagClass(article.tag_article)}`}>
                {normalizeTag(article.tag_article)}
              </Badge>

              <Badge variant="outline" className="text-xs">
                Score {formatScore(article.score)}
              </Badge>

              {article.year && (
                <Badge variant="outline" className="text-xs">
                  {article.year}
                </Badge>
              )}

              {article.source && (
                <Badge variant="outline" className="text-xs">
                  {article.source}
                </Badge>
              )}

              <Badge
                variant="outline"
                className={`text-xs ${decisionClass(article.consultant_status)}`}
              >
                {decisionLabel(article.consultant_status)}
              </Badge>
            </div>
          </div>

          {article.url && (
            <a
              href={article.url}
              target="_blank"
              rel="noreferrer"
              className="size-8 rounded-md border border-border flex items-center justify-center text-muted-foreground hover:text-brand hover:bg-brand/10 transition-colors"
              title="Ouvrir la source"
            >
              <ExternalLink className="size-4" />
            </a>
          )}
        </div>

        {authors && (
          <p className="text-xs text-muted-foreground">
            Auteurs : {authors}
          </p>
        )}

        {article.doi && (
          <p className="text-xs text-muted-foreground break-all">
            DOI : {article.doi}
          </p>
        )}

        <div className="p-3 rounded-md bg-muted/40 border border-border">
          <p className="text-xs font-medium text-muted-foreground mb-1">
            Analyse EnnoScholar
          </p>
          <p className="text-sm text-foreground">
            {getArticleReason(article)}
          </p>
        </div>

        {expanded && (
          <div className="p-3 rounded-md bg-white border border-border">
            <p className="text-xs font-medium text-muted-foreground mb-1">
              Résumé / Abstract
            </p>
            <p className="text-sm text-foreground whitespace-pre-wrap">
              {getArticleAbstract(article)}
            </p>
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            size="sm"
            className="text-xs h-8 bg-brand hover:bg-brand/90"
            disabled={loading}
            onClick={() => updateDecision("garde")}
          >
            <CheckCircle2 className="size-3 mr-1" />
            Garder
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="text-xs h-8 text-destructive border-destructive/30 hover:bg-destructive/10"
            disabled={loading}
            onClick={() => updateDecision("rejete")}
          >
            <XCircle className="size-3 mr-1" />
            Rejeter
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="text-xs h-8"
            disabled={loading}
            onClick={() => updateDecision("en_attente")}
          >
            Remettre en attente
          </Button>

          <Button
            size="sm"
            variant="ghost"
            className="text-xs h-8"
            onClick={() => setExpanded((prev) => !prev)}
          >
            {expanded ? (
              <>
                <EyeOff className="size-3 mr-1" />
                Masquer résumé
              </>
            ) : (
              <>
                <Eye className="size-3 mr-1" />
                Voir résumé
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

function ArticleSection({
  title,
  description,
  articles,
  projectId,
  onUpdated,
}: {
  title: string
  description: string
  articles: ArticleRead[]
  projectId: number
  onUpdated: (article: ArticleRead) => void
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm flex items-center gap-2">
          <FileText className="size-4 text-brand" />
          {title}
          <Badge variant="outline" className="ml-1">
            {articles.length}
          </Badge>
        </CardTitle>
        <CardDescription className="text-xs">
          {description}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-3">
        {articles.length === 0 ? (
          <div className="p-6 text-center border border-dashed rounded-lg">
            <p className="text-sm font-medium text-foreground">
              Aucun article dans cette catégorie.
            </p>
          </div>
        ) : (
          articles.map((article) => (
            <ArticleCard
              key={article.id}
              article={article}
              projectId={projectId}
              onUpdated={onUpdated}
            />
          ))
        )}
      </CardContent>
    </Card>
  )
}

// V46_AGENT2_GROUPED_BY_VERROU

function v46Text(value: any, fallback = ""): string {
  if (value === null || value === undefined) return fallback
  return String(value).replace(/\s+/g, " ").trim() || fallback
}

function v46Norm(value: any): string {
  return v46Text(value)
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .replace(/[^a-z0-9]+/g, " ")
    .trim()
}

function v46Short(value: any, max = 260): string {
  const s = v46Text(value)
  return s.length > max ? `${s.slice(0, max)}...` : s
}

function getArticleVerrouTitle(article: ArticleRead): string {
  const sj: any = article.source_json || {}

  return v46Short(
    sj?.scientific_intent?.verrou_title ||
      sj?.scientific_intent?.title ||
      sj?.verrou_title ||
      sj?.enriched_title ||
      sj?.scientific_title ||
      sj?.validation?.verrou_title ||
      sj?.result?.verrou_title ||
      sj?.verrou?.title ||
      sj?.verrou_label ||
      sj?.query_context?.verrou_title ||
      (article as any)?.verrou_title ||
      ((article as any)?.verrou_id ? `Verrou lié ${(article as any).verrou_id}` : "") ||
      "Verrou scientifique non identifié",
    300
  )
}

function getArticleOriginalSignal(article: ArticleRead): string {
  const sj: any = article.source_json || {}

  return v46Short(
    sj?.scientific_intent?.original_title ||
      sj?.original_title ||
      sj?.source_signal ||
      sj?.raw_verrou_title ||
      sj?.diagnostic_title ||
      "",
    220
  )
}

function getArticleVerrouKey(article: ArticleRead): string {
  const sj: any = article.source_json || {}
  const explicit =
    sj?.scientific_intent?.verrou_id ||
    sj?.verrou_id ||
    sj?.verrou_key ||
    sj?.group_id ||
    (article as any)?.verrou_id

  if (explicit !== null && explicit !== undefined && String(explicit).trim()) {
    return `id:${String(explicit).trim()}`
  }

  const title = getArticleVerrouTitle(article)
  return `title:${v46Norm(title).slice(0, 160)}`
}

function getArticleUniqueKey(article: ArticleRead): string {
  const sj: any = article.source_json || {}

  const doi = v46Norm(article.doi || sj?.doi)
  if (doi) return `doi:${doi}`

  const url = v46Norm(article.url || sj?.url)
  if (url) return `url:${url}`

  const paperId = v46Text(sj?.paper_id || sj?.paperId || sj?.id || "")
  if (paperId) return `paper:${paperId}`

  return `title:${v46Norm(article.title).slice(0, 180)}:${article.year || ""}`
}

function groupArticlesByScientificVerrou(articles: ArticleRead[]) {
  const groups = new Map<
    string,
    {
      key: string
      title: string
      signals: string[]
      articles: ArticleRead[]
      seenArticles: Set<string>
    }
  >()

  for (const article of articles) {
    const key = getArticleVerrouKey(article)
    const title = getArticleVerrouTitle(article)

    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title,
        signals: [],
        articles: [],
        seenArticles: new Set<string>(),
      })
    }

    const group = groups.get(key)!
    const signal = getArticleOriginalSignal(article)

    if (signal && !group.signals.some((item) => v46Norm(item) === v46Norm(signal))) {
      group.signals.push(signal)
    }

    const articleKey = getArticleUniqueKey(article)
    if (!group.seenArticles.has(articleKey)) {
      group.seenArticles.add(articleKey)
      group.articles.push(article)
    }
  }

  return Array.from(groups.values())
    .map((group) => ({
      ...group,
      signals: group.signals.slice(0, 8),
      articles: sortArticles(group.articles),
    }))
    .sort((a, b) => b.articles.length - a.articles.length)
}



function v48ReadCookie(name: string): string {
  if (typeof document === "undefined") return ""
  const match = document.cookie.match(new RegExp("(^| )" + name + "=([^;]+)"))
  return match ? decodeURIComponent(match[2]) : ""
}

function v48LooksLikeJwt(value: string): boolean {
  return /^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/.test(value.trim())
}

function v48ExtractToken(raw: string): string {
  if (!raw) return ""
  const direct = raw.trim().replace(/^Bearer\s+/i, "")
  if (v48LooksLikeJwt(direct) || direct.length > 30) return direct

  try {
    const parsed = JSON.parse(raw)
    const stack: any[] = [parsed]
    const tokenKeys = [
      "token",
      "access_token",
      "accessToken",
      "auth_token",
      "authToken",
      "jwt",
      "bearer",
      "bearer_token",
      "access",
    ]

    while (stack.length) {
      const obj = stack.pop()
      if (!obj || typeof obj !== "object") continue

      for (const key of tokenKeys) {
        const candidate = obj[key]
        if (typeof candidate === "string") {
          const cleaned = candidate.trim().replace(/^Bearer\s+/i, "")
          if (cleaned && (v48LooksLikeJwt(cleaned) || cleaned.length > 30)) return cleaned
        }
      }

      for (const v of Object.values(obj)) {
        if (typeof v === "object" && v !== null) stack.push(v)
        if (typeof v === "string" && v48LooksLikeJwt(v.trim())) return v.trim()
      }
    }
  } catch {
    // not JSON
  }

  const m = raw.match(/[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/)
  return m?.[0] || ""
}

function v48GetAuthToken(): string {
  if (typeof window === "undefined") return ""

  const keys = [
    "ennosmart_token",
    "ennosmart-auth",
    "ennosmart_auth",
    "auth",
    "auth-storage",
    "user",
    "session",
    "access_token",
    "accessToken",
    "auth_token",
    "authToken",
    "token",
    "jwt",
  ]

  for (const key of keys) {
    const raw = localStorage.getItem(key) || sessionStorage.getItem(key) || v48ReadCookie(key)
    const token = v48ExtractToken(raw || "")
    if (token) return token
  }

  for (const storage of [localStorage, sessionStorage]) {
    for (let i = 0; i < storage.length; i++) {
      const key = storage.key(i)
      if (!key) continue
      const token = v48ExtractToken(storage.getItem(key) || "")
      if (token) return token
    }
  }

  return ""
}

function v48AuthHeaders(): HeadersInit {
  const token = v48GetAuthToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

function isArticleSelectedForStateOfArt(article: ArticleRead): boolean {
  const a: any = article
  const sj: any = a.source_json || {}

  const raw =
    a.consultant_selected ??
    a.selected ??
    a.is_selected ??
    a.isSelected ??
    a.keep ??
    a.kept ??
    sj.consultant_selected ??
    sj.selected ??
    sj.is_selected

  if (raw === true) return true

  const decision = v46Norm(
    a.decision_article ||
      a.consultant_decision ||
      a.selection_status ||
      a.status_article ||
      a.status ||
      a.decision ||
      sj.decision_article ||
      sj.consultant_decision ||
      sj.selection_status ||
      ""
  )

  return [
    "retenu",
    "retu",
    "garde",
    "gardee",
    "gardé",
    "gardée",
    "selected",
    "select",
    "keep",
    "kept",
    "valide",
    "validé",
    "validée",
    "accepted",
  ].some((word) => decision.includes(v46Norm(word)))
}

function articleToStateOfArtPayload(article: ArticleRead) {
  const a: any = article
  const sj: any = a.source_json || {}

  return {
    ...sj,
    title: a.title || sj.title,
    abstract: a.abstract || sj.abstract || sj.tldr || "",
    year: a.year || sj.year,
    doi: a.doi || sj.doi,
    url: a.url || sj.url,
    source: a.source || sj.source,
    authors: a.authors || sj.authors || [],
    tag: normalizeTag(a.tag_article || sj.tag || sj.tag_article),
    tag_article: normalizeTag(a.tag_article || sj.tag || sj.tag_article),
    relevance_score: Number(a.relevance_score ?? sj.relevance_score ?? 0),
    consultant_selected: true,
  }
}

function EnnoScholarByVerrouSection({
  groups,
  projectId,
  onUpdated,
}: {
  groups: ReturnType<typeof groupArticlesByScientificVerrou>
  projectId: number
  onUpdated: (article: ArticleRead) => void
}) {
  const [selectedVerrouKey, setSelectedVerrouKey] = useState<string>("all")
  const [selectedTag, setSelectedTag] = useState<"all" | "Direct" | "Connexe" | "Fondamental" | "Autres">("all")
  const [writerMode, setWriterMode] = useState<"template" | "auto" | "llm">("auto")
  const [writing, setWriting] = useState(false)
  const [writerError, setWriterError] = useState("")
  const [writerResult, setWriterResult] = useState<any | null>(null)
  const [writerPayload, setWriterPayload] = useState<any | null>(null)

  const filteredGroups = selectedVerrouKey === "all"
    ? groups
    : groups.filter((group) => group.key === selectedVerrouKey)

  const selectedVerrouTitle =
    selectedVerrouKey === "all"
      ? "Tous les verrous scientifiques"
      : groups.find((group) => group.key === selectedVerrouKey)?.title || "Verrou sélectionné"

  const writableGroups = filteredGroups
    .map((group) => {
      // V48.1 :
      // Pour le test etat de l'art, on utilise les articles Direct/Connexe du verrou affiche.
      // La selection "Garde" existe deja dans l'onglet Selection consultant, mais selon le backend
      // elle peut etre stockee sous plusieurs champs differents. Ici on evite de bloquer la redaction
      // a 0 alors que les articles sont bien gardes visuellement.
      const selectedArticles = group.articles.filter((article) => {
        const tag = normalizeTag(article.tag_article)
        return tag === "Direct" || tag === "Connexe"
      })

      return { group, selectedArticles }
    })
    .filter((item) => item.selectedArticles.length > 0)

  const selectedDirectConnexeCount = writableGroups.reduce(
    (total, item) => total + item.selectedArticles.length,
    0
  )

  async function launchStateOfArtWriting() {
    setWriting(true)
    setWriterError("")
    setWriterResult(null)
    setWriterPayload(null)

    try {
      if (writableGroups.length === 0) {
        throw new Error("Aucun article Direct/Connexe sélectionné pour la rédaction.")
      }

      const payload = {
        agent: "EnnoScholar",
        payload_type: "selected_articles_for_state_of_art",
        verrous: writableGroups.map(({ group, selectedArticles }) => ({
          verrou_id: group.key,
          verrou_title: group.title,
          scientific_intent: {
            verrou_id: group.key,
            verrou_title: group.title,
            source_signals: group.signals,
          },
          source_signals: group.signals,
          selected_articles: selectedArticles.map(articleToStateOfArtPayload),
        })),
      }

      setWriterPayload(payload)

      const apiBase = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"
      const response = await fetch(
        `${apiBase}/projects/${projectId}/scholar/state-of-art/write-from-selection?writer_mode=${writerMode}`,
        {
          method: "POST",
          credentials: "include",
          headers: {
            "Content-Type": "application/json",
            ...v48AuthHeaders(),
          },
          body: JSON.stringify(payload),
        }
      )

      if (!response.ok) {
        const txt = await response.text()
        throw new Error(txt || "Erreur backend pendant la rédaction de l’état de l’art.")
      }

      const data = await response.json()
      setWriterResult(data)
    } catch (error: any) {
      setWriterError(error?.message || "Erreur pendant la rédaction de l’état de l’art.")
    } finally {
      setWriting(false)
    }
  }

  if (groups.length === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center">
          <p className="text-sm font-medium text-foreground">
            Aucun article trouvé pour ce projet.
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Lance EnnoScholar depuis le diagnostic puis reviens ici.
          </p>
        </CardContent>
      </Card>
    )
  }

  const tagButtons: Array<"all" | "Direct" | "Connexe" | "Fondamental" | "Autres"> = [
    "all",
    "Direct",
    "Connexe",
    "Fondamental",
    "Autres",
  ]

  return (
    <div className="space-y-4">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <BookOpen className="size-4 text-brand" />
            Filtrer les articles par verrou scientifique
          </CardTitle>
          <CardDescription className="text-xs">
            Choisis un verrou, puis filtre ses articles par catégorie : Direct, Connexe ou Fondamental.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          <div className="grid gap-3 lg:grid-cols-[1.6fr_1fr]">
            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Filtre par verrou</p>
              <select
                value={selectedVerrouKey}
                onChange={(event) => setSelectedVerrouKey(event.target.value)}
                className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
              >
                <option value="all">Tous les verrous scientifiques consolidés</option>
                {groups.map((group, index) => (
                  <option key={group.key} value={group.key}>
                    V{index + 1} — {group.title} ({group.articles.length} article(s))
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-2">
              <p className="text-xs font-medium text-muted-foreground">Verrou affiché</p>
              <div className="rounded-md border border-border bg-background px-3 py-2 text-sm font-medium text-foreground">
                {selectedVerrouTitle}
              </div>
            </div>
          </div>

          <div className="space-y-2">
            <p className="text-xs font-medium text-muted-foreground">Filtre par type d’article</p>
            <div className="flex flex-wrap gap-2">
              {tagButtons.map((tag) => {
                const active = selectedTag === tag
                const label = tag === "all" ? "Tous" : tag

                return (
                  <button
                    key={tag}
                    type="button"
                    onClick={() => setSelectedTag(tag)}
                    className={`rounded-full border px-3 py-1 text-xs font-medium transition ${
                      active
                        ? "border-brand bg-brand text-white"
                        : "border-border bg-background text-muted-foreground hover:bg-muted"
                    }`}
                  >
                    {label}
                  </button>
                )
              })}
            </div>
          </div>

          <div className="rounded-md border border-brand/20 bg-background p-3 space-y-3">
            <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  Rédaction de l’état de l’art
                </p>
                <p className="text-xs text-muted-foreground">
                  Articles Direct/Connexe utilisés pour l'état de l'art du verrou affiché : {selectedDirectConnexeCount}
                </p>
              </div>

              <div className="flex flex-wrap items-center gap-2">
                <select
                  value={writerMode}
                  onChange={(event) => setWriterMode(event.target.value as any)}
                  className="rounded-md border border-border bg-background px-2 py-2 text-xs"
                >
                  <option value="template">Template</option>
                  <option value="auto">Auto LLM</option>
                  <option value="llm">LLM</option>
                </select>

                <button
                  type="button"
                  onClick={launchStateOfArtWriting}
                  disabled={writing || selectedDirectConnexeCount === 0}
                  className="rounded-md bg-brand px-4 py-2 text-xs font-semibold text-white disabled:opacity-50"
                >
                  {writing ? "Rédaction..." : "Lancer la rédaction"}
                </button>
              </div>
            </div>

            {writerError && (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                {writerError}
              </div>
            )}

            {writerResult && (
              <div className="space-y-3">
                <div className="rounded-md border border-success/30 bg-success/10 p-2 text-xs text-success">
                  État de l’art généré : {writerResult.verrous_written || writerResult.results?.length || 0} verrou(s).
                </div>

                <EnnoScholarStructuredStateArtPanel
                  projectId={projectId}
                  initialReport={writerResult}
                  selectionPayload={writerPayload}
                  apiBaseUrl={process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000"}
                  authToken={v48GetAuthToken()}
                />
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {filteredGroups.map((group, index) => {
        const direct = group.articles.filter((article) => normalizeTag(article.tag_article) === "Direct")
        const connexe = group.articles.filter((article) => normalizeTag(article.tag_article) === "Connexe")
        const fondamental = group.articles.filter((article) => normalizeTag(article.tag_article) === "Fondamental")
        const autres = group.articles.filter((article) => {
          const tag = normalizeTag(article.tag_article)
          return !["Direct", "Connexe", "Fondamental"].includes(tag)
        })

        const sections = [
          {
            tag: "Direct",
            title: "Articles directs",
            description: "Articles les plus alignés avec ce verrou scientifique.",
            articles: direct,
          },
          {
            tag: "Connexe",
            title: "Articles connexes",
            description: "Articles utiles pour compléter l’état de l’art de ce verrou.",
            articles: connexe,
          },
          {
            tag: "Fondamental",
            title: "Articles fondamentaux",
            description: "Articles de contexte scientifique général pour ce verrou.",
            articles: fondamental,
          },
          {
            tag: "Autres",
            title: "Autres articles",
            description: "Articles rattachés au verrou mais non classés Direct, Connexe ou Fondamental.",
            articles: autres,
          },
        ].filter((section) => selectedTag === "all" || section.tag === selectedTag)

        const visibleCount = sections.reduce((total, section) => total + section.articles.length, 0)
        const realIndex = groups.findIndex((item) => item.key === group.key)

        return (
          <Card key={group.key} className="border border-brand/20">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <BookOpen className="size-4 text-brand" />
                Verrou scientifique consolidé {realIndex >= 0 ? realIndex + 1 : index + 1}
                <Badge variant="outline" className="ml-1">
                  {visibleCount} article(s) affiché(s)
                </Badge>
              </CardTitle>
              <CardDescription className="text-xs">
                <span className="font-medium text-foreground">Nom du verrou : </span>
                {group.title}
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              {group.signals.length > 0 && (
                <div className="p-3 rounded-md bg-muted/40 border border-border">
                  <p className="text-xs font-medium text-muted-foreground mb-2">
                    Signaux EnnoDiagnostic liés
                  </p>
                  <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-1">
                    {group.signals.map((signal) => (
                      <li key={signal}>{signal}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="flex flex-wrap gap-2">
                <Badge variant="outline" className="bg-success/10 text-success border-success/30">
                  Direct {direct.length}
                </Badge>
                <Badge variant="outline" className="bg-brand/10 text-brand border-brand/30">
                  Connexe {connexe.length}
                </Badge>
                <Badge variant="outline" className="bg-blue-500/10 text-blue-700 border-blue-500/30">
                  Fondamental {fondamental.length}
                </Badge>
                {autres.length > 0 && (
                  <Badge variant="outline">
                    Autres {autres.length}
                  </Badge>
                )}
              </div>

              {sections.every((section) => section.articles.length === 0) && (
                <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                  Aucun article dans ce filtre pour ce verrou.
                </div>
              )}

              {sections.map((section) =>
                section.articles.length > 0 ? (
                  <ArticleSection
                    key={section.tag}
                    title={section.title}
                    description={section.description}
                    articles={section.articles}
                    projectId={projectId}
                    onUpdated={onUpdated}
                  />
                ) : null
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}


function v51MarkdownText(value: any): string {
  const text = String(value || "")
  return text
    .replace(/(## )/g, "\n$1")
    .replace(/(### )/g, "\n$1")
    .replace(/\n{3,}/g, "\n\n")
    .trim()
}

export function EnnoScholarPage() {
  const [activeTab, setActiveTab] = useState("par-verrou")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [query, setQuery] = useState("")
  const [showHorsSujet, setShowHorsSujet] = useState(false)
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [articles, setArticles] = useState<ArticleRead[]>([])
  const [scholarBundle, setScholarBundle] = useState<any>(null)

  const filtered = useMemo(
    () => sortArticles(filterArticles(articles, query)),
    [articles, query]
  )

  const grouped = useMemo(() => groupArticles(filtered), [filtered])

  const groupedByVerrou = useMemo(
    () => groupArticlesByScientificVerrou(filtered),
    [filtered]
  )

  const groupedByVerrouArticleCount = groupedByVerrou.reduce(
    (total, group) => total + group.articles.length,
    0
  )

  const usefulArticlesCount = groupedByVerrouArticleCount

  const loadData = async () => {
    setLoading(true)
    setError("")

    try {
      const projectList = await getProjects()
      setProjects(projectList)

      if (projectList.length === 0) {
        setProject(null)
        setArticles([])
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selectedProject =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selectedProject.id)
      setProject(selectedProject)

      const [articlesData, scholarData] = await Promise.all([
        getArticles(selectedProject.id),
        getScholarLatest(selectedProject.id).catch(() => null),
      ])

      setArticles(articlesData)
      setScholarBundle(scholarData)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger EnnoScholar."
      )
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  const changeProject = async (projectId: number) => {
    setCurrentProjectId(projectId)
    setLoading(true)
    setError("")

    try {
      const selectedProject = projects.find((item) => item.id === projectId) || null
      setProject(selectedProject)

      const [articlesData, scholarData] = await Promise.all([
        getArticles(projectId),
        getScholarLatest(projectId).catch(() => null),
      ])

      setArticles(articlesData)
      setScholarBundle(scholarData)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : "Impossible de charger les articles du projet."
      )
    } finally {
      setLoading(false)
    }
  }

  const updateLocalArticle = (updated: ArticleRead) => {
    setArticles((prev) =>
      prev.map((article) => (article.id === updated.id ? updated : article))
    )
  }

  if (loading) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 flex items-center justify-center gap-3 text-muted-foreground">
            <Loader2 className="size-5 animate-spin" />
            Chargement des articles EnnoScholar depuis FastAPI...
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card className="border-destructive/30 bg-destructive/10">
          <CardContent className="p-5 flex items-start gap-3 text-destructive">
            <AlertCircle className="size-5 mt-0.5" />
            <div className="space-y-3">
              <div>
                <p className="text-sm font-semibold">Erreur EnnoScholar</p>
                <p className="text-xs mt-1">{error}</p>
              </div>
              <Button size="sm" variant="outline" onClick={loadData}>
                Réessayer
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (!project) {
    return (
      <div className="p-6 max-w-7xl mx-auto">
        <Card>
          <CardContent className="p-8 text-center">
            <p className="text-sm font-medium text-foreground">
              Aucun projet disponible.
            </p>
            <p className="text-xs text-muted-foreground mt-1">
              Crée un projet côté backend avant d’ouvrir EnnoScholar.
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div className="p-6 space-y-6 max-w-7xl mx-auto">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <div className="size-7 rounded-md bg-brand flex items-center justify-center">
              <BookOpen className="size-4 text-brand-foreground" />
            </div>
            <h1 className="text-2xl font-bold text-foreground">
              EnnoScholar
            </h1>
          </div>

          <p className="text-sm text-muted-foreground">
            {project.organisme} — {project.project_name} — {project.year}
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Recherche scientifique et validation de l’état de l’art · Dossier ID #{project.id}
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {projects.length > 1 && (
            <select
              value={project.id}
              onChange={(event) => changeProject(Number(event.target.value))}
              className="h-9 rounded-md border border-border bg-background px-3 text-sm"
            >
              {projects.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.organisme} — {item.project_name} — {item.year}
                </option>
              ))}
            </select>
          )}

          <Button variant="outline" size="sm" onClick={loadData}>
            <RefreshCw className="size-4 mr-2" />
            Actualiser
          </Button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Articles utiles</p>
            <p className="text-2xl font-bold text-foreground mt-1">
              {usefulArticlesCount}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Directs</p>
            <p className="text-2xl font-bold text-success mt-1">
              {grouped.direct.length}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Connexes</p>
            <p className="text-2xl font-bold text-brand mt-1">
              {grouped.connexe.length}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Fondamentaux</p>
            <p className="text-2xl font-bold text-blue-700 mt-1">
              {grouped.fondamental.length}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="p-4">
            <p className="text-xs text-muted-foreground">Hors sujet</p>
            <p className="text-2xl font-bold text-muted-foreground mt-1">
              {grouped.horsSujet.length}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Search and options */}
      <Card>
        <CardContent className="p-4 flex flex-col lg:flex-row gap-3 lg:items-center lg:justify-between">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-4 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Rechercher un article, un DOI, une source..."
              className="pl-10"
            />
          </div>

          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowHorsSujet((prev) => !prev)}
          >
            {showHorsSujet ? (
              <>
                <EyeOff className="size-4 mr-2" />
                Masquer hors sujet
              </>
            ) : (
              <>
                <Eye className="size-4 mr-2" />
                Afficher hors sujet
              </>
            )}
          </Button>
        </CardContent>
      </Card>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsList className="grid grid-cols-2 lg:grid-cols-6 h-auto">
          <TabsTrigger value="par-verrou">Par verrou</TabsTrigger>
          <TabsTrigger value="direct">Directs</TabsTrigger>
          <TabsTrigger value="connexe">Connexes</TabsTrigger>
          <TabsTrigger value="fondamental">Fondamentaux</TabsTrigger>
          <TabsTrigger value="selection">Sélection consultant</TabsTrigger>
          <TabsTrigger value="hors-sujet" disabled={!showHorsSujet}>
            Hors sujet
          </TabsTrigger>
        </TabsList>

        <TabsContent value="par-verrou">
          <EnnoScholarByVerrouSection
            groups={groupedByVerrou}
            projectId={project.id}
            onUpdated={updateLocalArticle}
          />
        </TabsContent>

        <TabsContent value="direct">
          <ArticleSection
            title="Articles directs"
            description="Articles alignés directement avec les verrous techniques du dossier."
            articles={grouped.direct}
            projectId={project.id}
            onUpdated={updateLocalArticle}
          />
        </TabsContent>

        <TabsContent value="connexe">
          <ArticleSection
            title="Articles connexes"
            description="Articles proches du sujet, utiles pour compléter l’état de l’art."
            articles={grouped.connexe}
            projectId={project.id}
            onUpdated={updateLocalArticle}
          />
        </TabsContent>

        <TabsContent value="fondamental">
          <ArticleSection
            title="Articles fondamentaux"
            description="Articles généraux utiles pour expliquer les principes scientifiques."
            articles={grouped.fondamental}
            projectId={project.id}
            onUpdated={updateLocalArticle}
          />
        </TabsContent>

        <TabsContent value="selection">
          <ArticleSection
            title="Sélection consultant"
            description="Articles gardés par le consultant pour l’état de l’art."
            articles={sortArticles(articles.filter((article) => article.consultant_status === "garde"))}
            projectId={project.id}
            onUpdated={updateLocalArticle}
          />
        </TabsContent>

        <TabsContent value="hors-sujet">
          {showHorsSujet ? (
            <ArticleSection
              title="Articles hors sujet / ignorés"
              description="Articles conservés pour traçabilité, mais masqués par défaut."
              articles={grouped.horsSujet}
              projectId={project.id}
              onUpdated={updateLocalArticle}
            />
          ) : (
            <Card>
              <CardContent className="p-8 text-center">
                <p className="text-sm font-medium text-foreground">
                  Les articles hors sujet sont masqués.
                </p>
                <p className="text-xs text-muted-foreground mt-1">
                  Clique sur “Afficher hors sujet” pour les consulter.
                </p>
              </CardContent>
            </Card>
          )}
        </TabsContent>
      </Tabs>

      {scholarBundle?.bundle?.files_found && (
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Fichiers EnnoScholar détectés</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2">
            {Object.entries(scholarBundle.bundle.files_found).map(([key, value]) => (
              <Badge
                key={key}
                variant="outline"
                className={
                  value
                    ? "bg-success/10 text-success border-success/30"
                    : "bg-muted text-muted-foreground border-border"
                }
              >
                {key}: {value ? "OK" : "Absent"}
              </Badge>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}

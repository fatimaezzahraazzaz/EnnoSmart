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
  Languages,
  Loader2,
  RefreshCw,
  Search,
  XCircle,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsContent } from "@/components/ui/tabs"
import {
  getArticles,
  getDocuments,
  getProjectOverviews,
  getProjects,
  getScholarLatest,
  runScholarFromSelectedVerrous,
  syncScholarArticles,
  getStateOfArtHistory,
  getStateOfArtSelectionPreview,
  buildScholarArticleCards,
  getScholarArticleCards,
  getScholarDirectExtractStatus,
  getScholarFulltextStatus,
  prepareStateOfArtPhase1And2,
  uploadAndExtractArticlePdf,
  getLatestStateOfArt,
  runFullStateOfArt,
  translateArticleAbstract,
  updateArticleDecision,
  type ArticleRead,
  type DocumentRead,
  type ProjectOverview,
  type ProjectRead,
} from "@/lib/api"
import { getCurrentProjectId, setCurrentProjectId } from "@/lib/project-session"
import { scholarSearchFailureMessage } from "@/lib/scholar-search-status"
import { EnnoScholarStructuredStateArtPanel } from "./ennoscholar-structured-state-of-art-panel"
import { EnnoScholarPlanChat } from "./ennoscholar-plan-chat"
import {
  StatusNotice,
  WorkflowSteps,
} from "@/components/ennosmart/workspace-ui"

type ArticleDecision = "garde" | "rejete" | "en_attente"
type SourceFilter = "all" | "semantic_scholar" | "openalex" | "arxiv" | "memory_v2" | "technical"

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
  if (value.toLowerCase().includes("technique") || value.toLowerCase().includes("technical")) return "Technique"

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
    case "Technique":
      return "bg-purple-500/10 text-purple-700 border-purple-500/30"
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

function getEvidencePresentation(article: ArticleRead) {
  const sj: any = article.source_json || {}
  const preflight: any = sj.evidence_preflight || {}
  const status =
    (article as any).evidence_status ||
    preflight.evidence_status ||
    "NOT_CHECKED"
  const reasonDetail =
    (article as any).evidence_reason_detail ||
    preflight.reason_detail ||
    ""
  const recommendedAction =
    (article as any).evidence_recommended_action ||
    preflight.recommended_action ||
    ""
  const cause = [reasonDetail, recommendedAction].filter(Boolean).join(" ")
  // ENNOSCHOLAR_ACCESS_UX_V165
  const accessStatus = String((article as any).access_status || "").trim().toUpperCase()
  const reasonCode = String(
    (article as any).evidence_reason_code ||
    preflight.reason_code ||
    ""
  ).trim().toUpperCase()
  const manualVerified = Boolean(
    (article as any).manual_upload_verified ||
    sj.manual_upload_verified
  )

  if (status === "FULLTEXT_READY" && manualVerified) {
    return {
      status,
      title: "PDF manuel validé · texte intégral prêt",
      detail: "Le PDF importé a été vérifié comme correspondant à cet article puis extrait. La cause d'accès initiale reste conservée dans la traçabilité.",
      className: "border-success/30 bg-success/5 text-success",
    }
  }
  if (accessStatus === "PAYWALLED" || reasonCode === "PAYWALL_BLOCKED") {
    return {
      status,
      title: "Payant · aucune version légale trouvée",
      detail: cause || "Open Access et MCP légal n'ont trouvé aucune copie exploitable. Vous pouvez importer un PDF uniquement si vous êtes autorisé à l'utiliser.",
      className: "border-destructive/30 bg-destructive/5 text-destructive",
    }
  }
  if (accessStatus === "AUTOMATION_BLOCKED" || ["PUBLIC_PDF_BROWSER_ONLY", "ANTIBOT_BLOCKED", "AUTOMATED_ACCESS_BLOCKED"].includes(reasonCode)) {
    return {
      status,
      title: "Téléchargement automatique bloqué",
      detail: cause || "L'article est accessible, mais le site bloque le client automatique. Ouvrez-le dans votre navigateur puis importez le PDF autorisé.",
      className: "border-warning/30 bg-warning/5 text-warning",
    }
  }
  if (accessStatus === "LEGAL_ALTERNATIVE" || reasonCode === "MCP_VERIFIED_FULLTEXT_ACCESSIBLE") {
    return {
      status,
      title: "Version légale alternative trouvée",
      detail: cause || "Le MCP a vérifié une copie légale correspondant au même article. Elle peut être extraite automatiquement.",
      className: "border-success/30 bg-success/5 text-success",
    }
  }

  if (status === "FULLTEXT_READY") {
    return {
      status,
      title: "Texte intégral extrait",
      detail: "Preuve complète disponible pour méthodes, résultats et limites.",
      className: "border-success/30 bg-success/5 text-success",
    }
  }
  if (status === "ABSTRACT_READY") {
    return {
      status,
      title: "Texte intégral à récupérer",
      detail: cause || "Abstract uniquement — non utilisable comme preuve scientifique complète.",
      className: "border-warning/30 bg-warning/5 text-warning",
    }
  }
  if (status === "METADATA_ONLY") {
    return {
      status,
      title: "Texte intégral indisponible automatiquement",
      detail: cause || "Référence conservée, mais non utilisable comme preuve complète.",
      className: "border-warning/30 bg-warning/5 text-warning",
    }
  }
  if (status === "EXTRACTION_FAILED") {
    return {
      status,
      title: "Extraction automatique échouée",
      detail: cause || "L'article reste visible pour vérification consultant.",
      className: "border-destructive/30 bg-destructive/5 text-destructive",
    }
  }
  if (status === "ACCESS_AVAILABLE") {
    return {
      status,
      title: "Texte intégral accessible",
      detail: cause || "Cliquez sur cette fiche pour lancer uniquement l'extraction de cet article.",
      className: "border-brand/30 bg-brand/5 text-brand",
    }
  }
  if (status === "ACCESS_UNAVAILABLE") {
    return {
      status,
      title: "Import du PDF nécessaire",
      detail: cause || "Aucune copie publique exploitable n'a été trouvée automatiquement.",
      className: "border-warning/30 bg-warning/5 text-warning",
    }
  }
  if (status === "BROWSER_DOWNLOAD_REQUIRED") {
    return {
      status,
      title: "PDF public trouvé — navigateur requis",
      detail: cause || "Le site bloque le worker, mais le PDF officiel peut être téléchargé dans votre navigateur puis importé ici.",
      className: "border-brand/30 bg-brand/5 text-brand",
    }
  }
  if (status === "ACCESS_UNCONFIRMED") {
    return {
      status,
      title: "Vérification MCP incomplète",
      detail: cause || "Le MCP est temporairement indisponible ; aucune conclusion définitive n'est affichée.",
      className: "border-destructive/30 bg-destructive/5 text-destructive",
    }
  }
  if (status === "ACCESS_CHECKING" || status === "MCP_SEARCHING" || status === "EXTRACTION_QUEUED" || status === "EXTRACTION_RUNNING") {
    return {
      status,
      title: status === "MCP_SEARCHING"
        ? "Recherche MCP des copies légales"
        : status === "ACCESS_CHECKING" ? "Vérification de l'accès en cours" : "Extraction du texte en cours",
      detail: status === "MCP_SEARCHING"
        ? "Les sources directes n'ont rien trouvé ; le MCP vérifie les fournisseurs restants avant la conclusion."
        : status === "ACCESS_CHECKING"
          ? "Le classement est déjà visible. EnnoScholar vérifie seulement si une copie publique existe."
          : "EnnoScholar extrait uniquement cet article à votre demande.",
      className: "border-brand/30 bg-brand/5 text-brand",
    }
  }
  return {
    status: "NOT_CHECKED",
    title: "Vérification de l'accès en préparation",
    detail: "Le classement est visible ; les décisions seront activées après le contrôle d'accès.",
    className: "border-border bg-muted/30 text-muted-foreground",
  }
}

// ENNOSMART_RESEARCH_UPGRADE_V1_UI
function getEvidencePreflight(article: ArticleRead) {
  const sj: any = article.source_json || {}
  return sj?.evidence_preflight || ((article as any).evidence_status ? {
    evidence_status: (article as any).evidence_status,
    evidence_label: (article as any).evidence_label,
  } : null)
}

function evidenceBadgeClass(status?: string) {
  switch (status) {
    case "FULLTEXT_READY":
      return "bg-success/10 text-success border-success/30"
    case "ACCESS_AVAILABLE":
      return "bg-brand/10 text-brand border-brand/30"
    case "ACCESS_UNAVAILABLE":
      return "bg-warning/10 text-warning border-warning/30"
    case "BROWSER_DOWNLOAD_REQUIRED":
      return "bg-brand/10 text-brand border-brand/30"
    case "ACCESS_UNCONFIRMED":
      return "bg-destructive/10 text-destructive border-destructive/30"
    case "ABSTRACT_READY":
      return "bg-warning/10 text-warning border-warning/30"
    case "METADATA_ONLY":
      return "bg-muted text-muted-foreground border-border"
    case "EXTRACTION_FAILED":
      return "bg-destructive/10 text-destructive border-destructive/30"
    default:
      return "bg-muted text-muted-foreground border-border"
  }
}

function evidenceShortLabel(status?: string) {
  switch (status) {
    case "FULLTEXT_READY": return "Texte intégral prêt"
    case "ACCESS_AVAILABLE": return "Accessible · cliquer pour extraire"
    case "ACCESS_UNAVAILABLE": return "PDF à importer"
    case "BROWSER_DOWNLOAD_REQUIRED": return "PDF public · navigateur"
    case "ACCESS_UNCONFIRMED": return "MCP à relancer"
    case "ABSTRACT_READY": return "Résumé disponible"
    case "METADATA_ONLY": return "Référence disponible"
    case "EXTRACTION_FAILED": return "Texte non récupéré"
    case "ACCESS_CHECKING":
    case "MCP_SEARCHING":
    case "EXTRACTION_QUEUED":
    case "EXTRACTION_RUNNING": return "Texte en vérification"
    default: return "Vérification à venir"
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

function getArticleAbstractOriginal(article: ArticleRead) {
  const sj: any = article.source_json || {}
  const articleSummary: any = sj.article_summary || {}
  const tldr = typeof sj?.tldr === "string" ? sj.tldr : sj?.tldr?.text

  const abstract =
    articleSummary?.abstract_original ||
    sj?.abstract_original ||
    sj?.abstract ||
    sj?.summary ||
    articleSummary?.resume_court ||
    tldr ||
    ""

  if (!abstract) return "Aucun résumé original disponible."

  return String(abstract).replace(/\s+/g, " ").trim()
}

function getArticleAbstractFrench(article: ArticleRead) {
  const sj: any = article.source_json || {}
  const articleSummary: any = sj.article_summary || {}

  const abstract =
    articleSummary?.abstract_fr ||
    articleSummary?.resume_fr ||
    sj?.abstract_fr ||
    sj?.abstract_translated_fr ||
    sj?.resume_fr ||
    ""

  return String(abstract || "").replace(/\s+/g, " ").trim()
}

function hasFrenchTranslation(article: ArticleRead) {
  return Boolean(getArticleAbstractFrench(article))
}

function getArticleAbstractDisplay(article: ArticleRead, mode: "fr" | "original") {
  const french = getArticleAbstractFrench(article)
  const original = getArticleAbstractOriginal(article)

  if (mode === "fr" && french) return french
  return original
}

function getArticleAbstractLabel(article: ArticleRead, mode: "fr" | "original") {
  const french = getArticleAbstractFrench(article)

  if (mode === "fr" && french) {
    return "Résumé traduit en français"
  }

  return "Résumé original / Abstract complet"
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

function getArticleEvidenceStatus(article: ArticleRead | any): string {
  const a: any = article || {}
  const sj: any = a.source_json || {}
  const evidence: any = sj.evidence_preflight || a.evidence_preflight || {}
  return String(a.evidence_status || evidence.evidence_status || sj.evidence_status || "NOT_CHECKED").trim().toUpperCase()
}

// ENNOSCHOLAR_ACCESS_UX_V165
function getArticleAccessStatus(article: ArticleRead | any): string {
  const explicit = String((article as any)?.access_status || "").trim().toUpperCase()
  if (explicit) return explicit

  const sj: any = article?.source_json || {}
  const evidence: any = sj.evidence_preflight || {}
  const reasonCode = String(
    (article as any)?.evidence_reason_code ||
    evidence.reason_code ||
    ""
  ).trim().toUpperCase()
  const accessKind = String(
    (article as any)?.evidence_access_kind ||
    evidence.access_kind ||
    ""
  ).trim().toLowerCase()
  const status = getArticleEvidenceStatus(article)

  if (status === "FULLTEXT_READY") {
    return Boolean((article as any)?.manual_upload_verified || sj.manual_upload_verified)
      ? "READY_MANUAL"
      : "READY_AUTO"
  }
  if (reasonCode === "PAYWALL_BLOCKED" || accessKind === "paid") return "PAYWALLED"
  if (
    status === "BROWSER_DOWNLOAD_REQUIRED" ||
    ["PUBLIC_PDF_BROWSER_ONLY", "ANTIBOT_BLOCKED", "AUTOMATED_ACCESS_BLOCKED"].includes(reasonCode) ||
    ["blocked", "public_browser_only"].includes(accessKind)
  ) return "AUTOMATION_BLOCKED"
  if (status === "ACCESS_AVAILABLE") {
    return reasonCode === "MCP_VERIFIED_FULLTEXT_ACCESSIBLE" || accessKind === "legal_mcp_fulltext_url"
      ? "LEGAL_ALTERNATIVE"
      : "EXTRACTIBLE"
  }
  if (status === "ACCESS_UNCONFIRMED") return "UNCONFIRMED"
  if (["NOT_CHECKED", "ACCESS_CHECKING", "MCP_SEARCHING", "EXTRACTION_QUEUED", "EXTRACTION_RUNNING"].includes(status)) {
    return "CHECKING"
  }
  return "UNAVAILABLE"
}

function isArticlePaywalled(article: ArticleRead | any): boolean {
  return getArticleAccessStatus(article) === "PAYWALLED"
}

function isArticleAutomationBlocked(article: ArticleRead | any): boolean {
  return getArticleAccessStatus(article) === "AUTOMATION_BLOCKED"
}

function isArticleManualUploadVerified(article: ArticleRead | any): boolean {
  const sj: any = article?.source_json || {}
  return Boolean((article as any)?.manual_upload_verified || sj.manual_upload_verified)
}

function isArticleFulltextReady(article: ArticleRead | any): boolean {
  return getArticleEvidenceStatus(article) === "FULLTEXT_READY"
}

function isArticleAccessAvailable(article: ArticleRead | any): boolean {
  return getArticleEvidenceStatus(article) === "ACCESS_AVAILABLE"
}

function isArticleAccessUnavailable(article: ArticleRead | any): boolean {
  return ["ACCESS_UNAVAILABLE", "BROWSER_DOWNLOAD_REQUIRED", "ABSTRACT_READY", "METADATA_ONLY", "EXTRACTION_FAILED"].includes(
    getArticleEvidenceStatus(article),
  )
}

function isArticleBrowserDownloadRequired(article: ArticleRead | any): boolean {
  return getArticleEvidenceStatus(article) === "BROWSER_DOWNLOAD_REQUIRED"
}

function getArticleBrowserDownloadUrl(article: ArticleRead | any): string {
  const sj: any = article?.source_json || {}
  const preflight: any = sj.evidence_preflight || {}
  const access: any = sj.access_probe_result || {}
  const candidates: any[] = Array.isArray(sj.deterministic_oa_candidates)
    ? sj.deterministic_oa_candidates
    : []
  const publicPdf = candidates.find((candidate) =>
    candidate?.legal_access === true &&
    String(candidate?.kind || "").toLowerCase() === "pdf" &&
    /^https?:\/\//i.test(String(candidate?.url || "")),
  )
  return String(
    preflight.browser_download_url ||
    access.browser_download_url ||
    publicPdf?.url ||
    article?.url ||
    "",
  ).trim()
}

function isArticleAccessUnconfirmed(article: ArticleRead | any): boolean {
  return getArticleEvidenceStatus(article) === "ACCESS_UNCONFIRMED"
}

function evidenceLabel(article: ArticleRead | any): string {
  const accessStatus = getArticleAccessStatus(article)
  if (accessStatus === "READY_MANUAL") return "PDF manuel validé"
  if (accessStatus === "READY_AUTO") return "Texte intégral vérifié"
  if (accessStatus === "LEGAL_ALTERNATIVE") return "Version légale trouvée"
  if (accessStatus === "EXTRACTIBLE") return "Accès vérifié · extraction au clic"
  if (accessStatus === "PAYWALLED") return "Payant · PDF autorisé requis"
  if (accessStatus === "AUTOMATION_BLOCKED") return "Téléchargement automatique bloqué"
  if (accessStatus === "UNCONFIRMED") return "Accès à confirmer"
  if (accessStatus === "CHECKING") return "Accès en vérification"

  switch (getArticleEvidenceStatus(article)) {
    case "ABSTRACT_READY": return "Résumé disponible"
    case "METADATA_ONLY": return "Référence disponible"
    case "EXTRACTION_FAILED": return "Texte non récupéré"
    default: return "PDF autorisé requis"
  }
}

function countEvidenceStatuses(articles: ArticleRead[]) {
  const out = { fulltext: 0, available: 0, unavailable: 0, browserOnly: 0, unconfirmed: 0, abstract: 0, metadata: 0, failed: 0, queued: 0, notChecked: 0, total: 0 }
  for (const article of articles || []) {
    out.total += 1
    const status = getArticleEvidenceStatus(article)
    if (status === "FULLTEXT_READY") out.fulltext += 1
    else if (status === "ACCESS_AVAILABLE") out.available += 1
    else if (status === "ACCESS_UNAVAILABLE") out.unavailable += 1
    else if (status === "BROWSER_DOWNLOAD_REQUIRED") {
      out.browserOnly += 1
      out.unavailable += 1
    }
    else if (status === "ACCESS_UNCONFIRMED") out.unconfirmed += 1
    else if (status === "ABSTRACT_READY") out.abstract += 1
    else if (status === "METADATA_ONLY") out.metadata += 1
    else if (status === "EXTRACTION_FAILED") out.failed += 1
    else if (status === "ACCESS_CHECKING" || status === "MCP_SEARCHING" || status === "EXTRACTION_QUEUED" || status === "EXTRACTION_RUNNING") {
      out.queued += 1
      out.notChecked += 1
    }
    else out.notChecked += 1
  }
  return out
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
  const [decisionError, setDecisionError] = useState("")
  const [translationError, setTranslationError] = useState("")
  const [translating, setTranslating] = useState(false)
  const [translationForceMode, setTranslationForceMode] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [abstractMode, setAbstractMode] = useState<"fr" | "original">("fr")

  const reportOnly = isReportOnlyArticle(article)
  const fulltextReady = isArticleFulltextReady(article)
  const accessAvailable = isArticleAccessAvailable(article)
  const accessUnavailable = isArticleAccessUnavailable(article)
  const browserDownloadRequired = isArticleBrowserDownloadRequired(article)
  const browserDownloadUrl = getArticleBrowserDownloadUrl(article)
  const accessUnconfirmed = isArticleAccessUnconfirmed(article)
  const paywalled = isArticlePaywalled(article)
  const automationBlocked = isArticleAutomationBlocked(article)
  const manualUploadVerified = isArticleManualUploadVerified(article)
  const evidencePending = ["NOT_CHECKED", "ACCESS_CHECKING", "MCP_SEARCHING", "EXTRACTION_QUEUED", "EXTRACTION_RUNNING"].includes(
    getArticleEvidenceStatus(article)
  )
  const backendSelectionBlocked = (article as any).selection_allowed === false
  const decisionBlocked = backendSelectionBlocked || accessUnavailable || accessUnconfirmed || evidencePending

  const uploadMissingPdf = async (file: File) => {
    if (!file || uploading || reportOnly) return
    setUploading(true)
    setDecisionError("")
    try {
      const result = await uploadAndExtractArticlePdf(projectId, article.id, file)
      if (result?.article) onUpdated(result.article as ArticleRead)
      else {
        const refreshed = await getArticles(projectId, false)
        const updated = refreshed.find((item) => item.id === article.id)
        if (updated) onUpdated(updated)
      }
    } catch (error: any) {
      setDecisionError(error?.message || "Impossible d'importer et d'extraire ce PDF.")
    } finally {
      setUploading(false)
    }
  }

  const updateDecision = async (decision: ArticleDecision) => {
    if (reportOnly) {
      setDecisionError("Article lu depuis le dernier rapport EnnoScholar mais non synchronisé en base. Relance la synchronisation articles côté backend avant de décider cet article.")
      return
    }

    setLoading(true)
    setDecisionError("")

    try {
      const updated = await updateArticleDecision(projectId, article.id, decision)
      onUpdated(updated)
    } catch (error: any) {
      setDecisionError(error?.message || "Impossible de mettre à jour la décision consultant.")
    } finally {
      setLoading(false)
    }
  }

  const translateAbstract = async (force = false) => {
    setTranslating(true)
    setTranslationForceMode(force)
    setTranslationError("")

    try {
      const updated = await translateArticleAbstract(projectId, article.id, force)
      onUpdated(updated)
      setAbstractMode("fr")
      setExpanded(true)
    } catch (error: any) {
      setTranslationError(error?.message || "Impossible de traduire le résumé en français.")
    } finally {
      setTranslating(false)
      setTranslationForceMode(false)
    }
  }

  const authors = getAuthors(article)
  const hasTranslation = hasFrenchTranslation(article)
  const displayedAbstract = getArticleAbstractDisplay(article, abstractMode)
  const displayedAbstractLabel = getArticleAbstractLabel(article, abstractMode)
  const coveredVerrous = getCoveredVerrous(article)
  const evidencePresentation = getEvidencePresentation(article)

  return (
    <Card className="border border-border hover:border-brand/25">
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

              <Badge
                variant="outline"
                className={`text-xs ${decisionClass(getConsultantStatus(article))}`}
              >
                {decisionLabel(getConsultantStatus(article))}
              </Badge>

              <Badge
                variant="outline"
                className={`text-xs ${
                  fulltextReady
                    ? "bg-success/10 text-success border-success/30"
                    : accessAvailable
                      ? "bg-brand/10 text-brand border-brand/30"
                    : evidencePending
                      ? "bg-muted text-muted-foreground border-border"
                      : "bg-warning/10 text-warning border-warning/30"
                }`}
              >
                {evidenceLabel(article)}
              </Badge>

            </div>

            <p className="text-xs leading-5 text-muted-foreground">
              {[article.year, article.source, authors].filter(Boolean).join(" · ")}
              {article.doi ? ` · DOI ${article.doi}` : ""}
            </p>
            {(manualUploadVerified || reportOnly || hasTranslation || coveredVerrous.length > 1) && (
              <p className="text-[11px] leading-5 text-muted-foreground">
                {[
                  manualUploadVerified ? "PDF vérifié" : "",
                  reportOnly ? "Rapport non synchronisé" : "",
                  hasTranslation ? "Résumé FR disponible" : "",
                  coveredVerrous.length > 1 ? `${coveredVerrous.length} verrous couverts` : "",
                ].filter(Boolean).join(" · ")}
              </p>
            )}

            {expanded && coveredVerrous.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                <span className="text-[11px] text-muted-foreground mr-1">Couvre :</span>
                {coveredVerrous.map((verrou) => (
                  <Badge
                    key={`${verrou.verrou_id || verrou.verrou_number}-${v46Norm(verrou.verrou_title).slice(0, 40)}`}
                    variant="outline"
                    className={`text-[11px] ${tagClass(verrou.tag)}`}
                    title={`${getMultiVerrouBadgeLabel(verrou)} — ${verrou.verrou_title}${verrou.tag ? ` • ${normalizeTag(verrou.tag)}` : ""}`}
                  >
                    {getMultiVerrouBadgeLabel(verrou)}
                  </Badge>
                ))}
              </div>
            )}
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

        <div className={`rounded-md border p-3 ${evidencePresentation.className}`}>
          <div className="flex items-start gap-2">
            {["FULLTEXT_READY", "ACCESS_AVAILABLE"].includes(evidencePresentation.status) ? (
              <CheckCircle2 className="size-4 mt-0.5 shrink-0" />
            ) : ["NOT_CHECKED", "ACCESS_CHECKING", "MCP_SEARCHING", "EXTRACTION_QUEUED", "EXTRACTION_RUNNING"].includes(evidencePresentation.status) ? (
              <Loader2 className="size-4 mt-0.5 shrink-0" />
            ) : (
              <AlertCircle className="size-4 mt-0.5 shrink-0" />
            )}
            <div>
              <p className="text-xs font-semibold">{evidencePresentation.title}</p>
              <p className="text-xs mt-0.5">{evidencePresentation.detail}</p>
            </div>
          </div>
        </div>

        {accessUnavailable && (
          <div className="rounded-md border border-warning/30 bg-warning/5 p-3 space-y-2">
            <p className={`text-xs ${paywalled ? "text-destructive" : "text-warning"}`}>
              {paywalled
                ? "Article payant : aucune version légale exploitable n'a été trouvée après la vérification MCP. Importez uniquement un PDF que vous êtes autorisé à utiliser ; après vérification d'identité, Garder et Rejeter seront activés."
                : automationBlocked
                  ? "L'article est accessible, mais le téléchargement automatique est bloqué. Ouvrez la source dans votre navigateur puis importez le PDF autorisé ; son identité sera vérifiée avant de débloquer la sélection."
                  : browserDownloadRequired
                    ? "Le PDF public existe, mais sa protection anti-robot impose le téléchargement dans votre navigateur. Importez ensuite le fichier pour activer Garder et Rejeter."
                    : "Garder et Rejeter sont désactivés jusqu'à l'import d'une copie PDF autorisée et validée."}
            </p>
            {(paywalled || automationBlocked) && article.url && (
              <a
                href={article.url}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-10 items-center justify-center rounded-md border border-border px-3 text-xs font-medium text-foreground hover:bg-muted"
              >
                <ExternalLink className="size-3 mr-2" />
                Ouvrir l'article
              </a>
            )}
            {browserDownloadRequired && browserDownloadUrl && (
              <a
                href={browserDownloadUrl}
                target="_blank"
                rel="noreferrer"
                className="inline-flex min-h-10 items-center justify-center rounded-md border border-brand/30 px-3 text-xs font-medium text-brand hover:bg-brand/10"
              >
                <ExternalLink className="size-3 mr-2" />
                Télécharger le PDF public
              </a>
            )}
            <label className={`inline-flex h-8 items-center justify-center rounded-md border border-warning/30 px-3 text-xs font-medium text-warning ${uploading ? "pointer-events-none opacity-60" : "cursor-pointer hover:bg-warning/10"}`}>
              {uploading ? <Loader2 className="size-3 mr-2 animate-spin" /> : <FileText className="size-3 mr-2" />}
              {uploading ? "Import et extraction..." : "Importer le PDF"}
              <input
                type="file"
                accept="application/pdf,.pdf"
                className="hidden"
                disabled={uploading || reportOnly}
                onChange={(event) => {
                  const file = event.target.files?.[0]
                  if (file) void uploadMissingPdf(file)
                  event.currentTarget.value = ""
                }}
              />
            </label>
          </div>
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
              {displayedAbstractLabel}
            </p>
            <p className="text-sm text-foreground whitespace-pre-wrap">
              {displayedAbstract}
            </p>
          </div>
        )}

        {(decisionError || translationError) && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
            {decisionError || translationError}
          </div>
        )}

        <div className="flex flex-wrap gap-2 pt-1">
          <Button
            size="sm"
            className="min-h-10 text-xs bg-brand hover:bg-brand/90"
            disabled={loading || reportOnly || decisionBlocked}
            onClick={() => updateDecision("garde")}
          >
            {loading ? (
              <Loader2 className="size-3 mr-1 animate-spin" />
            ) : (
              <CheckCircle2 className="size-3 mr-1" />
            )}
            {loading && accessAvailable && !fulltextReady ? "Préparation..." : "Garder"}
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="min-h-10 text-xs text-destructive border-destructive/30 hover:bg-destructive/10"
            disabled={loading || reportOnly || decisionBlocked}
            onClick={() => updateDecision("rejete")}
          >
            <XCircle className="size-3 mr-1" />
            Rejeter
          </Button>

          <Button
            size="sm"
            variant="outline"
            className="min-h-10 text-xs"
            disabled={loading || reportOnly}
            onClick={() => updateDecision("en_attente")}
          >
            Remettre en attente
          </Button>

          <Button
            size="sm"
            variant="ghost"
            className="min-h-10 text-xs"
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

          {!hasTranslation ? (
            <Button
              size="sm"
              variant="outline"
              className="min-h-10 text-xs"
              disabled={translating || loading}
              onClick={() => translateAbstract(false)}
              title="Traduire le résumé en français avec OPUS"
            >
              <Languages className="size-3 mr-1" />
              {translating && !translationForceMode ? "Traduction..." : "Traduire FR"}
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant={abstractMode === "fr" ? "default" : "outline"}
                className="min-h-10 text-xs"
                disabled={translating}
                onClick={() => {
                  setAbstractMode("fr")
                  setExpanded(true)
                }}
                title="Afficher la traduction française sauvegardée"
              >
                <Languages className="size-3 mr-1" />
                FR
              </Button>

              <Button
                size="sm"
                variant={abstractMode === "original" ? "default" : "outline"}
                className="min-h-10 text-xs"
                disabled={translating}
                onClick={() => {
                  setAbstractMode("original")
                  setExpanded(true)
                }}
                title="Afficher l'abstract original"
              >
                Original
              </Button>

              <Button
                size="sm"
                variant="outline"
                className="min-h-10 text-xs"
                disabled={translating || loading}
                onClick={() => translateAbstract(true)}
                title="Relancer la traduction et remplacer la traduction en cache"
              >
                <RefreshCw
                  className={`size-3 mr-1 ${
                    translating && translationForceMode ? "animate-spin" : ""
                  }`}
                />
                {translating && translationForceMode ? "Retraduction..." : "Retraduire"}
              </Button>
            </>
          )}
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
  embedded = false,
}: {
  title: string
  description: string
  articles: ArticleRead[]
  projectId: number
  onUpdated: (article: ArticleRead) => void
  embedded?: boolean
}) {
  const content = (
    <>
      <div className={embedded ? "border-b border-border/70 pb-3" : undefined}>
        <CardTitle className="text-sm flex items-center gap-2">
          <FileText className="size-4 text-brand" aria-hidden="true" />
          {title}
          <Badge variant="outline" className="ml-1">
            {articles.length}
          </Badge>
        </CardTitle>
        <CardDescription className="text-xs">
          {description}
        </CardDescription>
      </div>

      <div className="space-y-3">
        {articles.length === 0 ? (
          <div className="p-6 text-center border border-dashed rounded-lg">
            <p className="text-sm font-medium text-foreground">
              Aucun article dans cette catégorie.
            </p>
          </div>
        ) : (
          articles.map((article) => (
            <ArticleCard
              key={getArticleUniqueKey(article)}
              article={article}
              projectId={projectId}
              onUpdated={onUpdated}
            />
          ))
        )}
      </div>
    </>
  )

  if (embedded) {
    return <section className="space-y-3">{content}</section>
  }

  return (
    <Card>
      <CardContent className="space-y-4 p-4 sm:p-5">{content}</CardContent>
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

function getArticleGrouping(article: ArticleRead, groupingGroups: any[] = []) {
  if (!Array.isArray(groupingGroups) || groupingGroups.length === 0) return null

  const sj: any = article.source_json || {}
  const validation: any = sj.verrou_scientific_validation || {}

  const ids = [
    (article as any).verrou_id,
    validation.verrou_id,
    sj.verrou_id,
    sj.db_verrou_id,
    sj.db_article_id,
  ]
    .filter((value) => value !== null && value !== undefined && String(value).trim())
    .map((value) => String(value).trim())

  const titleCandidates = [
    validation.verrou_title,
    sj?.scientific_intent?.verrou_title,
    sj?.verrou_title,
    sj?.enriched_title,
    (article as any)?.verrou_title,
  ].map((value) => v46Norm(value))

  return (
    groupingGroups.find((group: any) => {
      const groupedDbIds = (group?.grouped_db_verrou_ids || []).map((value: any) => String(value).trim())
      const groupedSourceIds = (group?.grouped_source_verrou_ids || []).map((value: any) => String(value).trim())
      const groupedIds = [...groupedDbIds, ...groupedSourceIds]

      if (ids.some((id) => groupedIds.includes(id))) return true

      const groupedTitle = v46Norm(group?.consolidated_title)
      if (groupedTitle && titleCandidates.includes(groupedTitle)) return true

      return false
    }) || null
  )
}

function getArticleVerrouTitle(article: ArticleRead, groupingGroups: any[] = []): string {
  const sj: any = article.source_json || {}
  const validation: any = sj.verrou_scientific_validation || {}
  const grouping = getArticleGrouping(article, groupingGroups)

  return v46Short(
    grouping?.consolidated_title ||
      validation?.verrou_title ||
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

function getArticleVerrouKey(article: ArticleRead, groupingGroups: any[] = []): string {
  const sj: any = article.source_json || {}
  const grouping = getArticleGrouping(article, groupingGroups)

  if (grouping?.group_key) return `group:${String(grouping.group_key)}`
  if (grouping?.consolidated_title) return `group-title:${v46Norm(grouping.consolidated_title).slice(0, 180)}`

  const validation: any = sj.verrou_scientific_validation || {}
  const explicit =
    sj?.scientific_intent?.verrou_id ||
    validation?.verrou_id ||
    sj?.verrou_id ||
    sj?.verrou_key ||
    sj?.group_id ||
    (article as any)?.verrou_id

  if (explicit !== null && explicit !== undefined && String(explicit).trim()) {
    return `id:${String(explicit).trim()}`
  }

  const title = getArticleVerrouTitle(article, groupingGroups)
  return `title:${v46Norm(title).slice(0, 160)}`
}

function getArticleUniqueKey(article: ArticleRead): string {
  const sj: any = article.source_json || {}

  // Le titre exact normalise passe avant le DOI : un meme papier arrive
  // parfois d'une source avec DOI et d'une autre sans DOI.
  const title = v46Norm(article.title || sj?.title || sj?.article_title)
  if (title) return `title:${title}`

  const doi = v46Norm(
    v46Text(article.doi || sj?.doi)
      .replace(/^https?:\/\/(?:dx\.)?doi\.org\//i, "")
      .replace(/^doi:\s*/i, "")
  )
  if (doi) return `doi:${doi}`

  const url = v46Norm(article.url || sj?.url)
  if (url) return `url:${url}`

  const paperId = v46Text(sj?.paper_id || sj?.paperId || sj?.id || "")
  if (paperId) return `paper:${paperId}`

  return `article:${article.id || "unknown"}`
}

function groupArticlesByScientificVerrou(articles: ArticleRead[], groupingGroups: any[] = []) {
  const groups = new Map<
    string,
    {
      key: string
      title: string
      profile?: string
      reason?: string
      groupedCount?: number
      groupedIds?: string[]
      candidateCount?: number
      rawCount?: number
      technicalSourcesCount?: number
      usefulCount?: number
      signals: string[]
      articles: ArticleRead[]
      seenArticles: Set<string>
    }
  >()

  for (const article of articles) {
    const grouping = getArticleGrouping(article, groupingGroups)
    const key = getArticleVerrouKey(article, groupingGroups)
    const title = getArticleVerrouTitle(article, groupingGroups)

    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title,
        profile: grouping?.profile,
        reason: grouping?.reason,
        groupedCount: Number(grouping?.grouped_count || 1),
        groupedIds: grouping?.grouped_db_verrou_ids || grouping?.grouped_source_verrou_ids || [],
        signals: [],
        articles: [],
        seenArticles: new Set<string>(),
      })
    }

    const group = groups.get(key)!

    const groupingSignals = Array.isArray(grouping?.grouped_original_titles)
      ? grouping.grouped_original_titles
      : []

    for (const item of groupingSignals) {
      const signal = v46Short(item, 260)
      if (signal && !group.signals.some((x) => v46Norm(x) === v46Norm(signal))) {
        group.signals.push(signal)
      }
    }

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
      usefulCount: group.articles.filter((article) => ["Direct", "Connexe", "Fondamental"].includes(normalizeTag(article.tag_article))).length,
      signals: group.signals.slice(0, 12),
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


function isTechnicalCatalogArticle(article: ArticleRead | any): boolean {
  const a: any = article || {}
  const sj: any = a.source_json || {}

  const source = v46Norm(a.source || sj.source || "")
  const sourceType = v46Norm(a.source_type || sj.source_type || "")
  const sourceKind = v46Norm(a.source_kind || sj.source_kind || "")
  const paperId = v46Norm(a.paper_id || sj.paper_id || "")
  const tag = normalizeTag(a.tag_article || sj.tag || sj.tag_article)

  return (
    source === "technical catalog" ||
    source === "technical_catalog" ||
    sourceType === "technical reference" ||
    sourceType === "technical_reference" ||
    sourceKind.includes("source technique") ||
    paperId.startsWith("tech ") ||
    paperId.startsWith("tech:") ||
    tag === "Technique"
  )
}

function getArticleSourceKind(article: ArticleRead | any): SourceFilter {
  const a: any = article || {}
  const sj: any = a.source_json || {}
  const source = v46Norm(a.source || sj.source || "")
  const paperId = v46Norm(a.paper_id || sj.paper_id || sj.paperId || sj.id || "")

  if (isTechnicalCatalogArticle(article)) return "technical"
  if (source.includes("memory_v2") || a.memory_v2_prior || sj.memory_v2_prior || String(paperId).includes("memory")) return "memory_v2"
  if (source.includes("semantic")) return "semantic_scholar"
  if (source.includes("openalex")) return "openalex"
  if (source.includes("arxiv") || paperId.includes("arxiv")) return "arxiv"

  return "all"
}

function sourceFilterLabel(value: SourceFilter): string {
  switch (value) {
    case "semantic_scholar":
      return "Semantic Scholar"
    case "openalex":
      return "OpenAlex"
    case "arxiv":
      return "ArXiv"
    case "memory_v2":
      return "Mémoire V2"
    case "technical":
      return "Sources techniques"
    default:
      return "Toutes les sources"
  }
}

function articleMatchesSourceFilter(article: ArticleRead, sourceFilter: SourceFilter): boolean {
  if (sourceFilter === "all") return true
  return getArticleSourceKind(article) === sourceFilter
}

function dedupeArticlesGlobally(articles: ArticleRead[]): ArticleRead[] {
  const byKey = new Map<string, ArticleRead>()

  const tagRank: Record<string, number> = {
    Direct: 5,
    Connexe: 4,
    Fondamental: 3,
    Technique: 2,
    "Hors sujet": 1,
  }

  function articleQuality(article: ArticleRead) {
    const tag = normalizeTag(article.tag_article || article.source_json?.tag || article.source_json?.tag_article)
    const sourceKind = getArticleSourceKind(article)
    const score = Number(article.score ?? article.source_json?.relevance_score ?? 0)
    const memoryPenalty = sourceKind === "memory_v2" ? -0.03 : 0
    return (tagRank[tag] || 0) * 10 + score + memoryPenalty
  }

  for (const article of articles || []) {
    const key = getArticleUniqueKey(article)
    const existing = byKey.get(key)

    if (!existing || articleQuality(article) > articleQuality(existing)) {
      byKey.set(key, article)
    }
  }

  return Array.from(byKey.values())
}

function countArticlesByTag(articles: ArticleRead[]) {
  const counts = {
    total: 0,
    direct: 0,
    connexe: 0,
    fondamental: 0,
    technique: 0,
    horsSujet: 0,
    autres: 0,
    directConnexe: 0,
  }

  for (const article of articles || []) {
    counts.total += 1

    if (isTechnicalCatalogArticle(article)) {
      counts.technique += 1
      continue
    }

    const tag = normalizeTag(article.tag_article || article.source_json?.tag || article.source_json?.tag_article)

    if (tag === "Direct") counts.direct += 1
    else if (tag === "Connexe") counts.connexe += 1
    else if (tag === "Fondamental") counts.fondamental += 1
    else if (tag === "Hors sujet") counts.horsSujet += 1
    else counts.autres += 1
  }

  counts.directConnexe = counts.direct + counts.connexe
  return counts
}

function isKeptForStateOfArt(article: ArticleRead | any): boolean {
  const a: any = article || {}
  const sj: any = a.source_json || {}

  const status = v46Norm(
    a.consultant_status ||
      a.consultantStatus ||
      a.status ||
      sj.consultant_status ||
      sj.consultantStatus ||
      ""
  )

  if (["garde", "gardee", "gardé", "gardée", "keep", "kept", "selected", "retenu", "valide", "validé"].some((x) => status.includes(v46Norm(x)))) {
    return true
  }

  return isArticleSelectedForStateOfArt(article)
}

function isUsableArticleForStateOfArtWriting(article: ArticleRead | any): boolean {
  const a: any = article || {}
  const sj: any = a.source_json || {}
  const tag = normalizeTag(a.tag_article || sj.tag || sj.tag_article)

  if (isTechnicalCatalogArticle(article)) return false
  if (!["Direct", "Connexe", "Fondamental"].includes(tag)) return false

  const evidenceStatus =
    (a as any).evidence_status ||
    sj?.evidence_preflight?.evidence_status ||
    null

  if (evidenceStatus && evidenceStatus !== "FULLTEXT_READY") return false

  return isKeptForStateOfArt(article)
}

function getWriterResults(report: any): any[] {
  if (!report) return []
  if (Array.isArray(report?.results)) return report.results
  if (Array.isArray(report?.report?.results)) return report.report.results
  if (Array.isArray(report?.latest?.report?.results)) return report.latest.report.results
  if (Array.isArray(report?.state_of_art_report?.results)) return report.state_of_art_report.results
  if (Array.isArray(report?.data?.results)) return report.data.results
  return []
}

function getWriterDraft(result: any): string {
  return (
    v46Text(result?.state_of_art?.draft) ||
    v46Text(result?.draft) ||
    v46Text(result?.state_of_art?.text) ||
    v46Text(result?.state_of_art?.markdown) ||
    ""
  )
}

function getWriterStructured(result: any): any {
  return (
    result?.state_of_art?.structured ||
    result?.state_of_art?.structured_state_of_art ||
    result?.structured_state_of_art ||
    result?.structured ||
    null
  )
}

function renderStructuredStateOfArtText(structured: any): string {
  if (!structured || typeof structured !== "object") return ""

  const lines: string[] = []
  const title = v46Text(structured.verrou_title)
  if (title) {
    lines.push(`État de l’art — ${title}`)
    lines.push("")
  }

  if (structured.positionnement) {
    lines.push("1. Positionnement du verrou")
    lines.push(v46Text(structured.positionnement))
    lines.push("")
  }

  const direct = Array.isArray(structured.travaux_directs) ? structured.travaux_directs : []
  if (direct.length) {
    lines.push("2. Travaux directement liés")
    for (const item of direct) {
      if (!item || typeof item !== "object") continue
      const ref = v46Text(item.article_ref)
      const title = v46Text(item.article_title)
      lines.push(`${ref ? ref + " — " : ""}${title}`)
      if (item.synthesis) lines.push(`Synthèse : ${v46Text(item.synthesis)}`)
      if (item.limits_for_project) lines.push(`Limite / transposition : ${v46Text(item.limits_for_project)}`)
      lines.push("")
    }
  }

  const connexe = Array.isArray(structured.travaux_connexes) ? structured.travaux_connexes : []
  if (connexe.length) {
    lines.push("3. Travaux connexes utiles")
    for (const item of connexe) {
      if (!item || typeof item !== "object") continue
      const ref = v46Text(item.article_ref)
      const title = v46Text(item.article_title)
      lines.push(`${ref ? ref + " — " : ""}${title}`)
      if (item.synthesis) lines.push(`Synthèse : ${v46Text(item.synthesis)}`)
      if (item.limits_for_project) lines.push(`Limite / transposition : ${v46Text(item.limits_for_project)}`)
      lines.push("")
    }
  }

  const limits = Array.isArray(structured.limites_etat_art) ? structured.limites_etat_art : []
  if (limits.length) {
    lines.push("4. Limites de l’état de l’art")
    for (const item of limits) lines.push(`- ${v46Text(item)}`)
    lines.push("")
  }

  if (structured.gap_scientifique) {
    lines.push("5. Gap scientifique pour le dossier CIR")
    lines.push(v46Text(structured.gap_scientifique))
    lines.push("")
  }

  const hypotheses = Array.isArray(structured.hypotheses_a_valider) ? structured.hypotheses_a_valider : []
  if (hypotheses.length) {
    lines.push("6. Hypothèses à valider consultant")
    for (const item of hypotheses) lines.push(`- ${v46Text(item)}`)
    lines.push("")
  }

  const refs = Array.isArray(structured.references) ? structured.references : []
  if (refs.length) {
    lines.push("7. Références mobilisées")
    for (const ref of refs) {
      if (typeof ref === "string") {
        lines.push(`- ${ref}`)
      } else if (ref && typeof ref === "object") {
        lines.push(`- [${v46Text(ref.article_ref)}] ${v46Text(ref.reference || ref.title)}`)
      }
    }
  }

  return lines.join("\n").replace(/\n{3,}/g, "\n\n").trim()
}

function StateOfArtInlineGeneratedPanel({ report }: { report: any }) {
  const results = getWriterResults(report)

  if (!results.length) {
    return (
      <Card className="border-warning/30 bg-warning/5">
        <CardContent className="p-4 text-sm text-warning">
          La génération a répondu, mais aucun résultat exploitable n’a été trouvé dans le JSON retourné.
        </CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-4">
      {results.map((result: any, index: number) => {
        const structured = getWriterStructured(result)
        const draft = getWriterDraft(result) || renderStructuredStateOfArtText(structured)
        const refs =
          result?.state_of_art?.references ||
          result?.citation_articles ||
          structured?.references ||
          []
        const guard = result?.state_of_art?.citation_guard || {}

        return (
          <Card key={String(result?.verrou_id || index)} className="border-brand/20">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4 text-brand" />
                {result?.verrou_title || structured?.verrou_title || `Verrou ${index + 1}`}
              </CardTitle>
              <CardDescription className="text-xs">
                Articles utilisés : {result?.selected_articles_count ?? result?.citation_articles?.length ?? refs?.length ?? "—"}
                {result?.state_of_art?.mode ? ` · Mode : ${result.state_of_art.mode}` : ""}
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              {draft ? (
                <div className="rounded-md border bg-white p-4">
                  <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-7 text-foreground">
                    {v51MarkdownText(draft)}
                  </pre>
                </div>
              ) : (
                <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
                  Aucun texte rédigé reçu pour ce verrou. Vérifie le champ <code>state_of_art.draft</code> dans la réponse API.
                </div>
              )}

              {Array.isArray(refs) && refs.length > 0 && (
                <div className="rounded-md border bg-muted/30 p-3">
                  <p className="text-xs font-semibold text-foreground mb-2">Références utilisées</p>
                  <div className="space-y-1">
                    {refs.map((ref: any, idx: number) => (
                      <p key={idx} className="text-xs text-muted-foreground break-words">
                        <span className="font-medium text-foreground">
                          {ref?.citation_id ? `[${ref.citation_id}]` : ref?.token || ref?.label || `R${idx + 1}`}
                        </span>{" "}
                        {ref?.title || ref?.reference || ref?.label || ""}
                        {ref?.year ? ` — ${ref.year}` : ""}
                      </p>
                    ))}
                  </div>
                </div>
              )}

              {guard?.unknown_citations?.length > 0 && (
                <div className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-xs text-destructive">
                  Citations inconnues à vérifier : {guard.unknown_citations.join(", ")}
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}


function getStateOfArtView(report: any): any {
  return (
    report?.state_of_art_view ||
    report?.report?.state_of_art_view ||
    report?.report ||
    report?.state_of_art ||
    null
  )
}

function getStateOfArtVerrouViews(report: any): any[] {
  const view = getStateOfArtView(report)
  if (Array.isArray(view?.verrous)) return view.verrous
  if (Array.isArray(report?.verrous)) return report.verrous
  if (Array.isArray(report?.drafts)) {
    return report.drafts.map((draft: any, index: number) => {
      const draftJson = draft?.draft_json || draft?.candidate_draft_json || {}
      return {
        index,
        ok: draft?.ok ?? draftJson?.ok,
        verrou_id: draft?.verrou_id || draftJson?.verrou_id,
        verrou_title: draft?.verrou_title || draftJson?.verrou_title,
        draft_title: draftJson?.draft_title,
        sections: draftJson?.sections || {},
        method_evidence_chains: draftJson?.method_evidence_chains || [],
        method_evidence_chains_count: (draftJson?.method_evidence_chains || []).length,
        citations_used: draftJson?.citations_used || [],
        references_utilisees: draftJson?.sections?.references_utilisees || [],
        guard: draft?.guard || {},
        polish: draft?.polish || {},
      }
    })
  }
  return []
}

function getSectionText(sections: any, key: string) {
  const value = sections?.[key]
  if (Array.isArray(value)) return value.join("\n")
  return v46Text(value)
}

function EvidenceChainCard({ chain, index }: { chain: any; index: number }) {
  const rows = [
    ["Problème scientifique", chain?.scientific_problem],
    ["Pourquoi cette méthode existe", chain?.why_this_method_exists],
    ["Mécanisme", chain?.mechanism],
    ["Pipeline d’entraînement", chain?.training_pipeline],
    ["Protocole d’évaluation", chain?.evaluation_protocol],
    ["Résultat expérimental", chain?.experimental_results],
    ["Limite / transposition", chain?.remaining_limitations],
    ["Transition", chain?.transition_to_next_method],
  ].filter(([, value]) => v46Text(value))

  return (
    <Card className="border border-brand/20 bg-background">
      <CardHeader className="pb-3">
        <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
          <Badge variant="outline" className="bg-brand/10 text-brand border-brand/30">
            {chain?.citation_label || chain?.citation || `M${index + 1}`}
          </Badge>
          <span>{chain?.concept || `Méthode ${index + 1}`}</span>
          {chain?.usage_type && (
            <Badge variant="outline" className="text-xs">
              {chain.usage_type}
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          Evidence Chain : problème → mécanisme → pipeline → validation → limite → transition.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-2">
        {rows.map(([label, value]) => (
          <div key={String(label)} className="rounded-md border bg-muted/20 p-3">
            <p className="text-xs font-semibold text-foreground mb-1">{label}</p>
            <p className="text-sm text-muted-foreground leading-6">{v46Text(value)}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  )
}

function StateOfArtEvidenceChainsPanel({ report }: { report: any }) {
  const verrous = getStateOfArtVerrouViews(report)

  if (!verrous.length) return null

  return (
    <div className="space-y-4">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <BookOpen className="size-4 text-brand" />
            Evidence Chains V5.9
          </CardTitle>
          <CardDescription>
            Affichage consultant du raisonnement interne : chaque méthode est transformée en chaîne problème → mécanisme → validation → gap, et non en résumé d’article.
          </CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 md:grid-cols-4">
          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Verrous rédigés</p>
            <p className="text-2xl font-semibold">{verrous.length}</p>
          </div>
          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Evidence Chains</p>
            <p className="text-2xl font-semibold">
              {verrous.reduce((total, verrou) => total + Number(verrou?.method_evidence_chains_count || verrou?.method_evidence_chains?.length || 0), 0)}
            </p>
          </div>
          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Guard</p>
            <p className="text-sm font-semibold">
              {verrous.every((v) => v?.guard?.strict_ok === true) ? "Strict OK" : "À vérifier"}
            </p>
          </div>
          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">LLM</p>
            <p className="text-sm font-semibold">
              {verrous.some((v) => v?.polish?.accepted) ? "Édition acceptée" : "Draft déterministe"}
            </p>
          </div>
        </CardContent>
      </Card>

      {verrous.map((verrou: any, verrouIndex: number) => {
        const chains = Array.isArray(verrou?.method_evidence_chains) ? verrou.method_evidence_chains : []
        const sections = verrou?.sections || {}

        return (
          <Card key={String(verrou?.verrou_id || verrouIndex)} className="border-brand/20">
            <CardHeader>
              <CardTitle className="text-sm flex items-center gap-2 flex-wrap">
                <FileText className="size-4 text-brand" />
                Verrou {verrouIndex + 1} — {verrou?.verrou_title || "Verrou scientifique"}
                <Badge variant="outline" className={statusBadge(verrou?.guard?.strict_ok)}>
                  {verrou?.guard?.strict_ok ? "Strict OK" : "À vérifier"}
                </Badge>
              </CardTitle>
              <CardDescription>
                {chains.length} chaîne(s) méthode construites à partir de Phase 4.5 + Phase 4.7.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="rounded-md border bg-muted/20 p-3">
                  <p className="text-xs font-semibold text-foreground mb-1">Positionnement</p>
                  <p className="text-sm text-muted-foreground leading-6">
                    {v46Short(getSectionText(sections, "positionnement_scientifique_du_verrou"), 900)}
                  </p>
                </div>
                <div className="rounded-md border bg-muted/20 p-3">
                  <p className="text-xs font-semibold text-foreground mb-1">Gap R&D</p>
                  <p className="text-sm text-muted-foreground leading-6">
                    {v46Short(getSectionText(sections, "insuffisances_et_gap_rd"), 900)}
                  </p>
                </div>
              </div>

              {chains.length ? (
                <div className="grid gap-3 lg:grid-cols-2">
                  {chains.map((chain: any, index: number) => (
                    <EvidenceChainCard key={`${chain?.citation_label || index}-${index}`} chain={chain} index={index} />
                  ))}
                </div>
              ) : (
                <div className="rounded-md border border-warning/30 bg-warning/10 p-3 text-sm text-warning">
                  Aucune Evidence Chain trouvée pour ce verrou. Vérifie que Phase 5 V5.9 est bien utilisée.
                </div>
              )}
            </CardContent>
          </Card>
        )
      })}
    </div>
  )
}

function EnnoScholarByVerrouSection({
  groups,
  projectId,
  onUpdated,
  groupingSummary,
}: {
  groups: ReturnType<typeof groupArticlesByScientificVerrou>
  projectId: number
  onUpdated: (article: ArticleRead) => void
  groupingSummary?: any
}) {
  const [selectedVerrouKey, setSelectedVerrouKey] = useState<string>("all")
  const [selectedTag, setSelectedTag] = useState<"all" | "Direct" | "Connexe" | "Fondamental" | "Hors sujet" | "Autres">("all")

  const filteredGroups = selectedVerrouKey === "all"
    ? groups
    : groups.filter((group) => group.key === selectedVerrouKey)

  const selectedVerrouGroup =
    selectedVerrouKey === "all"
      ? null
      : groups.find((group) => group.key === selectedVerrouKey) || null

  if (groups.length === 0) {
    return (
      <Card>
        <CardContent className="p-8 text-center">
          <p className="text-sm font-medium text-foreground">
            Aucun article trouvé pour ce filtre.
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            Reviens à « Toutes les sources » ou vérifie le filtre actif. Si la liste reste vide, relance la synchronisation EnnoScholar.
          </p>
        </CardContent>
      </Card>
    )
  }

  const tagButtons: Array<"all" | "Direct" | "Connexe" | "Fondamental" | "Hors sujet" | "Autres"> = [
    "all",
    "Direct",
    "Connexe",
    "Fondamental",
    "Hors sujet",
    "Autres",
  ]

  return (
    <div className="space-y-4">
      <section className="overflow-hidden rounded-xl border border-brand/20 bg-card shadow-xs" aria-labelledby="scholar-verrou-filters-title">
        <div className="border-b border-brand/15 bg-brand/5 px-4 py-3">
          <h2 id="scholar-verrou-filters-title" className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <BookOpen className="size-4 text-brand" aria-hidden="true" />
            Filtrer les articles par verrou scientifique
          </h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Choisis un verrou, puis filtre ses articles par catégorie : Direct, Connexe ou Fondamental.
          </p>
        </div>

        <div className="space-y-4 p-4">
          {groupingSummary?.active && (
            <dl className="flex flex-wrap items-center gap-x-6 gap-y-2 rounded-lg border border-border bg-muted/30 px-3 py-2.5">
              {[
                ["Signaux de départ", groupingSummary?.input_signals_count ?? "—"],
                ["Verrous consolidés", groupingSummary?.grouped_verrous_count ?? groups.length],
                ["Doublons évités", groupingSummary?.duplicates_removed ?? 0],
              ].map(([label, value]) => (
                <div key={String(label)} className="flex items-baseline gap-2">
                  <dt className="text-xs text-muted-foreground">{label}</dt>
                  <dd className="text-sm font-semibold tabular-nums text-foreground">{value}</dd>
                </div>
              ))}
            </dl>
          )}

          <div className="space-y-2">
              <label htmlFor="scholar-verrou-filter" className="text-xs font-medium text-muted-foreground">Filtre par verrou</label>
              <select
                id="scholar-verrou-filter"
                value={selectedVerrouKey}
                onChange={(event) => setSelectedVerrouKey(event.target.value)}
                className="min-h-11 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
              >
                <option value="all">Tous les verrous scientifiques consolidés</option>
                {groups.map((group, index) => (
                  <option key={group.key} value={group.key}>
                    V{index + 1} — {group.title} ({group.usefulCount ?? group.articles.length} utile(s){Number(group.candidateCount || 0) > 0 ? ` / ${group.candidateCount} candidat(s)` : ""})
                  </option>
                ))}
              </select>
          </div>

          {selectedVerrouGroup && (
            <div className="rounded-md border border-brand/20 bg-background p-3 space-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="outline" className="text-xs">
                  {selectedVerrouGroup.groupedCount || 1} signal{Number(selectedVerrouGroup.groupedCount || 1) > 1 ? "s" : ""} regroupé{Number(selectedVerrouGroup.groupedCount || 1) > 1 ? "s" : ""}
                </Badge>
                {selectedVerrouGroup.profile && (
                  <Badge variant="outline" className="text-xs">
                    Profil : {selectedVerrouGroup.profile}
                  </Badge>
                )}
              </div>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Ce que ce verrou regroupe : </span>
                {selectedVerrouGroup.signals.length > 0 ? selectedVerrouGroup.signals.join(" ; ") : "signal unique"}
              </p>
              <p className="text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Pourquoi ce regroupement : </span>
                {selectedVerrouGroup.reason || "même objet technique ou même phénomène scientifique détecté dans les sources."}
              </p>
            </div>
          )}

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
                    aria-pressed={active}
                    className={`min-h-10 rounded-full border px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25 ${
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

        </div>
      </section>

      {filteredGroups.map((group, index) => {
        const direct = group.articles.filter((article) => normalizeTag(article.tag_article) === "Direct")
        const connexe = group.articles.filter((article) => normalizeTag(article.tag_article) === "Connexe")
        const fondamental = group.articles.filter((article) => normalizeTag(article.tag_article) === "Fondamental")
        const horsSujet = group.articles.filter((article) => normalizeTag(article.tag_article) === "Hors sujet")
        const autres = group.articles.filter((article) => {
          const tag = normalizeTag(article.tag_article)
          return !["Direct", "Connexe", "Fondamental", "Hors sujet"].includes(tag)
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
            tag: "Hors sujet",
            title: "Articles hors sujet",
            description: "Articles détectés comme hors sujet, conservés seulement pour contrôle.",
            articles: horsSujet,
          },
          {
            tag: "Autres",
            title: "Autres articles",
            description: "Articles rattachés au verrou mais non classés Direct, Connexe, Fondamental ou Hors sujet.",
            articles: autres,
          },
        ].filter((section) => selectedTag === "all" || section.tag === selectedTag)

        const visibleCount = sections.reduce((total, section) => total + section.articles.length, 0)
        const usefulVisibleCount = direct.length + connexe.length + fondamental.length
        const evidenceCounts = countEvidenceStatuses(group.articles)
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
                {usefulVisibleCount !== visibleCount && (
                  <Badge variant="outline" className="ml-1">
                    {usefulVisibleCount} utile(s)
                  </Badge>
                )}
                {Number(group.candidateCount || 0) > 0 && (
                  <Badge variant="outline" className="ml-1">
                    {group.candidateCount} candidat(s) récupéré(s)
                  </Badge>
                )}
              </CardTitle>
              <CardDescription className="text-xs">
                <span className="font-medium text-foreground">Nom du verrou : </span>
                {group.title}
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              <div className="p-3 rounded-md bg-muted/40 border border-border space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-xs font-medium text-muted-foreground">
                    Regroupement EnnoDiagnostic → EnnoScholar
                  </p>
                  <Badge variant="outline" className="text-xs">
                    {group.groupedCount || group.signals.length || 1} signal{Number(group.groupedCount || group.signals.length || 1) > 1 ? "s" : ""}
                  </Badge>
                  {group.profile && (
                    <Badge variant="outline" className="text-xs">
                      {group.profile}
                    </Badge>
                  )}
                </div>

                {group.signals.length > 0 && (
                  <div>
                    <p className="text-xs font-medium text-foreground mb-1">Signaux regroupés</p>
                    <ul className="list-disc pl-5 text-xs text-muted-foreground space-y-1">
                      {group.signals.map((signal) => (
                        <li key={signal}>{signal}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <p className="text-xs text-muted-foreground">
                  <span className="font-medium text-foreground">Pourquoi : </span>
                  {group.reason || "les articles sont rattachés au même verrou scientifique consolidé."}
                </p>
              </div>

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
                {horsSujet.length > 0 && (
                  <Badge variant="outline" className="border-slate-300 bg-slate-100 text-slate-600">
                    Hors sujet {horsSujet.length}
                  </Badge>
                )}
                {autres.length > 0 && (
                  <Badge variant="outline">
                    Autres {autres.length}
                  </Badge>
                )}
                <Badge variant="outline" className="bg-success/10 text-success border-success/30">
                  Texte intégral {evidenceCounts.fulltext}
                </Badge>
                {evidenceCounts.abstract > 0 && (
                  <Badge variant="outline" className="bg-warning/10 text-warning border-warning/30">
                    Abstract {evidenceCounts.abstract}
                  </Badge>
                )}
                {evidenceCounts.metadata > 0 && (
                  <Badge variant="outline">Métadonnées {evidenceCounts.metadata}</Badge>
                )}
                {evidenceCounts.notChecked > 0 && (
                  <Badge variant="outline" className="bg-muted text-muted-foreground">
                    Textes vérifiés {evidenceCounts.total - evidenceCounts.notChecked}/{evidenceCounts.total}
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
                    embedded
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


function formatStateArtDate(value: any) {
  if (!value) return "—"

  try {
    const date = new Date(String(value))
    if (Number.isNaN(date.getTime())) return String(value)
    return date.toLocaleString("fr-FR")
  } catch {
    return String(value)
  }
}

function getGeneratedMarkdown(report: any): string {
  const candidates = [
    report?.markdown,
    report?.state_of_art?.markdown,
    report?.state_of_art?.draft,
    report?.draft,
  ]

  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value.trim()
  }

  return ""
}


function compactJson(value: any) {
  try {
    return JSON.stringify(value || {}, null, 2)
  } catch {
    return String(value || "")
  }
}

function countArray(value: any, keys: string[] = []) {
  if (Array.isArray(value)) return value.length
  for (const key of keys) {
    const current = value?.[key]
    if (Array.isArray(current)) return current.length
  }
  return 0
}

function statusBadge(ok: boolean | null | undefined) {
  if (ok === true) return "bg-success/10 text-success border-success/30"
  if (ok === false) return "bg-destructive/10 text-destructive border-destructive/30"
  return "bg-muted text-muted-foreground border-border"
}

function extractPayloadArray(payload: any, keys: string[] = []) {
  if (Array.isArray(payload)) return payload

  for (const key of keys) {
    const value = payload?.[key]
    if (Array.isArray(value)) return value
  }

  return []
}

function getArticleCardsList(payload: any) {
  return extractPayloadArray(payload, [
    "cards",
    "article_cards",
    "articles",
    "selected_article_cards",
    "items",
    "results",
  ])
}

function getStatusResults(payload: any) {
  return extractPayloadArray(payload, ["results", "items", "articles", "statuses"])
}

function normalizeId(value: any) {
  if (value === null || value === undefined) return ""
  return String(value)
}

function findByArticleId(items: any[], articleId: number) {
  const wanted = normalizeId(articleId)
  return items.find((item) => normalizeId(item?.article_id || item?.id) === wanted) || null
}

function getDoiUrl(doi: string | null | undefined) {
  const value = String(doi || "").trim()
  if (!value) return ""
  if (value.startsWith("http://") || value.startsWith("https://")) return value
  return `https://doi.org/${value}`
}

function getBestArticleLink(article: ArticleRead, status: any | null, directStatus: any | null) {
  const candidates = [
    status?.pdf_url,
    status?.pdf_source_url,
    status?.candidate_pdf_url,
    status?.url,
    directStatus?.pdf_source_url,
    article.url,
    getDoiUrl(article.doi),
  ]

  return candidates.map((x) => String(x || "").trim()).find(Boolean) || ""
}

function getStatusText(status: any | null, directStatus: any | null) {
  const direct = String(directStatus?.full_text_status || directStatus?.status || "")
  const full = String(status?.full_text_status || status?.status || "")

  if (direct === "text_extracted" || full === "text_extracted") return "Texte extrait"
  if (full === "pdf_url_available") return "PDF trouvé"
  if (direct === "pdf_url_found_but_download_blocked" || full === "pdf_url_found_but_download_blocked") return "PDF bloqué"
  if (full === "no_pdf_url_found" || direct === "no_pdf_url_found") return "PDF introuvable"
  if (status?.needs_consultant_upload === true || directStatus?.needs_consultant_upload === true) return "À uploader"
  if (full === "not_checked" || direct === "not_checked") return "Non vérifié"
  if (!full && !direct) return "Non vérifié"
  return full || direct || "À vérifier"
}

function isTextExtracted(status: any | null, directStatus: any | null) {
  return (
    status?.full_text_status === "text_extracted" ||
    directStatus?.full_text_status === "text_extracted" ||
    Number(status?.text_chars || directStatus?.text_chars || 0) >= 1000
  )
}

function needsPdfUpload(status: any | null, directStatus: any | null) {
  const text = getStatusText(status, directStatus).toLowerCase()
  return (
    text.includes("uploader") ||
    text.includes("introuvable") ||
    text.includes("bloqué") ||
    status?.needs_consultant_upload === true ||
    directStatus?.needs_consultant_upload === true
  )
}

function getArticleGlobalStatus(articleCard: any | null, status: any | null, directStatus: any | null) {
  const cardOk = Boolean(articleCard && articleCard?.quality_guard?.status !== "invalid" && articleCard?.status !== "card_generation_error")
  const extracted = isTextExtracted(status, directStatus)
  const statusText = getStatusText(status, directStatus)

  if (cardOk && extracted) {
    return {
      label: "Complet",
      className: "bg-success/10 text-success border-success/30",
      detail: "Article Card + texte intégral récupéré.",
    }
  }

  if (cardOk && statusText === "PDF trouvé") {
    return {
      label: "PDF trouvé",
      className: "bg-brand/10 text-brand border-brand/30",
      detail: "Relance l’extraction directe pour obtenir le texte.",
    }
  }

  if (cardOk && needsPdfUpload(status, directStatus)) {
    return {
      label: "PDF à uploader",
      className: "bg-warning/10 text-warning border-warning/30",
      detail: "Le consultant doit télécharger le PDF puis l’uploader ici.",
    }
  }

  if (!articleCard) {
    return {
      label: "Carte manquante",
      className: "bg-destructive/10 text-destructive border-destructive/30",
      detail: "Construis ou reconstruis les Article Cards.",
    }
  }

  return {
    label: statusText,
    className: "bg-muted text-muted-foreground border-border",
    detail: "Statut à vérifier.",
  }
}

// ============================================================
// COMPOSANT StateOfArtPreparationSection (MODIFIÉ)
// ============================================================
function StateOfArtPreparationSection({
  projectId,
  selectedArticles,
  selectionPreview,
  articleCardsPayload,
  fulltextStatus,
  directExtractStatus,
  loading,
  preparationStage,
  uploadingArticleId,
  error,
  onRefresh,
  onPrepareAll,
  onUploadArticlePdf,
  onNext,
}: {
  projectId: number
  selectedArticles: ArticleRead[]
  selectionPreview: any | null
  articleCardsPayload: any | null
  fulltextStatus: any | null
  directExtractStatus: any | null
  loading: boolean
  preparationStage: string
  uploadingArticleId: number | null
  error: string
  onRefresh: () => void
  onPrepareAll: () => void
  onUploadArticlePdf: (articleId: number, file: File) => void
  onNext: () => void
}) {
  const cards = getArticleCardsList(articleCardsPayload)
  const fullResults = getStatusResults(fulltextStatus)
  const directResults = getStatusResults(directExtractStatus)

  const selectedCount =
    selectedArticles.length ||
    Number(selectionPreview?.selected_articles_count || selectionPreview?.summary?.selected_articles_count || 0) ||
    countArray(selectionPreview?.selected_articles, ["articles", "items"])

  const cardsCount = Math.max(
    Number(articleCardsPayload?.cards_count || 0),
    Number(articleCardsPayload?.article_cards_count || 0),
    Number(articleCardsPayload?.summary?.cards_count || 0),
    cards.length,
  )

  const extractedFromRows = selectedArticles.filter((article) =>
    isTextExtracted(
      findByArticleId(fullResults, article.id),
      findByArticleId(directResults, article.id),
    )
  ).length

  const extractedCount = Math.max(
    Number(fulltextStatus?.text_extracted_count || 0),
    Number(fulltextStatus?.summary?.text_extracted_count || 0),
    Number(directExtractStatus?.text_extracted_count || 0),
    Number(directExtractStatus?.summary?.text_extracted_count || 0),
    extractedFromRows,
  )

  const uploadNeededCount = selectedArticles.filter((article) =>
    needsPdfUpload(findByArticleId(fullResults, article.id), findByArticleId(directResults, article.id))
  ).length

  const completeCount = selectedArticles.filter((article) => {
    const card = findByArticleId(cards, article.id)
    const full = findByArticleId(fullResults, article.id)
    const direct = findByArticleId(directResults, article.id)
    return getArticleGlobalStatus(card, full, direct).label === "Complet"
  }).length

  const canGoNext = selectedCount > 0 && completeCount >= selectedCount

  return (
    <div className="space-y-4">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <BookOpen className="size-4 text-brand" />
            Préparation de l’état de l’art
          </CardTitle>
          <CardDescription>
            Vérifie chaque article gardé par le consultant : Article Card, PDF, texte intégral, upload manuel si nécessaire, puis relance le contrôle avant la rédaction finale. Dossier #{projectId}.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-2">
            <Button
              size="sm"
              className="bg-brand hover:bg-brand/90"
              disabled={loading || selectedCount === 0}
              onClick={onPrepareAll}
              title="Relance réellement la préparation : sélection, résolution fulltext, extraction/OCR et reconstruction des Article Cards."
            >
              {loading && preparationStage !== "refresh" ? (
                <Loader2 className="size-4 mr-2 animate-spin" />
              ) : (
                <FileText className="size-4 mr-2" />
              )}
              {loading && preparationStage !== "refresh"
                ? `Recalcul des ${selectedCount} articles...`
                : "Préparer / recalculer les articles"}
            </Button>

            <Button
              size="sm"
              variant="outline"
              disabled={loading}
              onClick={onRefresh}
              title="Recharge seulement les statuts déjà enregistrés, sans relancer l’extraction ni les Article Cards."
            >
              {loading && preparationStage === "refresh" ? (
                <Loader2 className="size-4 mr-2 animate-spin" />
              ) : (
                <RefreshCw className="size-4 mr-2" />
              )}
              {loading && preparationStage === "refresh"
                ? "Actualisation..."
                : "Actualiser les statuts"}
            </Button>

            <Button
              size="sm"
              variant="outline"
              disabled={loading || !canGoNext}
              onClick={onNext}
            >
              Suivant : état de l’art
            </Button>
          </div>

          <div className="rounded-md border border-brand/20 bg-background p-3 text-sm">
            <div className="flex items-center gap-2">
              {loading ? (
                <Loader2 className="size-4 animate-spin text-brand" />
              ) : preparationStage === "done" ? (
                <CheckCircle2 className="size-4 text-success" />
              ) : (
                <BookOpen className="size-4 text-brand" />
              )}

            </div>
          </div>

          {error && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
              {error}
            </div>
          )}

          <div className="grid gap-3 md:grid-cols-5">
            <div className="rounded-lg border bg-background p-3">
              <p className="text-xs text-muted-foreground">Articles sélectionnés</p>
              <p className="text-2xl font-semibold">{selectedCount || "—"}</p>
              <Badge variant="outline" className={statusBadge(selectionPreview?.ok)}>
                {selectionPreview ? (selectionPreview?.ok === false ? "À vérifier" : "OK") : "Non chargé"}
              </Badge>
            </div>

            <div className="rounded-lg border bg-background p-3">
              <p className="text-xs text-muted-foreground">Article Cards</p>
              <p className="text-2xl font-semibold">{cardsCount || "—"}</p>
              <Badge variant="outline" className={statusBadge(articleCardsPayload?.ok)}>
                {articleCardsPayload ? (articleCardsPayload?.ok === false ? "À reconstruire" : "OK") : "Non chargé"}
              </Badge>
            </div>

            <div className="rounded-lg border bg-background p-3">
              <p className="text-xs text-muted-foreground">Textes extraits</p>
              <p className="text-2xl font-semibold">{extractedCount || "—"}</p>
              <Badge variant="outline" className="bg-muted text-muted-foreground border-border">
                Fulltext
              </Badge>
            </div>

            <div className="rounded-lg border bg-background p-3">
              <p className="text-xs text-muted-foreground">À uploader</p>
              <p className="text-2xl font-semibold">{uploadNeededCount}</p>
              <Badge variant="outline" className={uploadNeededCount ? "bg-warning/10 text-warning border-warning/30" : "bg-success/10 text-success border-success/30"}>
                {uploadNeededCount ? "Action requise" : "OK"}
              </Badge>
            </div>

            <div className="rounded-lg border bg-background p-3">
              <p className="text-xs text-muted-foreground">Articles complets</p>
              <p className="text-2xl font-semibold">{completeCount}/{selectedCount || 0}</p>
              <Badge variant="outline" className={completeCount === selectedCount && selectedCount > 0 ? "bg-success/10 text-success border-success/30" : "bg-muted text-muted-foreground border-border"}>
                Validation
              </Badge>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <FileText className="size-4 text-brand" />
            Articles sélectionnés à contrôler
          </CardTitle>
          <CardDescription>
            Chaque article gardé doit avoir une Article Card. Si le texte intégral n’est pas récupéré automatiquement, le consultant clique sur le lien, télécharge le PDF, puis l’upload dans la ligne de l’article.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {selectedArticles.length === 0 ? (
            <div className="rounded-md border bg-muted/20 p-4 text-sm text-muted-foreground">
              Aucun article gardé pour l’instant. Retourne dans l’onglet Sélection et garde les articles utiles.
            </div>
          ) : (
            selectedArticles.map((article, index) => {
              const card = findByArticleId(cards, article.id)
              const full = findByArticleId(fullResults, article.id)
              const direct = findByArticleId(directResults, article.id)
              const global = getArticleGlobalStatus(card, full, direct)
              const bestLink = getBestArticleLink(article, full, direct)
              const statusText = getStatusText(full, direct)
              const textChars = Number(full?.text_chars || direct?.text_chars || card?.evidence?.text_chars || 0)
              const isUploading = uploadingArticleId === article.id

              return (
                <div key={article.id} className="rounded-lg border bg-background p-4 space-y-3">
                  <div className="flex flex-col gap-2 md:flex-row md:items-start md:justify-between">
                    <div className="min-w-0 space-y-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <Badge variant="outline" className="bg-muted text-muted-foreground border-border">
                          A{index + 1}
                        </Badge>
                        <Badge variant="outline" className={tagClass(article.tag_article)}>
                          {normalizeTag(article.tag_article)}
                        </Badge>
                        <Badge variant="outline" className={global.className}>
                          {global.label}
                        </Badge>
                      </div>

                      <p className="font-medium leading-snug text-foreground">
                        {article.title || `Article ${index + 1}`}
                      </p>

                      <p className="text-xs text-muted-foreground">
                        Source : {article.source || "—"} · Année : {article.year || "—"} · Score : {formatScore(article.score)}
                      </p>
                    </div>

                    <div className="flex flex-wrap gap-2 md:justify-end">
                      {bestLink && (
                        <a
                            href={bestLink}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex h-9 items-center justify-center rounded-md border px-3 text-sm font-medium hover:bg-muted"
                          >
                            <ExternalLink className="size-4 mr-2" />
                            Ouvrir / télécharger
                          </a>
                      )}

                      <label className={`inline-flex h-9 cursor-pointer items-center justify-center rounded-md border px-3 text-sm font-medium ${isUploading ? "opacity-60 pointer-events-none" : "hover:bg-muted"}`}>
                        {isUploading ? <Loader2 className="size-4 mr-2 animate-spin" /> : <FileText className="size-4 mr-2" />}
                        Upload PDF
                        <input
                          type="file"
                          accept="application/pdf"
                          className="hidden"
                          disabled={loading || isUploading}
                          onChange={(event) => {
                            const file = event.target.files?.[0]
                            event.target.value = ""
                            if (file) onUploadArticlePdf(article.id, file)
                          }}
                        />
                      </label>
                    </div>
                  </div>

                  <div className="grid gap-2 md:grid-cols-4 text-xs">
                    <div className="rounded-md border bg-muted/20 p-2">
                      <p className="text-muted-foreground">Article Card</p>
                      <p className="font-medium">{card ? card?.quality_guard?.status || card?.status || "créée" : "manquante"}</p>
                    </div>
                    <div className="rounded-md border bg-muted/20 p-2">
                      <p className="text-muted-foreground">PDF / lien</p>
                      <p className="font-medium">{statusText}</p>
                    </div>
                    <div className="rounded-md border bg-muted/20 p-2">
                      <p className="text-muted-foreground">Texte extrait</p>
                      <p className="font-medium">{textChars ? `${textChars.toLocaleString("fr-FR")} caractères` : "—"}</p>
                    </div>
                    <div className="rounded-md border bg-muted/20 p-2">
                      <p className="text-muted-foreground">Action</p>
                      <p className="font-medium">{global.detail}</p>
                    </div>
                  </div>

                  {(full?.reason || full?.message || direct?.reason || direct?.message) && (
                    <p className="text-xs text-muted-foreground">
                      Détail : {full?.reason || full?.message || direct?.reason || direct?.message}
                    </p>
                  )}
                </div>
              )
            })
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Détails techniques Phase 1/2</CardTitle>
          <CardDescription>
            Affichage de contrôle. Tu peux masquer cette partie plus tard.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <details className="rounded-md border bg-muted/20 p-3">
            <summary className="cursor-pointer text-xs font-semibold">Selection payload</summary>
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{compactJson(selectionPreview)}</pre>
          </details>
          <details className="rounded-md border bg-muted/20 p-3">
            <summary className="cursor-pointer text-xs font-semibold">Article Cards payload</summary>
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{compactJson(articleCardsPayload)}</pre>
          </details>
          <details className="rounded-md border bg-muted/20 p-3">
            <summary className="cursor-pointer text-xs font-semibold">Fulltext status</summary>
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{compactJson(fulltextStatus)}</pre>
          </details>
          <details className="rounded-md border bg-muted/20 p-3">
            <summary className="cursor-pointer text-xs font-semibold">Direct extract status</summary>
            <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs">{compactJson(directExtractStatus)}</pre>
          </details>
        </CardContent>
      </Card>
    </div>
  )
}

function StateOfArtHistorySection({
  entries,
  selectedEntry,
  selectedRunId,
  onSelect,
  projectId,
  onGenerate,
  generating,
  generateError,
  generatedResult,
  latestResult,
}: {
  entries: any[]
  selectedEntry: any | null
  selectedRunId: string | null
  onSelect: (value: string) => void
  projectId: number
  onGenerate: () => void
  generating: boolean
  generateError: string
  generatedResult?: any | null
  latestResult?: any | null
}) {
  const currentEntry = selectedEntry || null
  const summary = currentEntry?.summary || generatedResult?.status || {}
  const report = currentEntry?.report || generatedResult?.state_of_art_view || generatedResult || latestResult?.state_of_art_view || latestResult?.report || null
  const selectionPayload = currentEntry?.selection_payload || null
  const generatedMarkdown = getGeneratedMarkdown(currentEntry) || getGeneratedMarkdown(report)
  const verrousWritten = Number(summary?.verrous_written || report?.verrous_written || report?.drafts?.length || 0)
  const selectedArticlesCount = Number(summary?.selected_articles_count || 0)
  const usedCitationsCount = Number(summary?.used_citations_count || report?.citations_used?.length || 0)
  const results = Array.isArray(report?.results) ? report.results : []

  return (
    <div className="space-y-4">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <FileText className="size-4 text-brand" />
            Génération finale de l’état de l’art
          </CardTitle>
          <CardDescription>
            Lance la rédaction après sélection consultant des articles. Le résultat s’affiche ici, dans la section état de l’art.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-3">
          <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <div>
              <p className="text-sm font-semibold text-foreground">
                État de l’art du dossier
              </p>
              <p className="text-xs text-muted-foreground">
                Pipeline backend : Phase 3 → Phase 4.5 → Phase 4.6 → Phase 5.
              </p>
            </div>

            <Button
              size="sm"
              className="bg-brand hover:bg-brand/90"
              disabled={generating}
              onClick={onGenerate}
            >
              {generating ? (
                <>
                  <Loader2 className="size-4 mr-2 animate-spin" />
                  Génération...
                </>
              ) : (
                <>
                  <FileText className="size-4 mr-2" />
                  Générer l’état de l’art
                </>
              )}
            </Button>
          </div>

          {generateError && (
            <div className="rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
              {generateError}
            </div>
          )}
        </CardContent>
      </Card>

      {!entries.length && !generatedMarkdown && !report ? (
        <Card>
          <CardHeader>
            <CardTitle>États de l’art rédigés</CardTitle>
            <CardDescription>
              Aucun état de l’art rédigé n’a encore été trouvé pour ce projet.
            </CardDescription>
          </CardHeader>
          <CardContent className="text-sm text-muted-foreground">
            Clique sur “Générer l’état de l’art” après avoir gardé les articles utiles.
          </CardContent>
        </Card>
      ) : (
        <>
          <Card>
            <CardHeader>
              <CardTitle>États de l’art rédigés</CardTitle>
              <CardDescription>
                Historique des rédactions sauvegardées et dernier résultat généré.
              </CardDescription>
            </CardHeader>

            <CardContent className="space-y-4">
              {entries.length > 0 && (
                <div className="grid gap-3 md:grid-cols-[1fr_auto] md:items-end">
                  <div className="space-y-2">
                    <label className="text-xs font-medium text-muted-foreground">
                      Rédaction disponible
                    </label>
                    <select
                      value={selectedRunId || String(entries[0]?.run_id || "")}
                      onChange={(event) => onSelect(event.target.value)}
                      className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    >
                      {entries.map((entry) => {
                        const s = entry.summary || {}
                        return (
                          <option key={String(entry.run_id)} value={String(entry.run_id)}>
                            {formatStateArtDate(entry.generated_at || entry.updated_at)} — {Number(s.verrous_written || 0)} verrou(x) — {Number(s.selected_articles_count || 0)} article(s)
                          </option>
                        )
                      })}
                    </select>
                  </div>

                  <div className="text-xs text-muted-foreground">
                    {entries.length} rédaction(s) trouvée(s)
                  </div>
                </div>
              )}

              <div className="grid gap-3 md:grid-cols-4">
                <div className="rounded-lg border bg-background p-3">
                  <p className="text-xs text-muted-foreground">Verrous rédigés</p>
                  <p className="text-2xl font-semibold">{verrousWritten || "—"}</p>
                </div>
                <div className="rounded-lg border bg-background p-3">
                  <p className="text-xs text-muted-foreground">Articles sélectionnés</p>
                  <p className="text-2xl font-semibold">{selectedArticlesCount || "—"}</p>
                </div>
                <div className="rounded-lg border bg-background p-3">
                  <p className="text-xs text-muted-foreground">Articles cités</p>
                  <p className="text-2xl font-semibold">{usedCitationsCount || "—"}</p>
                </div>
                <div className="rounded-lg border bg-background p-3">
                  <p className="text-xs text-muted-foreground">Validation</p>
                  <p className="text-sm font-semibold">
                    {summary?.strict === true || summary?.strict_ok === true ? "Strict OK" : summary?.phase5_ok ? "Phase 5 OK" : "—"}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    LLM : {summary?.llm_used || report?.llm_used ? "oui" : "non"}
                  </p>
                </div>
              </div>

              {(currentEntry?.state_of_art_report_path || report?.paths?.state_of_art_markdown) && (
                <div className="rounded-lg border bg-muted/30 p-3 text-xs text-muted-foreground">
                  <p className="font-medium text-foreground">Traçabilité</p>
                  <p className="mt-1 break-all">
                    {currentEntry?.state_of_art_report_path || report?.paths?.state_of_art_markdown}
                  </p>
                </div>
              )}

              {results.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-medium text-muted-foreground">Verrous contenus dans cette rédaction</p>
                  <div className="flex flex-wrap gap-2">
                    {results.map((result: any, idx: number) => (
                      <Badge key={idx} variant="outline" className="max-w-full whitespace-normal text-left">
                        {result?.verrou_title || result?.structured_state_of_art?.verrou_title || `Verrou ${idx + 1}`}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          <StateOfArtEvidenceChainsPanel report={report} />

          {generatedMarkdown ? (
            <Card className="border-brand/20">
              <CardHeader>
                <CardTitle className="text-sm flex items-center gap-2">
                  <FileText className="size-4 text-brand" />
                  Texte généré
                </CardTitle>
                <CardDescription>
                  Markdown final retourné par le backend.
                </CardDescription>
              </CardHeader>
              <CardContent>
                <div className="rounded-md border bg-white p-4">
                  <pre className="whitespace-pre-wrap break-words font-sans text-sm leading-7 text-foreground">
                    {v51MarkdownText(generatedMarkdown)}
                  </pre>
                </div>
              </CardContent>
            </Card>
          ) : report ? (
            <div className="space-y-4">
              <StateOfArtInlineGeneratedPanel report={report} />

              <EnnoScholarStructuredStateArtPanel
                key={String(currentEntry?.run_id || "state-art")}
                projectId={projectId}
                initialReport={report}
                selectionPayload={selectionPayload}
              />
            </div>
          ) : (
            <Card>
              <CardContent className="p-5 text-sm text-muted-foreground">
                Rapport introuvable ou illisible.
              </CardContent>
            </Card>
          )}
        </>
      )}
    </div>
  )
}


type ScholarFoundCounts = {
  total: number
  direct: number
  connexe: number
  fondamental: number
  technique: number
  horsSujet: number
  autres: number
  fromReport: boolean
}

function numberValue(value: any): number {
  const n = Number(value)
  return Number.isFinite(n) && n > 0 ? n : 0
}

function firstNonEmptyArrayLocal(...values: any[]): any[] {
  for (const value of values) {
    if (Array.isArray(value) && value.length > 0) return value
  }
  return []
}

function getScholarResultsForStats(bundle: any): any[] {
  return firstNonEmptyArrayLocal(
    bundle?.bundle?.payload?.results,
    bundle?.bundle?.report?.results,
    bundle?.latest_run?.raw_result_json?.payload?.results,
    bundle?.latest_run?.raw_result_json?.report?.results,
    bundle?.latest_run?.raw_result_json?.results,
    bundle?.raw_result_json?.payload?.results,
    bundle?.raw_result_json?.report?.results,
    bundle?.payload?.results,
    bundle?.report?.results,
    bundle?.results,
  )
}

function getResultArticlesForStats(result: any): any[] {
  return firstNonEmptyArrayLocal(
    result?.articles,
    result?.selected_articles,
    result?.items,
    result?.results,
  )
}

function tagFromScholarArticleForStats(article: any): string {
  return normalizeTag(
    article?.tag_article ||
      article?.tag ||
      article?.role ||
      article?.source_json?.tag_article ||
      article?.source_json?.tag ||
      article?.article_summary?.tag_recommande ||
      "Non classé"
  )
}

function computeScholarSearchCounts(bundle: any, fallbackArticles: ArticleRead[]): ScholarFoundCounts {
  const results = getScholarResultsForStats(bundle)

  if (results.length > 0) {
    const out: ScholarFoundCounts = {
      total: 0,
      direct: 0,
      connexe: 0,
      fondamental: 0,
      technique: 0,
      horsSujet: 0,
      autres: 0,
      fromReport: true,
    }

    let tagTotal = 0

    for (const result of results) {
      out.total += numberValue(result?.articles_found)
      out.technique += numberValue(result?.technical_sources_added)

      for (const article of getResultArticlesForStats(result)) {
        const tag = tagFromScholarArticleForStats(article)
        tagTotal += 1

        if (tag === "Direct") out.direct += 1
        else if (tag === "Connexe") out.connexe += 1
        else if (tag === "Fondamental") out.fondamental += 1
        else if (tag === "Technique") out.technique += 1
        else if (tag === "Hors sujet") out.horsSujet += 1
        else out.autres += 1
      }
    }

    if (out.total <= 0) out.total = tagTotal
    return out
  }

  const fallback = countArticlesByTag(fallbackArticles)
  return {
    total: fallback.total,
    direct: fallback.direct,
    connexe: fallback.connexe,
    fondamental: fallback.fondamental,
    technique: fallback.technique,
    horsSujet: fallback.horsSujet,
    autres: fallback.autres,
    fromReport: false,
  }
}


function getScholarReportArticlesForDisplay(bundle: any): ArticleRead[] {
  const results = getScholarResultsForStats(bundle)
  const out: ArticleRead[] = []
  const seen = new Set<string>()

  results.forEach((result: any, resultIndex: number) => {
    const verrouTitle = v46Text(result?.verrou_title || result?.title || `Verrou ${resultIndex + 1}`)
    const verrouId = result?.verrou_id || result?.id || resultIndex + 1

    getResultArticlesForStats(result).forEach((raw: any, articleIndex: number) => {
      const title = v46Text(raw?.title || raw?.article_title || `Article ${articleIndex + 1}`)
      const doi = v46Text(raw?.doi || raw?.source_json?.doi || "")
      const url = v46Text(raw?.url || raw?.source_json?.url || "")
      const paperId = v46Text(raw?.paper_id || raw?.paperId || raw?.source_json?.paper_id || "")
      const key = v46Norm(doi || url || paperId || title)

      if (!key || seen.has(key)) return
      seen.add(key)

      const tag = normalizeTag(
        raw?.tag_article ||
          raw?.tag ||
          raw?.role ||
          raw?.source_json?.tag_article ||
          raw?.source_json?.tag ||
          raw?.article_summary?.tag_recommande ||
          "Non classé"
      )

      const dbId = Number(raw?.article_id || raw?.db_article_id || raw?.id || raw?.source_json?.article_id || 0)
      const id = Number.isFinite(dbId) && dbId > 0 ? dbId : -Math.abs((resultIndex + 1) * 100000 + articleIndex + 1)

      out.push({
        id,
        scholar_run_id: Number(raw?.scholar_run_id || 0),
        verrou_id: Number(raw?.verrou_id || verrouId || 0) || null,
        title,
        year: raw?.year === null || raw?.year === undefined || raw?.year === "" ? null : Number(raw.year),
        source: v46Text(raw?.source || raw?.venue || raw?.source_json?.source || "—"),
        tag_article: tag,
        score: raw?.score === null || raw?.score === undefined
          ? Number(raw?.relevance_score ?? raw?.score_details?.relevance_score_before_rerank ?? 0) || null
          : Number(raw.score),
        url: url || null,
        doi: doi || null,
        consultant_status: v46Text(raw?.consultant_status || raw?.source_json?.consultant_status || "en_attente"),
        source_json: {
          ...(raw?.source_json && typeof raw.source_json === "object" ? raw.source_json : {}),
          ...raw,
          verrou_title: verrouTitle,
          verrou_id: verrouId,
          frontend_result_index: resultIndex,
          frontend_result_key: `result:${resultIndex}:${v46Norm(verrouTitle).slice(0, 120)}`,
          frontend_articles_found: numberValue(result?.articles_found),
          frontend_raw_articles_retrieved: numberValue(result?.raw_articles_retrieved),
          frontend_technical_sources_added: numberValue(result?.technical_sources_added),
          frontend_report_only: !(Number.isFinite(dbId) && dbId > 0),
        },
        created_at: v46Text(raw?.created_at || raw?.generated_at || ""),
      } as ArticleRead)
    })
  })

  return out
}

function countUsefulFromFoundCounts(counts: ScholarFoundCounts): number {
  return Number(counts.direct || 0) + Number(counts.connexe || 0) + Number(counts.fondamental || 0)
}



type ScholarVerrouForCrossMap = {
  verrou_id: string
  verrou_number: number
  verrou_title: string
  verrou_text: string
}

function buildScholarVerrousForCrossMapFromArticles(articles: ArticleRead[]): ScholarVerrouForCrossMap[] {
  const map = new Map<string, ScholarVerrouForCrossMap>()

  for (const article of articles || []) {
    const sj: any = article.source_json || {}
    const rawIndex = Number(sj.frontend_result_index)
    const number = Number.isFinite(rawIndex) && rawIndex >= 0 ? rawIndex + 1 : Number(sj.verrou_number || 0) || 0
    const id = v46Text(sj.verrou_id || article.verrou_id || number || "")
    const title = v46Text(
      sj.verrou_title ||
        sj.scientific_intent?.verrou_title ||
        sj.query_context?.verrou_title ||
        (article as any).verrou_title ||
        `Verrou ${number || "?"}`
    )
    const text = v46Text(
      sj.verrou_text ||
        sj.scientific_intent?.scientific_problem ||
        sj.scientific_intent?.technical_object ||
        sj.query_context?.verrou_text ||
        title
    )
    const key = id ? `id:${id}` : `n:${number}:${v46Norm(title)}`
    if (!map.has(key)) {
      map.set(key, {
        verrou_id: id || String(number || key),
        verrou_number: number,
        verrou_title: title,
        verrou_text: text,
      })
    }
  }

  return Array.from(map.values()).sort((a, b) => {
    if (a.verrou_number && b.verrou_number) return a.verrou_number - b.verrou_number
    return v46Norm(a.verrou_title).localeCompare(v46Norm(b.verrou_title))
  })
}

function articleCrossMapText(article: ArticleRead): string {
  const sj: any = article.source_json || {}
  return v46Norm([
    article.title,
    article.source,
    article.tag_article,
    sj.title,
    sj.abstract,
    sj.abstract_fr,
    sj.article_summary?.abstract_original,
    sj.article_summary?.resume_court,
    sj.article_summary?.lien_avec_verrou,
    sj.reason,
    sj.score_details?.matched_anchors,
  ].flat().join(" "))
}

function hasAnyText(text: string, words: string[]): boolean {
  return words.some((w) => text.includes(v46Norm(w)))
}


function isFrontendCoverageInferenceEnabled(): boolean {
  // V49 : par défaut, le frontend n'infère plus les liens article-verrou.
  // La source de vérité doit venir de l'agent EnnoScholar via source_json.covered_verrous.
  // On garde ce flag uniquement pour debug manuel.
  return String(process.env.NEXT_PUBLIC_ENNOSCHOLAR_ENABLE_FRONTEND_COVERAGE_INFERENCE || "")
    .trim()
    .toLowerCase() === "true"
}

function inferArticleCoverageForVerrou(article: ArticleRead, verrou: ScholarVerrouForCrossMap): MultiVerrouInfo | null {
  const articleText = articleCrossMapText(article)
  const verrouText = v46Norm(`${verrou.verrou_title} ${verrou.verrou_text}`)
  const tag = normalizeTag(article.tag_article || article.source_json?.tag || article.source_json?.tag_article || "Non classé")
  const score = Number(article.score || article.source_json?.relevance_score || 0) || 0

  const hasSar = hasAnyText(articleText, ["sar", "synthetic aperture radar", "sar imagery", "sar image"])
  const hasAtr = hasAnyText(articleText, ["atr", "automatic target recognition", "target recognition"])
  const hasRadar = hasAnyText(articleText, ["radar", "mstar", "mocem", "salsa"])
  const hasCoreSarAtr = (hasSar && hasAtr) || hasAnyText(articleText, ["mstar", "mocem", "salsa"])
  const hasProjectTool = hasAnyText(articleText, ["mocem", "salsa"])

  const hasSyntheticSimulation = hasAnyText(articleText, [
    "synthetic", "synthétique", "synthetiques", "synthétiques",
    "simulation", "simulator", "simulateur", "simulated", "mocem", "salsa",
    "synthetic data", "synthetic images", "synthetic training",
  ])

  const hasRealValidation = hasAnyText(articleText, [
    "real measurement", "real measurements", "measured", "measurement", "measurements",
    "real-world", "real world", "operational", "generalization", "generalisation",
    "representative", "representativeness", "validation", "verification", "benchmark",
    "experimental", "domain adaptation", "transfer", "transposability", "robustness",
  ])

  const hasClassificationImage = hasAnyText(articleText, [
    "classification", "recognition", "target recognition", "sar image", "sar imagery",
    "image classification", "segmentation", "feature", "vision transformer", "cnn",
    "deep learning", "classifier", "classifiers",
  ])

  const hasAugmentation = hasAnyText(articleText, [
    "augmentation", "data augmentation", "feature augmenter", "augmenter", "limited training",
    "few-shot", "few shot", "limited data", "domain randomization", "adversarial training",
    "adversarial", "synthetic training", "training data", "self-supervised",
  ])

  const hasCompute = hasAnyText(articleText, [
    "cpu", "gpu", "fpga", "hardware", "accelerator", "latency", "throughput",
    "runtime", "computational", "computation", "inference", "efficient",
    "efficiency", "co-design", "low-complexity", "low latency", "model size",
  ])

  const hasImagingPhysics = hasAnyText(articleText, [
    "scattering", "scatter", "renderer", "rendering", "reconstruction", "despeckling",
    "speckle", "imaging", "image modeling", "gaussian", "backprojection",
    "ray tracing", "mesh", "maillage", "physical", "physics", "parameters",
    "parameter", "azimuth", "polarimetric", "filtered backprojection",
  ])

  let matched = false
  let reason = ""

  // V5 / imagerie / maillage : ce test doit passer AVANT les tests génériques
  // contenant "représentativité", sinon tous les articles SAR/ATR finissent en V5.
  if (hasAnyText(verrouText, ["maillage", "imagerie", "paramètres de maillage", "parametres de maillage", "d'imagerie"])) {
    matched = hasCoreSarAtr && hasImagingPhysics
    reason = "Couverture transversale stricte : imagerie SAR, paramètres physiques, reconstruction, scattering ou rendu."
  }
  // V6 / CPU-GPU / implémentation Salsa.
  else if (hasAnyText(verrouText, ["cpu", "gpu", "implémentation", "implementation", "salsa"])) {
    matched = (hasCompute && (hasCoreSarAtr || hasProjectTool)) || hasProjectTool
    reason = "Couverture transversale stricte : performance CPU/GPU, accélération ou implémentation appliquée au SAR/ATR/Salsa."
  }
  // V7 / augmentation de données.
  else if (hasAnyText(verrouText, ["augmentation de données", "augmentation donnees", "méthodes d'augmentation", "methodes d'augmentation", "d'augmentation"])) {
    matched = hasCoreSarAtr && hasAugmentation
    reason = "Couverture transversale stricte : augmentation de données, few-shot, limited data ou entraînement synthétique en SAR/ATR."
  }
  // V3 / images synthétiques et classification SAR.
  else if (hasAnyText(verrouText, ["images synthétiques", "images synthetiques", "classification sar", "classification"])) {
    matched = hasCoreSarAtr && hasClassificationImage && (hasSyntheticSimulation || hasAugmentation || hasProjectTool)
    reason = "Couverture transversale stricte : classification SAR avec images synthétiques, données limitées ou augmentation."
  }
  // V4 / transposabilité conditions réelles.
  else if (hasAnyText(verrouText, ["transposabilité", "transposabilite", "conditions réelles", "conditions reelles"])) {
    matched = hasCoreSarAtr && (hasRealValidation || hasProjectTool) && (hasSyntheticSimulation || hasAugmentation || hasClassificationImage)
    reason = "Couverture transversale stricte : généralisation, validation ou transposition simulation/réel en SAR/ATR."
  }
  // V2 / représentativité des résultats expérimentaux.
  else if (hasAnyText(verrouText, ["résultats expérimentaux", "resultats experimentaux", "validation globale", "validation expérimentale", "validation experimentale"])) {
    matched = hasCoreSarAtr && (hasRealValidation || hasProjectTool)
    reason = "Couverture transversale stricte : validation expérimentale, benchmark, mesures ou comparaison simulation/réel."
  }
  // V1 / données synthétiques pour entraînement ATR.
  else if (hasAnyText(verrouText, ["données synthétiques", "donnees synthetiques", "entrainement", "entraînement", "training"])) {
    matched = hasCoreSarAtr && (hasSyntheticSimulation || hasAugmentation || hasProjectTool)
    reason = "Couverture transversale stricte : données synthétiques, simulation ou entraînement SAR/ATR."
  }

  if (!matched) return null

  return {
    verrou_id: verrou.verrou_id,
    verrou_number: verrou.verrou_number,
    verrou_title: verrou.verrou_title,
    tag,
    relevance_score: score,
    reason,
  }
}

type MultiVerrouInfo = {
  verrou_id: string
  verrou_number: number
  verrou_title: string
  tag: string
  relevance_score: number
  reason: string
}

function tagPriorityForMultiVerrou(tag: any): number {
  const normalized = normalizeTag(String(tag || ""))
  if (normalized === "Direct") return 5
  if (normalized === "Connexe") return 4
  if (normalized === "Fondamental") return 3
  if (normalized === "Technique") return 2
  if (normalized === "Hors sujet") return 1
  return 0
}

function decisionPriorityForMultiVerrou(status: ArticleDecision): number {
  if (status === "garde") return 3
  if (status === "rejete") return 2
  return 1
}

function mergeConsultantStatusForMultiVerrou(a: ArticleDecision, b: ArticleDecision): ArticleDecision {
  return decisionPriorityForMultiVerrou(b) > decisionPriorityForMultiVerrou(a) ? b : a
}

function getCoveredVerrous(article: ArticleRead): MultiVerrouInfo[] {
  const sj: any = article.source_json || {}
  const topLevelCovered = Array.isArray((article as any).covered_verrous) ? (article as any).covered_verrous : []
  const sourceJsonCovered = Array.isArray(sj.covered_verrous) ? sj.covered_verrous : []
  const existing = [...sourceJsonCovered, ...topLevelCovered]

  const verrouTitle = v46Text(
    sj.verrou_title ||
      sj.scientific_intent?.verrou_title ||
      sj.query_context?.verrou_title ||
      (article as any).verrou_title ||
      "Verrou scientifique non identifié"
  )

  const rawNumber = Number(sj.frontend_result_index)
  const verrouNumber = Number.isFinite(rawNumber) && rawNumber >= 0 ? rawNumber + 1 : Number(sj.verrou_number || 0) || 0
  const verrouId = v46Text(sj.verrou_id || (article as any).verrou_id || verrouNumber || "")

  const current: MultiVerrouInfo = {
    verrou_id: verrouId || String(verrouNumber || ""),
    verrou_number: verrouNumber,
    verrou_title: verrouTitle,
    tag: normalizeTag(article.tag_article || sj.tag || sj.tag_article || "Non classé"),
    relevance_score: Number(article.score || sj.relevance_score || 0) || 0,
    reason: v46Text(sj.reason || sj.alignment_reason || ""),
  }

  const out: MultiVerrouInfo[] = []
  const seen = new Set<string>()

  for (const item of [...existing, current]) {
    if (!item) continue
    const info: MultiVerrouInfo = {
      verrou_id: v46Text(item.verrou_id || item.id || item.db_verrou_id || ""),
      verrou_number: Number(item.verrou_number || item.number || item.index || 0) || 0,
      verrou_title: v46Text(item.verrou_title || item.title || item.label || verrouTitle),
      tag: normalizeTag(item.tag || item.tag_article || "Non classé"),
      relevance_score: Number(item.relevance_score || item.score || 0) || 0,
      reason: v46Text(item.reason || ""),
    }

    const key = info.verrou_id
      ? `id:${info.verrou_id}`
      : `title:${v46Norm(info.verrou_title)}:${info.verrou_number}`

    if (!key || seen.has(key)) continue
    seen.add(key)
    out.push(info)
  }

  return out.sort((a, b) => {
    if (Number(a.verrou_number || 0) && Number(b.verrou_number || 0)) {
      return Number(a.verrou_number || 0) - Number(b.verrou_number || 0)
    }
    return v46Norm(a.verrou_title).localeCompare(v46Norm(b.verrou_title))
  })
}

function buildMultiVerrouArticles(articles: ArticleRead[]): ArticleRead[] {
  const map = new Map<string, ArticleRead>()
  const allVerrousForCrossMap = buildScholarVerrousForCrossMapFromArticles(articles)

  for (const article of articles || []) {
    const key = getArticleUniqueKey(article)
    if (!key) continue

    const currentCovered = getCoveredVerrous(article)
    const currentCoveredKeys = new Set(currentCovered.map((info) => info.verrou_id ? `id:${info.verrou_id}` : `title:${v46Norm(info.verrou_title)}:${info.verrou_number}`))

    // V49 : plus d'inférence métier active côté frontend.
    // Les badges « Couvre Vx » affichent uniquement les liens vérifiés par l'agent
    // dans source_json.covered_verrous / covered_verrous.
    if (isFrontendCoverageInferenceEnabled()) {
      for (const verrou of allVerrousForCrossMap) {
        const key = verrou.verrou_id ? `id:${verrou.verrou_id}` : `title:${v46Norm(verrou.verrou_title)}:${verrou.verrou_number}`
        if (currentCoveredKeys.has(key)) continue
        const inferred = inferArticleCoverageForVerrou(article, verrou)
        if (inferred) {
          currentCovered.push({
            ...inferred,
            reason: `${inferred.reason} — inférence frontend debug, non source de vérité agent.`,
          })
          currentCoveredKeys.add(key)
        }
      }
    }
    const currentStatus = getConsultantStatus(article)
    const currentTag = normalizeTag(article.tag_article || article.source_json?.tag || article.source_json?.tag_article || "Non classé")
    const currentScore = Number(article.score || article.source_json?.relevance_score || 0) || 0

    if (!map.has(key)) {
      map.set(key, {
        ...article,
        consultant_status: currentStatus,
        tag_article: currentTag,
        score: currentScore || article.score,
        source_json: {
          ...(article.source_json || {}),
          covered_verrous: currentCovered,
          multi_verrou_article: true,
          multi_verrou_count: currentCovered.length,
          multi_verrou_key: key,
        },
      } as ArticleRead)
      continue
    }

    const existing = map.get(key)!
    const existingSj: any = existing.source_json || {}
    const existingCovered = getCoveredVerrous(existing)
    const mergedCoveredMap = new Map<string, MultiVerrouInfo>()

    for (const info of [...existingCovered, ...currentCovered]) {
      const infoKey = info.verrou_id
        ? `id:${info.verrou_id}`
        : `title:${v46Norm(info.verrou_title)}:${info.verrou_number}`
      const old = mergedCoveredMap.get(infoKey)
      if (!old || Number(info.relevance_score || 0) > Number(old.relevance_score || 0)) {
        mergedCoveredMap.set(infoKey, info)
      }
    }

    const mergedCovered = Array.from(mergedCoveredMap.values()).sort((a, b) => {
      if (Number(a.verrou_number || 0) && Number(b.verrou_number || 0)) {
        return Number(a.verrou_number || 0) - Number(b.verrou_number || 0)
      }
      return v46Norm(a.verrou_title).localeCompare(v46Norm(b.verrou_title))
    })

    const existingStatus = getConsultantStatus(existing)
    const mergedStatus = mergeConsultantStatusForMultiVerrou(existingStatus, currentStatus)

    const existingTag = normalizeTag(existing.tag_article || existingSj.tag || existingSj.tag_article || "Non classé")
    const shouldReplaceMainArticle =
      tagPriorityForMultiVerrou(currentTag) > tagPriorityForMultiVerrou(existingTag) ||
      (tagPriorityForMultiVerrou(currentTag) === tagPriorityForMultiVerrou(existingTag) && currentScore > Number(existing.score || 0)) ||
      (isReportOnlyArticle(existing) && !isReportOnlyArticle(article))

    const base = shouldReplaceMainArticle ? article : existing
    const baseSj: any = base.source_json || {}

    map.set(key, {
      ...base,
      consultant_status: mergedStatus,
      tag_article: shouldReplaceMainArticle ? currentTag : existingTag,
      score: Math.max(Number(existing.score || 0), currentScore) || base.score,
      source_json: {
        ...baseSj,
        covered_verrous: mergedCovered,
        multi_verrou_article: true,
        multi_verrou_count: mergedCovered.length,
        multi_verrou_key: key,
        merged_consultant_status: mergedStatus,
      },
    } as ArticleRead)
  }

  return Array.from(map.values()).sort((a, b) => {
    const av = getCoveredVerrous(a).length
    const bv = getCoveredVerrous(b).length
    if (bv !== av) return bv - av

    const tagDiff = tagPriorityForMultiVerrou(b.tag_article) - tagPriorityForMultiVerrou(a.tag_article)
    if (tagDiff !== 0) return tagDiff

    return Number(b.score || 0) - Number(a.score || 0)
  })
}

function getMultiVerrouBadgeLabel(info: MultiVerrouInfo): string {
  if (info.verrou_number && info.verrou_number > 0) return `V${info.verrou_number}`
  if (info.verrou_id) return `V${info.verrou_id}`
  return "V?"
}


function groupArticlesByReportVerrouForDisplay(articles: ArticleRead[]) {
  const groups = new Map<
    string,
    {
      key: string
      title: string
      profile?: string
      reason?: string
      groupedCount?: number
      groupedIds?: string[]
      candidateCount?: number
      rawCount?: number
      technicalSourcesCount?: number
      usefulCount?: number
      signals: string[]
      articles: ArticleRead[]
      seenArticles: Set<string>
    }
  >()

  for (const article of articles || []) {
    const sj: any = article.source_json || {}
    const covered = getCoveredVerrous(article)
    if (covered.length > 0) {
      const articleKey = getArticleUniqueKey(article) || String(article.id)
      for (const info of covered) {
        const key = info.verrou_id ? `verrou:${info.verrou_id}` : `verrou-title:${v46Norm(info.verrou_title)}:${info.verrou_number}`
        const title = info.verrou_title || `Verrou ${info.verrou_number || "?"}`
        if (!groups.has(key)) {
          groups.set(key, {
            key,
            title,
            profile: "multi_verrou_cross_mapping",
            reason: "Articles affichés par appartenance directe ou par couverture transversale détectée automatiquement.",
            groupedCount: 1,
            groupedIds: info.verrou_id ? [String(info.verrou_id)] : [],
            candidateCount: 0,
            rawCount: 0,
            technicalSourcesCount: 0,
            usefulCount: 0,
            signals: [],
            articles: [],
            seenArticles: new Set<string>(),
          })
        }
        const group = groups.get(key)!
        if (!group.seenArticles.has(articleKey)) {
          group.articles.push(article)
          group.seenArticles.add(articleKey)
        }
      }
      continue
    }

    const resultKey = v46Text(sj.frontend_result_key)
    const verrouTitle = v46Text(
      sj.verrou_title ||
        sj.scientific_intent?.verrou_title ||
        sj.query_context?.verrou_title ||
        (article as any).verrou_title ||
        "Verrou scientifique non identifié"
    )
    const key = resultKey || `title:${v46Norm(verrouTitle).slice(0, 160)}`

    if (!groups.has(key)) {
      groups.set(key, {
        key,
        title: verrouTitle,
        profile: "latest_scholar_run",
        reason: "Articles regroupés selon les 7 verrous du dernier rapport EnnoScholar, sans regroupement sémantique supplémentaire.",
        groupedCount: 1,
        groupedIds: sj.verrou_id ? [String(sj.verrou_id)] : [],
        candidateCount: numberValue(sj.frontend_articles_found),
        rawCount: numberValue(sj.frontend_raw_articles_retrieved),
        technicalSourcesCount: numberValue(sj.frontend_technical_sources_added),
        usefulCount: 0,
        signals: [],
        articles: [],
        seenArticles: new Set<string>(),
      })
    }

    const group = groups.get(key)!
    group.candidateCount = Math.max(Number(group.candidateCount || 0), numberValue(sj.frontend_articles_found))
    group.rawCount = Math.max(Number(group.rawCount || 0), numberValue(sj.frontend_raw_articles_retrieved))
    group.technicalSourcesCount = Math.max(Number(group.technicalSourcesCount || 0), numberValue(sj.frontend_technical_sources_added))
    const signal = v46Short(sj.original_title || sj.verrou_title || verrouTitle, 260)
    if (signal && !group.signals.some((x) => v46Norm(x) === v46Norm(signal))) {
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
      usefulCount: group.articles.filter((article) => ["Direct", "Connexe", "Fondamental"].includes(normalizeTag(article.tag_article))).length,
      signals: group.signals.slice(0, 12),
      articles: sortArticles(group.articles),
    }))
    .sort((a, b) => {
      const ai = Number(String(a.key).match(/^result:(\d+)/)?.[1] ?? 999)
      const bi = Number(String(b.key).match(/^result:(\d+)/)?.[1] ?? 999)
      if (ai !== bi) return ai - bi
      return b.articles.length - a.articles.length
    })
}

function getConsultantStatus(article: ArticleRead): ArticleDecision {
  const sj: any = article.source_json || {}
  const raw = v46Text(
    article.consultant_status ||
      sj.consultant_status ||
      sj.consultantStatus ||
      sj.db_consultant_status ||
      sj.consultant_decision ||
      sj.decision_consultant ||
      sj.status_consultant ||
      sj.status ||
      "en_attente"
  )

  const norm = v46Norm(raw)

  if (["garde", "garder", "garde consultant", "gardee", "gardé", "keep", "kept", "selected", "selectionne", "selectionnee", "validé", "valide"].includes(norm)) {
    return "garde"
  }

  if (["rejete", "rejetee", "rejeté", "reject", "rejected", "remove", "removed", "ignore", "ignored"].includes(norm)) {
    return "rejete"
  }

  return "en_attente"
}

function isConsultantKeptArticle(article: ArticleRead) {
  return getConsultantStatus(article) === "garde"
}

function isReportOnlyArticle(article: ArticleRead) {
  const sj: any = article.source_json || {}
  return Boolean(sj.frontend_report_only) || Number(article.id || 0) <= 0
}

function mergeReportArticlesWithDbDecisions(reportArticles: ArticleRead[], dbArticles: ArticleRead[]) {
  if (!reportArticles.length) return dbArticles

  const dbByKey = new Map<string, ArticleRead>()
  for (const dbArticle of dbArticles || []) {
    const key = getArticleUniqueKey(dbArticle)
    if (key && !dbByKey.has(key)) {
      dbByKey.set(key, dbArticle)
    }
  }

  return reportArticles.map((reportArticle) => {
    const key = getArticleUniqueKey(reportArticle)
    const dbArticle = key ? dbByKey.get(key) : undefined

    if (!dbArticle) {
      return {
        ...reportArticle,
        consultant_status: getConsultantStatus(reportArticle),
        source_json: {
          ...(reportArticle.source_json || {}),
          frontend_report_only: true,
          consultant_status_source: "latest_ennoscholar_report_no_db_decision",
        },
      } as ArticleRead
    }

    const dbStatus = getConsultantStatus(dbArticle)

    return {
      ...reportArticle,
      id: dbArticle.id,
      scholar_run_id: dbArticle.scholar_run_id || reportArticle.scholar_run_id,
      verrou_id: dbArticle.verrou_id || reportArticle.verrou_id,
      consultant_status: dbStatus,
      evidence_status: dbArticle.evidence_status || reportArticle.evidence_status,
      evidence_label: dbArticle.evidence_label || reportArticle.evidence_label,
      evidence_usable: dbArticle.evidence_usable ?? reportArticle.evidence_usable,
      fulltext_ready: dbArticle.fulltext_ready ?? reportArticle.fulltext_ready,
      candidate_only: dbArticle.candidate_only ?? reportArticle.candidate_only,
      access_check_status: dbArticle.access_check_status || reportArticle.access_check_status,
      evidence_reason_code: dbArticle.evidence_reason_code || reportArticle.evidence_reason_code,
      evidence_reason_detail: dbArticle.evidence_reason_detail || reportArticle.evidence_reason_detail,
      evidence_recommended_action: dbArticle.evidence_recommended_action || reportArticle.evidence_recommended_action,
      evidence_access_kind: dbArticle.evidence_access_kind || reportArticle.evidence_access_kind,
      source_json: {
        ...(reportArticle.source_json || {}),
        ...(dbArticle.source_json || {}),
        latest_report_source_json: reportArticle.source_json || {},
        db_article_id: dbArticle.id,
        db_consultant_status: dbArticle.consultant_status,
        frontend_report_only: false,
        consultant_status_source: "db_article_decision_merged_into_latest_report",
      },
    } as ArticleRead
  })
}

function getStrictConsultantSelectedArticles(articles: ArticleRead[]) {
  // Important : cette liste est contractuelle.
  // Elle ne doit contenir QUE les articles explicitement gardés par le consultant.
  // On ne fait plus de fallback Direct/Connexe/Fondamental, sinon des articles "En attente"
  // apparaissent à tort dans la section Sélection consultant.
  return sortArticles(articles.filter(isConsultantKeptArticle))
}

function getConsultantDecisionStats(articles: ArticleRead[]) {
  return articles.reduce(
    (acc, article) => {
      const status = getConsultantStatus(article)
      if (status === "garde") acc.garde += 1
      else if (status === "rejete") acc.rejete += 1
      else acc.enAttente += 1
      return acc
    },
    { garde: 0, rejete: 0, enAttente: 0 }
  )
}


// ============================================================
// COMPOSANT PRINCIPAL EnnoScholarPage (MODIFIÉ)
// ============================================================
type EnnoScholarPageProps = {
  onImmersiveModeChange?: (immersive: boolean) => void
}

export function EnnoScholarPage({
  onImmersiveModeChange,
}: EnnoScholarPageProps = {}) {
  const [activeTab, setActiveTab] = useState("par-verrou")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [query, setQuery] = useState("")
  const [showHorsSujet, setShowHorsSujet] = useState(false)
  const [sourceFilter, setSourceFilter] = useState<SourceFilter>("all")
  const [project, setProject] = useState<ProjectRead | null>(null)
  const [projects, setProjects] = useState<ProjectRead[]>([])
  const [projectOverviews, setProjectOverviews] = useState<ProjectOverview[]>([])
  const [diagnosticAvailable, setDiagnosticAvailable] = useState<boolean | null>(null)
  const [documents, setDocuments] = useState<DocumentRead[]>([])
  const [articles, setArticles] = useState<ArticleRead[]>([])
  const [scholarBundle, setScholarBundle] = useState<any>(null)
  const [stateOfArtHistory, setStateOfArtHistory] = useState<any[]>([])
  const [selectedStateOfArtRunId, setSelectedStateOfArtRunId] = useState<string | null>(null)
  const [generatingStateArt, setGeneratingStateArt] = useState(false)
  const [generateStateArtError, setGenerateStateArtError] = useState("")
  const [generatedStateArtResult, setGeneratedStateArtResult] = useState<any | null>(null)
  const [latestStateArtResult, setLatestStateArtResult] = useState<any | null>(null)
  const [preparingStateArt, setPreparingStateArt] = useState(false)
  const [prepareStateArtStage, setPrepareStateArtStage] = useState("idle")
  const [prepareStateArtError, setPrepareStateArtError] = useState("")
  const [selectionPreviewPayload, setSelectionPreviewPayload] = useState<any | null>(null)
  const [articleCardsPayload, setArticleCardsPayload] = useState<any | null>(null)
  const [fulltextStatusPayload, setFulltextStatusPayload] = useState<any | null>(null)
  const [directExtractStatusPayload, setDirectExtractStatusPayload] = useState<any | null>(null)
  const [uploadingArticleId, setUploadingArticleId] = useState<number | null>(null)
  const [scholarSearchRunning, setScholarSearchRunning] = useState(false)
  const [scholarSearchError, setScholarSearchError] = useState("")

  const scholarPayload = scholarBundle?.bundle?.payload || scholarBundle?.latest_run?.raw_result_json?.payload || {}
  const scholarReport = scholarBundle?.bundle?.report || scholarBundle?.latest_run?.raw_result_json?.report || {}
  const scholarSearchFailure = scholarSearchFailureMessage(scholarReport)
  const scholarSummary = scholarBundle?.bundle?.summary || scholarBundle?.latest_run?.raw_result_json?.summary || {}
  const scholarGroupingGroups =
    scholarPayload?.grouping_report?.groups ||
    scholarReport?.grouping_report?.groups ||
    scholarSummary?.grouping_report?.groups ||
    []
  const scholarGroupingSummary =
    scholarPayload?.grouping_summary ||
    scholarReport?.grouping_summary ||
    scholarSummary?.grouping_summary ||
    {}

  const reportArticles = useMemo(
    () => getScholarReportArticlesForDisplay(scholarBundle),
    [scholarBundle]
  )

  const reportArticlesWithDbDecisions = useMemo(
    () => mergeReportArticlesWithDbDecisions(reportArticles, articles),
    [reportArticles, articles]
  )

  // Source principale d'affichage : le dernier rapport EnnoScholar complet,
  // enrichi avec les décisions consultant de la table Article DB quand elles existent.
  // Comme ça :
  // - Sélection articles affiche les 7 verrous et les 734 candidats du dernier rapport ;
  // - les badges Gardé/Rejeté/En attente reflètent la décision DB ;
  // - Sélection consultant ne prend que les articles statut = garde, y compris Hors sujet si le consultant les garde.
  const articlesForDisplay = useMemo(
    () => (reportArticlesWithDbDecisions.length > 0 ? reportArticlesWithDbDecisions : articles),
    [reportArticlesWithDbDecisions, articles]
  )

  const multiVerrouArticles = useMemo(
    () => buildMultiVerrouArticles(articlesForDisplay),
    [articlesForDisplay]
  )

  // Source contractuelle des décisions consultant : uniquement les articles
  // réellement synchronisés en base. Le dernier rapport EnnoScholar contient
  // des centaines de candidats sans décision ; il ne doit jamais remplacer
  // la liste DB pour calculer Gardés / Rejetés / En attente ni la sélection finale.
  const databaseMultiVerrouArticles = useMemo(
    () => buildMultiVerrouArticles(articles),
    [articles]
  )

  const dedupedArticles = useMemo(
    () => multiVerrouArticles,
    [multiVerrouArticles]
  )

  const sourceFilteredArticles = useMemo(
    () => dedupedArticles.filter((article) => articleMatchesSourceFilter(article, sourceFilter)),
    [dedupedArticles, sourceFilter]
  )

  const visibleArticleBase = useMemo(
    () => sourceFilteredArticles.filter((article) => showHorsSujet || normalizeTag(article.tag_article || article.source_json?.tag) !== "Hors sujet"),
    [sourceFilteredArticles, showHorsSujet]
  )

  const filtered = useMemo(
    () => sortArticles(filterArticles(visibleArticleBase, query)),
    [visibleArticleBase, query]
  )

  const grouped = useMemo(() => groupArticles(filtered), [filtered])

  const groupedByVerrou = useMemo(
    () => reportArticles.length > 0
      ? groupArticlesByReportVerrouForDisplay(filtered)
      : groupArticlesByScientificVerrou(filtered, scholarGroupingGroups),
    [filtered, scholarGroupingGroups, reportArticles.length]
  )
  const latestScholarReport = useMemo(() => {
    const bundle: any = scholarBundle || {}

    if (bundle?.report?.results) return bundle.report
    if (bundle?.bundle?.report?.results) return bundle.bundle.report
    if (bundle?.raw_result_json?.report?.results) return bundle.raw_result_json.report
    if (bundle?.data?.report?.results) return bundle.data.report
    if (bundle?.results) return bundle

    return null
  }, [scholarBundle])

  const foundArticleCounts = useMemo(
    () => computeScholarSearchCounts(scholarBundle, articlesForDisplay),
    [scholarBundle, articlesForDisplay]
  )

  const hasLatestReportArticles = foundArticleCounts.fromReport

  const groupedByVerrouArticleCount = hasLatestReportArticles
    ? foundArticleCounts.total
    : groupedByVerrou.reduce(
        (total, group) => total + group.articles.length,
        0
      )

  const usefulArticlesCount = hasLatestReportArticles ? foundArticleCounts.total : filtered.length
  const usefulCandidateCount = hasLatestReportArticles ? countUsefulFromFoundCounts(foundArticleCounts) : grouped.direct.length + grouped.connexe.length + grouped.fondamental.length

  const consultantSelectedArticles = useMemo(
    () => getStrictConsultantSelectedArticles(databaseMultiVerrouArticles),
    [databaseMultiVerrouArticles]
  )

  const consultantDecisionStats = useMemo(
    () => getConsultantDecisionStats(databaseMultiVerrouArticles),
    [databaseMultiVerrouArticles]
  )

  const evidenceStats = useMemo(() => countEvidenceStatuses(articles), [articles])
  const preflightPendingCount = evidenceStats.notChecked

  const selectedStateOfArtEntry = useMemo(() => {
    if (!stateOfArtHistory.length) return null
    return (
      stateOfArtHistory.find((entry: any) => String(entry?.run_id) === String(selectedStateOfArtRunId)) ||
      stateOfArtHistory[0]
    )
  }, [stateOfArtHistory, selectedStateOfArtRunId])
  const directArticlesFoundCount = hasLatestReportArticles ? foundArticleCounts.direct : grouped.direct.length
  const connexeArticlesFoundCount = hasLatestReportArticles ? foundArticleCounts.connexe : grouped.connexe.length
  const fondamentalArticlesFoundCount = hasLatestReportArticles ? foundArticleCounts.fondamental : grouped.fondamental.length

  const loadData = async () => {
    setLoading(true)
    setError("")
    setScholarSearchError("")

    try {
      const [projectList, overviewList] = await Promise.all([
        getProjects(),
        getProjectOverviews().catch(() => [] as ProjectOverview[]),
      ])
      setProjects(projectList)
      setProjectOverviews(overviewList)

      if (projectList.length === 0) {
        setProject(null)
        setDiagnosticAvailable(null)
        setDocuments([])
        setArticles([])
        setStateOfArtHistory([])
        setSelectedStateOfArtRunId(null)
        setGeneratedStateArtResult(null)
        return
      }

      const storedProjectId = getCurrentProjectId()
      const selectedProject =
        projectList.find((item) => item.id === storedProjectId) || projectList[0]

      setCurrentProjectId(selectedProject.id)
      setProject(selectedProject)

      const selectedOverview = overviewList.find(
        (item) => item.project.id === selectedProject.id,
      )
      const selectedProjectHasDiagnostic = selectedOverview
        ? Boolean(selectedOverview.diagnostic.available)
        : true
      setDiagnosticAvailable(selectedProjectHasDiagnostic)
      if (!selectedProjectHasDiagnostic) {
        setActiveTab("etat-art-rediges")
      }

      const [documentsData, articlesData] = await Promise.all([
        getDocuments(selectedProject.id).catch(() => []),
        getArticles(selectedProject.id, true),
      ])

      setDocuments(documentsData)
      setArticles(articlesData)
      setGeneratedStateArtResult(null)
      setSelectionPreviewPayload(null)
      setArticleCardsPayload(null)
      setFulltextStatusPayload(null)
      setDirectExtractStatusPayload(null)
      setPrepareStateArtStage("idle")
      setLoading(false)

      void Promise.all([
        getScholarLatest(selectedProject.id).catch(() => null),
        getStateOfArtHistory(selectedProject.id).catch(() => null),
      ])
        .then(([scholarData, stateArtData]) => {
          setScholarBundle(scholarData)
          const reports = Array.isArray(stateArtData?.reports) ? stateArtData.reports : []
          const latest = reports[0]
          setStateOfArtHistory(reports)
          setSelectedStateOfArtRunId(latest?.run_id ? String(latest.run_id) : null)
          setLatestStateArtResult(latest ? {
            markdown: latest.markdown || latest.report?.markdown || "",
            report: latest.report || null,
            state_of_art_view: latest.report || null,
          } : null)
        })
        .catch(() => undefined)
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

  // ENNOSCHOLAR_STANDALONE_CHAT_CORPUS_V4
  // Le workflow autonome reste dans le chat pendant la préparation.
  // Le POST « Ajouter au corpus » attend déjà extraction + Article Card.
  // On garde le polling historique uniquement hors de l'espace chat.
  useEffect(() => {
    if (
      !project?.id ||
      diagnosticAvailable === false ||
      preflightPendingCount <= 0 ||
      activeTab === "etat-art-rediges"
    ) return

    let cancelled = false
    const refreshEvidence = async () => {
      try {
        const fresh = await getArticles(project.id, true)
        if (!cancelled) setArticles(fresh)
      } catch {
        // polling non bloquant hors chat
      }
    }
    const timer = window.setInterval(refreshEvidence, 4000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [project?.id, diagnosticAvailable, preflightPendingCount, activeTab])

  // IMPORTANT :
  // ne jamais changer automatiquement d'onglet à cause d'un statut
  // d'extraction. Le consultant reste dans sa conversation.

  useEffect(() => {
    onImmersiveModeChange?.(
      diagnosticAvailable === false || activeTab === "etat-art-rediges",
    )
  }, [activeTab, diagnosticAvailable, onImmersiveModeChange])

  useEffect(
    () => () => onImmersiveModeChange?.(false),
    [onImmersiveModeChange],
  )

  const changeProject = async (projectId: number) => {
    setCurrentProjectId(projectId)
    setLoading(true)
    setError("")
    setScholarSearchError("")

    try {
      const selectedProject = projects.find((item) => item.id === projectId) || null
      setProject(selectedProject)
      const selectedOverview = projectOverviews.find(
        (item) => item.project.id === projectId,
      )
      const selectedProjectHasDiagnostic = selectedOverview
        ? Boolean(selectedOverview.diagnostic.available)
        : true
      setDiagnosticAvailable(selectedProjectHasDiagnostic)
      setActiveTab(selectedProjectHasDiagnostic ? "par-verrou" : "etat-art-rediges")
      setScholarBundle(null)
      setStateOfArtHistory([])
      setSelectedStateOfArtRunId(null)
      setLatestStateArtResult(null)

      const [documentsData, articlesData] = await Promise.all([
        getDocuments(projectId).catch(() => []),
        getArticles(projectId, true),
      ])

      setDocuments(documentsData)
      setArticles(articlesData)
      setGeneratedStateArtResult(null)
      setSelectionPreviewPayload(null)
      setArticleCardsPayload(null)
      setFulltextStatusPayload(null)
      setDirectExtractStatusPayload(null)
      setPrepareStateArtStage("idle")
      setLoading(false)

      void Promise.all([
        getScholarLatest(projectId).catch(() => null),
        getStateOfArtHistory(projectId).catch(() => null),
      ])
        .then(([scholarData, stateArtData]) => {
          setScholarBundle(scholarData)
          const reports = Array.isArray(stateArtData?.reports) ? stateArtData.reports : []
          const latest = reports[0]
          setStateOfArtHistory(reports)
          setSelectedStateOfArtRunId(latest?.run_id ? String(latest.run_id) : null)
          setLatestStateArtResult(latest ? {
            markdown: latest.markdown || latest.report?.markdown || "",
            report: latest.report || null,
            state_of_art_view: latest.report || null,
          } : null)
        })
        .catch(() => undefined)
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
    setArticles((prev) => {
      const exists = prev.some((article) => article.id === updated.id)
      if (!exists) return [updated, ...prev]
      return prev.map((article) => (article.id === updated.id ? updated : article))
    })
  }

  const launchScholarSearch = async () => {
    if (!project?.id || scholarSearchRunning) return

    setScholarSearchRunning(true)
    setScholarSearchError("")

    try {
      const run = await runScholarFromSelectedVerrous(project.id)
      const rawRunId = run?.id ?? run?.run_id ?? run?.latest_run?.id
      const runId = Number(rawRunId)

      if (Number.isFinite(runId) && runId > 0) {
        await syncScholarArticles(project.id, runId)
      }

      const [latestScholar, latestArticles] = await Promise.all([
        getScholarLatest(project.id).catch(() => run),
        getArticles(project.id, true),
      ])

      setScholarBundle(latestScholar || run)
      setArticles(latestArticles)
      setActiveTab("par-verrou")
    } catch (reason) {
      setScholarSearchError(
        reason instanceof Error
          ? reason.message
          : "Impossible de lancer la recherche EnnoScholar.",
      )
    } finally {
      setScholarSearchRunning(false)
    }
  }

  const loadStateOfArtPreparationStatus = async () => {
    if (!project?.id) return

    const [selectionData, cardsData, fulltextData, directData] = await Promise.all([
      getStateOfArtSelectionPreview(project.id).catch((error) => ({
        ok: false,
        error: error?.message || String(error),
      })),
      getScholarArticleCards(project.id).catch((error) => ({
        ok: false,
        error: error?.message || String(error),
      })),
      getScholarFulltextStatus(project.id).catch((error) => ({
        ok: false,
        error: error?.message || String(error),
      })),
      getScholarDirectExtractStatus(project.id).catch((error) => ({
        ok: false,
        error: error?.message || String(error),
      })),
    ])

    setSelectionPreviewPayload(selectionData)
    setArticleCardsPayload(cardsData)

    // Deux sources distinctes :
    // - fulltextStatusPayload = résolution MCP / recherche de copies légales ;
    // - directExtractStatusPayload = téléchargement, extraction et OCR.
    setFulltextStatusPayload(fulltextData)
    setDirectExtractStatusPayload(directData)
  }

  const refreshStateOfArtPreparation = async () => {
    if (!project?.id) return

    setPreparingStateArt(true)
    setPrepareStateArtStage("refresh")
    setPrepareStateArtError("")

    try {
      await loadStateOfArtPreparationStatus()
      setPrepareStateArtStage("done")
    } catch (error: any) {
      setPrepareStateArtStage("idle")
      setPrepareStateArtError(
        error?.message || "Impossible de vérifier la préparation de l’état de l’art."
      )
    } finally {
      setPreparingStateArt(false)
    }
  }

  const refreshCorpusAfterChatAction = async () => {
    if (!project?.id) return

    // ENNOSCHOLAR_STANDALONE_CHAT_CORPUS_V4
    // Une seule relecture après le POST synchrone du chat.
    // Pas de redirection ni de relance de l'ancien écran de préparation.
    const refreshedArticles = await getArticles(project.id, true)
    setArticles(refreshedArticles)
  }

  // ============================================================
  // Préparation réelle :
  // sélection courante -> extraction directe/OCR -> récupération légale MCP
  // des échecs -> reconstruction des Article Cards.
  // Les succès existants sont réutilisés pour éviter un nouvel OCR.
  // ============================================================
  const launchCompleteStateOfArtPreparation = async () => {
    if (!project?.id) return

    setPreparingStateArt(true)
    setPrepareStateArtError("")

    try {
      const prepared = await prepareStateOfArtPhase1And2(project.id, {
        force: false,
        maxArticles: null,
        articleCardMode: "auto",
        onProgress: (event) => {
          switch (event.key) {
            case "phase1_selection_payload":
              setPrepareStateArtStage("selection")
              break
            case "phase2a_fulltext_resolve":
              setPrepareStateArtStage("extraction")
              break
            case "phase2b_direct_extract":
              setPrepareStateArtStage("mcp")
              break
            case "phase2d_article_cards":
              setPrepareStateArtStage("cards")
              break
            case "refresh_status":
              setPrepareStateArtStage("refresh")
              break
          }
        },
      })

      setSelectionPreviewPayload(prepared.finalStatus.selectionPreview)
      setFulltextStatusPayload(prepared.finalStatus.fulltextStatus)
      setDirectExtractStatusPayload(prepared.finalStatus.directExtractStatus)
      setArticleCardsPayload(prepared.finalStatus.articleCards)
      setPrepareStateArtStage("done")
    } catch (error: any) {
      setPrepareStateArtStage("idle")
      setPrepareStateArtError(
        error?.message ||
          "Impossible de préparer tous les articles sélectionnés pour l’état de l’art."
      )
    } finally {
      setPreparingStateArt(false)
    }
  }

  const uploadPdfForSelectedArticle = async (articleId: number, file: File) => {
    if (!project?.id) return

    setUploadingArticleId(articleId)
    setPrepareStateArtError("")

    try {
      await uploadAndExtractArticlePdf(project.id, articleId, file)
      await refreshStateOfArtPreparation()
    } catch (error: any) {
      setPrepareStateArtError(error?.message || "Impossible d’uploader et extraire le PDF de cet article.")
    } finally {
      setUploadingArticleId(null)
    }
  }

  const launchFinalStateOfArtGeneration = async (
    guidedSessionId?: string | null,
  ) => {
    if (!project?.id) return

    setGeneratingStateArt(true)
    setGenerateStateArtError("")
    setGeneratedStateArtResult(null)

    try {
      const generated = await runFullStateOfArt(project.id, {
        // Réutiliser les phases et Article Cards déjà validées.
        forcePhase3: false,
        forceArticleCards: false,

        // Premier test contrôlé : un seul writer GPT, sans appels
        // supplémentaires de normalisation ou de polish.
        enableNormalization: false,
        enablePolish: false,

        // Qualité complète : le backend applique le contexte Phase 3 → 4.7
        // et choisit le modèle via le LLMClient central.
        fastMode: false,
        guidedSessionId,
      })
      setGeneratedStateArtResult(generated)
      if (generated?.ok === false) {
        setGenerateStateArtError("")
        return generated
      }
      setLatestStateArtResult(generated)

      const stateArtData = await getStateOfArtHistory(project.id).catch(() => null)
      const stateArtReports = Array.isArray(stateArtData?.reports) ? stateArtData.reports : []

      setStateOfArtHistory(stateArtReports)
      setSelectedStateOfArtRunId(stateArtReports[0]?.run_id ? String(stateArtReports[0].run_id) : null)
      setActiveTab("etat-art-rediges")
      return generated
    } catch (error: any) {
      // V3 UX : apiRequest expose déjà le detail FastAPI dans error.message.
      // Ne pas masquer un 409 métier par une fausse indisponibilité du service.
      const rawMessage = String(error?.message || "").trim()
      const writingBlocked =
        /rédaction bloquée|redaction bloquee|aucun article.*gard|texte intégral|texte integral/i.test(
          rawMessage,
        )

      const deferred = {
        ok: false,
        status: writingBlocked
          ? "writing_blocked_by_corpus"
          : "writing_service_temporarily_unavailable",
        assistant_message:
          rawMessage ||
          "La rédaction n'a pas pu démarrer. Votre corpus, votre plan et vos choix sont conservés ; corrigez le point signalé puis relancez.",
        retryable: true,
        previous_draft_preserved: true,
      }

      // Le composant chat affiche déjà assistant_message comme bulle.
      // Ne pas afficher le même texte une deuxième fois en encart.
      setGenerateStateArtError("")
      return deferred
    } finally {
      setGeneratingStateArt(false)
    }
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

  const currentDraftMarkdown =
    generatedStateArtResult?.markdown ||
    generatedStateArtResult?.state_of_art_view?.markdown ||
    latestStateArtResult?.markdown ||
    latestStateArtResult?.state_of_art_view?.markdown ||
    latestStateArtResult?.report?.markdown ||
    selectedStateOfArtEntry?.markdown ||
    selectedStateOfArtEntry?.report?.markdown ||
    ""

  const standaloneChatMode = diagnosticAvailable === false

  const workflowSteps = [
    { label: "Rechercher", detail: "Corpus", status: usefulArticlesCount > 0 ? "complete" as const : "current" as const },
    { label: "Vérifier", detail: "Accès & preuves", status: preflightPendingCount > 0 ? "current" as const : usefulArticlesCount > 0 ? "complete" as const : "upcoming" as const },
    { label: "Sélectionner", detail: "Consultant", status: consultantSelectedArticles.length > 0 ? "complete" as const : usefulArticlesCount > 0 ? "current" as const : "upcoming" as const },
    { label: "Rédiger", detail: "État de l'art", status: currentDraftMarkdown ? "complete" as const : consultantSelectedArticles.length > 0 ? "current" as const : "upcoming" as const },
  ]

  const scholarSpaces = [
    { value: "par-verrou", label: "Sélection articles", hint: `${usefulArticlesCount} candidat(s)` },
    { value: "selection", label: "Sélection consultant", hint: `${consultantSelectedArticles.length} gardé(s)` },
    { value: "etat-art-rediges", label: "Rédaction état de l’art", hint: currentDraftMarkdown ? "Brouillon disponible" : "À préparer" },
  ]

  const scholarWorkspaceHeader = (
    <header className="shrink-0 border-b border-border bg-card/95 shadow-xs backdrop-blur-sm">
      <div className="flex min-h-14 flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between lg:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-xl border border-brand/20 bg-brand/8 text-brand">
            <BookOpen className="size-4.5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-base font-semibold tracking-tight text-foreground">EnnoScholar</h1>
              <Badge variant="outline" className="border-brand/20 bg-brand/5 text-brand">
                Agent de preuve scientifique
              </Badge>
            </div>
            <p className="truncate text-xs text-muted-foreground">
              {project.organisme} · {project.project_name} · {project.year} · {documents.length} document(s)
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {projects.length > 1 && (
            <select
              value={project.id}
              onChange={(event) => changeProject(Number(event.target.value))}
              className="min-h-10 max-w-full rounded-lg border border-border bg-background px-3 text-sm text-foreground focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25"
              aria-label="Changer de projet EnnoScholar"
            >
              {projects.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.organisme} — {item.project_name} — {item.year}
                </option>
              ))}
            </select>
          )}
          {diagnosticAvailable !== false && (
            <Button
              size="sm"
              className="min-h-10 bg-brand hover:bg-brand/90"
              disabled={scholarSearchRunning}
              onClick={launchScholarSearch}
              aria-label={usefulArticlesCount > 0 ? "Relancer la recherche EnnoScholar" : "Lancer la recherche EnnoScholar"}
            >
              {scholarSearchRunning ? (
                <Loader2 className="size-4 animate-spin" data-icon="inline-start" aria-hidden="true" />
              ) : (
                <Search className="size-4" data-icon="inline-start" aria-hidden="true" />
              )}
              {scholarSearchRunning
                ? "Recherche en cours..."
                : usefulArticlesCount > 0
                  ? "Relancer la recherche"
                  : "Lancer la recherche"}
            </Button>
          )}
          <Button variant="outline" size="sm" className="min-h-10" onClick={loadData}>
            <RefreshCw data-icon="inline-start" aria-hidden="true" />
            Actualiser
          </Button>
        </div>
      </div>

      {(scholarSearchError || scholarSearchFailure) && (
        <div
          className="flex items-start gap-2 border-t border-destructive/20 bg-destructive/5 px-4 py-2.5 text-xs text-destructive lg:px-6"
          role="alert"
          aria-live="assertive"
        >
          <AlertCircle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          <span>{scholarSearchError || scholarSearchFailure}</span>
        </div>
      )}

      {standaloneChatMode ? (
        <div className="border-t border-border/60 px-3 py-1.5 sm:px-4 lg:px-6">
          <div className="flex min-h-9 flex-wrap items-center gap-2">
            <Badge className="bg-brand text-brand-foreground hover:bg-brand">
              Mode autonome
            </Badge>
            <p className="text-xs text-muted-foreground">
              Chat scientifique EnnoScholar · aucun parcours EnnoDiagnostic pour ce projet
            </p>
          </div>
        </div>
      ) : activeTab === "etat-art-rediges" ? (
        <div className="border-t border-border/60 px-3 py-1.5 sm:px-4 lg:px-6">
          <div className="flex min-h-9 items-center justify-between gap-3 overflow-x-auto">
            <nav
              className="flex shrink-0 items-center gap-1"
              aria-label="Espaces EnnoScholar"
            >
              {scholarSpaces.map((space) => {
                const active = activeTab === space.value
                const compactLabel =
                  space.value === "par-verrou"
                    ? "Articles"
                    : space.value === "selection"
                      ? "Sélection"
                      : "Rédaction"

                return (
                  <button
                    key={space.value}
                    type="button"
                    onClick={() => setActiveTab(space.value)}
                    aria-current={active ? "page" : undefined}
                    className={`rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/20 ${
                      active
                        ? "bg-brand/[0.08] text-brand"
                        : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
                    }`}
                  >
                    {compactLabel}
                  </button>
                )
              })}
            </nav>

            <div
              className="hidden shrink-0 items-center gap-2.5 text-[10px] text-muted-foreground md:flex"
              aria-label="Progression EnnoScholar"
            >
              {workflowSteps.map((step, index) => {
                const complete = step.status === "complete"
                const current = step.status === "current"

                return (
                  <div key={step.label} className="flex items-center gap-2">
                    {index > 0 && (
                      <span className="h-px w-4 bg-border" aria-hidden="true" />
                    )}
                    <span
                      className={`flex items-center gap-1.5 whitespace-nowrap ${
                        complete || current ? "text-foreground" : ""
                      }`}
                    >
                      <span
                        className={`grid size-4 place-items-center rounded-full text-[9px] font-semibold ${
                          complete
                            ? "bg-success text-white"
                            : current
                              ? "bg-brand text-white"
                              : "border border-border bg-background"
                        }`}
                        aria-hidden="true"
                      >
                        {complete ? "✓" : current ? "•" : ""}
                      </span>
                      {step.label}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      ) : (
        <div className="border-t border-border/70 px-4 py-2 lg:px-6">
          <div className="grid gap-2 md:grid-cols-[minmax(0,1fr)_minmax(420px,0.9fr)] md:items-center">
            <nav className="grid grid-cols-1 gap-1 rounded-xl bg-muted/70 p-1 sm:grid-cols-3" aria-label="Espaces EnnoScholar">
              {scholarSpaces.map((space) => {
                const active = activeTab === space.value
                return (
                  <button
                    key={space.value}
                    type="button"
                    onClick={() => setActiveTab(space.value)}
                    aria-current={active ? "page" : undefined}
                    className={`min-h-11 rounded-lg px-3 py-2 text-left transition-colors focus-visible:outline-none focus-visible:ring-3 focus-visible:ring-ring/25 ${
                      active
                        ? "bg-card text-foreground shadow-xs ring-1 ring-border"
                        : "text-muted-foreground hover:bg-card/70 hover:text-foreground"
                    }`}
                  >
                    <span className="block truncate text-xs font-semibold">{space.label}</span>
                    <span className="mt-0.5 block truncate text-[10px] opacity-75">{space.hint}</span>
                  </button>
                )
              })}
            </nav>
            <WorkflowSteps steps={workflowSteps} className="border-0 bg-transparent shadow-none" />
          </div>
        </div>
      )}
    </header>
  )

  if (standaloneChatMode || activeTab === "etat-art-rediges") {
    return (
      <div className="flex h-full min-h-0 flex-col bg-background">
        {scholarWorkspaceHeader}
        <div className="min-h-0 flex-1">
          <EnnoScholarPlanChat
            key={`ennoscholar-chat-${project.id}`}
            projectId={project.id}
            projectLabel={`${project.organisme} — ${project.project_name} — ${project.year}`}
            immersive
            selectedArticles={consultantSelectedArticles}
            onCorpusChanged={refreshCorpusAfterChatAction}
            onGenerate={launchFinalStateOfArtGeneration}
            onRefreshDraft={async () => {
              const refreshed = await getLatestStateOfArt(project.id)
              setLatestStateArtResult(refreshed)
            }}
            draftMarkdown={currentDraftMarkdown}
            generating={generatingStateArt}
            generationError={generateStateArtError}
          />
        </div>
      </div>
    )
  }

  return (
    <div className="workspace-page-wide space-y-4">
      <div className="sticky top-0 z-20 -mx-4 sm:-mx-5 lg:-mx-7">
        {scholarWorkspaceHeader}
      </div>

      {preflightPendingCount > 0 && (
        <StatusNotice state="processing" live title="Vérification des accès aux articles" description="Le catalogue reste consultable. Les liens directs sont vérifiés, puis une copie légale est recherchée pour chaque échec.">
          <div className="mt-3 space-y-3">
            <div className="h-2 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full bg-brand transition-all duration-500"
                style={{
                  width: `${evidenceStats.total > 0
                    ? Math.round(((evidenceStats.total - preflightPendingCount) / evidenceStats.total) * 100)
                    : 0}%`,
                }}
              />
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
              <span className="font-medium text-foreground">
                {evidenceStats.total - preflightPendingCount} / {evidenceStats.total} accès vérifiés
              </span>
              <span className="text-muted-foreground">
                {preflightPendingCount} restant{preflightPendingCount > 1 ? "s" : ""}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              L'extraction lourde ne démarre qu'à votre demande ; l'import PDF est proposé uniquement après l'échec final de la recherche légale.
            </p>
          </div>
        </StatusNotice>
      )}
      {activeTab !== "etat-art-rediges" && (
        <>
      {/* Bandeau de lecture rapide : toutes les métriques, sans empiler six cartes. */}
      <section className="overflow-x-auto rounded-xl border border-border bg-card shadow-xs" aria-label="Indicateurs du corpus">
        <dl className="grid min-w-[720px] grid-cols-6 divide-x divide-border">
          {[
            { label: "Candidats", value: usefulArticlesCount, tone: "text-foreground" },
            { label: "Utiles", value: usefulCandidateCount, tone: "text-success" },
            { label: "Directs", value: directArticlesFoundCount, tone: "text-success" },
            { label: "Connexes", value: connexeArticlesFoundCount, tone: "text-brand" },
            { label: "Fondamentaux", value: fondamentalArticlesFoundCount, tone: "text-blue-700" },
            { label: "Techniques", value: foundArticleCounts.technique, tone: "text-purple-700" },
          ].map((stat) => (
            <div key={stat.label} className="px-4 py-3">
              <dt className="text-[11px] font-medium text-muted-foreground">{stat.label}</dt>
              <dd className={`mt-0.5 text-lg font-semibold tabular-nums ${stat.tone}`}>{stat.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      {/* Search and options */}
      <Card className="shadow-xs">
        <CardContent className="flex flex-col gap-3 p-3 lg:flex-row lg:items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Rechercher un article, un DOI, une source..."
              className="min-h-10 pl-10"
              aria-label="Rechercher dans les articles EnnoScholar"
            />
          </div>

          <label className="flex min-h-10 items-center gap-2 rounded-lg border border-border bg-background px-3 text-xs font-medium text-muted-foreground">
            Source
            <select
              value={sourceFilter}
              onChange={(event) => setSourceFilter(event.target.value as SourceFilter)}
              className="min-w-36 flex-1 bg-transparent text-sm text-foreground focus-visible:outline-none"
            >
              {(["all", "semantic_scholar", "openalex", "arxiv", "memory_v2", "technical"] as SourceFilter[]).map((value) => (
                <option key={value} value={value}>{sourceFilterLabel(value)}</option>
              ))}
            </select>
          </label>

          <Button
            variant="outline"
            size="sm"
            className="min-h-10"
            onClick={() => setShowHorsSujet((prev) => !prev)}
            aria-pressed={showHorsSujet}
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
        </>
      )}

      <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-4">
        <TabsContent value="par-verrou">
          <EnnoScholarByVerrouSection
            groups={groupedByVerrou}
            projectId={project.id}
            onUpdated={updateLocalArticle}
            groupingSummary={scholarGroupingSummary}
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
          <div className="space-y-4">
            <Card className="border-brand/20 bg-brand/5">
              <CardHeader>
                <CardTitle className="text-sm flex items-center gap-2">
                  <CheckCircle2 className="size-4 text-brand" />
                  Sélection consultant finalisée
                </CardTitle>
                <CardDescription>
                  Les articles gardés sont déjà préparés individuellement : le texte intégral est vérifié/extrait au moment de la conservation et l’Article Card est synchronisée automatiquement. Passe directement à la rédaction.
                </CardDescription>
              </CardHeader>
              <CardContent className="flex flex-wrap items-center gap-3">
                <Button
                  size="sm"
                  className="bg-brand hover:bg-brand/90"
                  disabled={consultantSelectedArticles.length === 0}
                  onClick={() => setActiveTab("etat-art-rediges")}
                  title={
                    consultantSelectedArticles.length === 0
                      ? "Garde au moins un article avant de passer à la rédaction."
                      : "Ouvrir l’espace de rédaction de l’état de l’art."
                  }
                >
                  Suivant : rédiger l’état de l’art
                </Button>

                <span className="text-xs text-muted-foreground">
                  {consultantSelectedArticles.length} article(s) gardé(s)
                </span>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-sm flex items-center gap-2">
                  <FileText className="size-4 text-brand" />
                  Décisions consultant
                </CardTitle>
                <CardDescription className="text-xs">
                  Cette section est stricte : seuls les articles avec consultant_status = garde apparaissent dans la liste finale. Les doublons sont fusionnés : un même article peut couvrir plusieurs verrous, même s’il n’a été trouvé initialement que dans un seul verrou.
                </CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3 md:grid-cols-3">
                <div className="rounded-lg border border-success/30 bg-success/5 p-3">
                  <p className="text-xs text-muted-foreground">Gardés</p>
                  <p className="text-2xl font-bold text-success">{consultantDecisionStats.garde}</p>
                </div>
                <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3">
                  <p className="text-xs text-muted-foreground">Rejetés</p>
                  <p className="text-2xl font-bold text-destructive">{consultantDecisionStats.rejete}</p>
                </div>
                <div className="rounded-lg border border-border bg-muted/30 p-3">
                  <p className="text-xs text-muted-foreground">En attente</p>
                  <p className="text-2xl font-bold text-muted-foreground">{consultantDecisionStats.enAttente}</p>
                </div>
              </CardContent>
            </Card>

            <ArticleSection
              title="Sélection consultant"
              description="Liste finale stricte : uniquement les articles marqués Gardé. Un même article apparaît une seule fois avec les badges V1, V2... des verrous qu’il couvre, y compris les couvertures transversales détectées automatiquement. Les Hors sujet gardés restent exploitables avec alerte."
              articles={consultantSelectedArticles}
              projectId={project.id}
              onUpdated={updateLocalArticle}
            />
          </div>
        </TabsContent>


        <TabsContent value="etat-art-rediges">
          <div className="space-y-4">
            <EnnoScholarPlanChat
              key={`ennoscholar-chat-${project.id}`}
              projectId={project.id}
              projectLabel={`${project.organisme} — ${project.project_name} — ${project.year}`}
              selectedArticles={consultantSelectedArticles}
              onCorpusChanged={refreshCorpusAfterChatAction}
              onGenerate={launchFinalStateOfArtGeneration}
              onRefreshDraft={async () => {
                const refreshed = await getLatestStateOfArt(project.id)
                setLatestStateArtResult(refreshed)
              }}
              draftMarkdown={
                generatedStateArtResult?.markdown ||
                generatedStateArtResult?.state_of_art_view?.markdown ||
                latestStateArtResult?.markdown ||
                latestStateArtResult?.state_of_art_view?.markdown ||
                latestStateArtResult?.report?.markdown ||
                selectedStateOfArtEntry?.markdown ||
                selectedStateOfArtEntry?.report?.markdown ||
                ""
              }
              generating={generatingStateArt}
              generationError={generateStateArtError}
            />
          </div>
        </TabsContent>

        <TabsContent value="evidence-chains">
          <StateOfArtEvidenceChainsPanel
            report={generatedStateArtResult?.state_of_art_view || generatedStateArtResult || latestStateArtResult?.state_of_art_view || latestStateArtResult?.report || selectedStateOfArtEntry?.report}
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

      {activeTab !== "etat-art-rediges" && scholarBundle?.bundle?.files_found && (
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

export default EnnoScholarPage

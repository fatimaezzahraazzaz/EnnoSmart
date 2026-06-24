"use client"

import { useMemo, useState } from "react"
import {
  ArrowRight,
  FileSearch,
  Link2,
  Maximize2,
  Minimize2,
  ShieldCheck,
  X,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"

type AnyObject = Record<string, any>
type PassageSide = "current" | "previous" | "common"
type QualityLevel = "strong" | "partial" | "weak"

type ContinuityPassage = {
  id: string
  side: PassageSide
  label: string
  text: string
  document?: string
  section?: string
  year?: string
  sourcePath?: string
  viewerUrl?: string
  page?: number
  fullText?: string
}

type ContinuityItem = {
  id: string
  title: string
  theme?: string
  similarityScore: number
  continuityScore: number
  noveltyScore: number
  sharedThemesCount: number
  qualityLevel: QualityLevel
  qualityLabel: string
  qualityExplanation: string
  currentPassages: ContinuityPassage[]
  previousPassages: ContinuityPassage[]
  commonPassages: ContinuityPassage[]
}

interface CirPreviousContinuityTabProps {
  diagnostic: AnyObject
  projectId: number
  apiBaseUrl: string
  authToken?: string
  organisme: string
  projectName: string
  currentYear: string
}

const THEME_KEYWORDS: Record<string, string[]> = {
  performance_pression_debit: [
    "débit",
    "debit",
    "pression",
    "300 bars",
    "90",
    "100 m3",
    "100m3",
    "performances",
    "refoulement",
  ],
  vibration_acoustique: [
    "vibration",
    "vibratoire",
    "acoustique",
    "bruit",
    "nuisances",
    "sonore",
    "rotation",
    "poulie",
    "équilibrage",
    "equilibrage",
    "contrepoids",
    "masselotte",
  ],
  thermique_refroidissement: [
    "température",
    "temperature",
    "échauffement",
    "echauffement",
    "refroidissement",
    "thermique",
    "réfrigérant",
    "refrigerant",
    "débit d'eau",
    "debit d'eau",
    "eau",
    "chaleur",
  ],
  qualite_air_sechage: [
    "air sec",
    "point de rosée",
    "point de rosee",
    "hygrométrie",
    "hygrometrie",
    "eau liquide",
    "condensat",
    "condensats",
    "sécheur",
    "secheur",
    "humidité",
    "humidite",
  ],
  usure_fiabilite_etancheite: [
    "usure",
    "fiabilité",
    "fiabilite",
    "étanchéité",
    "etancheite",
    "segment",
    "segments",
    "reniflard",
    "fuite",
    "huile",
    "résistance mécanique",
    "resistance mecanique",
  ],
  cause_racine_essais: [
    "essai",
    "essais",
    "mesure",
    "mesures",
    "test",
    "tests",
    "analyse",
    "cause",
    "hypothèse",
    "hypothese",
    "constat",
  ],
  compromis_contraintes: [
    "compromis",
    "contraintes",
    "caractéristiques",
    "caracteristiques",
    "solution",
    "implémentation",
    "implementation",
    "paramètres",
    "parametres",
  ],
  etat_art_non_transferable: [
    "connaissances existantes",
    "état de l’art",
    "etat de l'art",
    "non transférable",
    "non transferable",
    "plateau oscillant",
    "barillet",
    "travaux",
    "bibliographique",
  ],
}

function asArray(value: any): any[] {
  if (Array.isArray(value)) return value
  if (value === null || value === undefined) return []
  return [value]
}

function asText(value: any): string {
  if (typeof value === "string") return value.trim()
  if (value === null || value === undefined) return ""
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function percent(value: any, fallback = 0) {
  const n = Number(value)
  if (!Number.isFinite(n)) return fallback
  if (n <= 1) return Math.round(n * 100)
  return Math.round(n)
}

function truncate(text: string, max = 420) {
  const clean = text.replace(/\s+/g, " ").trim()
  if (clean.length <= max) return clean
  return `${clean.slice(0, max)}…`
}

function normalizeForSearch(text: string) {
  return text
    .toLowerCase()
    .normalize("NFD")
    .replace(/\p{Diacritic}/gu, "")
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim()
}

function getFirstText(...values: any[]) {
  for (const value of values) {
    const text = asText(value)
    if (text) return text
  }

  return ""
}


function hasCirPreviousComparisons(memory: any) {
  const report = unwrapCirPreviousMemory(memory)
  return (
    asArray(report?.verrou_comparisons).length > 0 ||
    asArray(report?.comparisons).length > 0 ||
    asArray(report?.continuity_comparisons).length > 0 ||
    asArray(report?.new_or_not_found).length > 0 ||
    asArray(report?.evolution_or_partial_continuity).length > 0 ||
    asArray(report?.continuity_strong).length > 0
  )
}

function hasCirPreviousMemory(memory: any) {
  const report = unwrapCirPreviousMemory(memory)
  const summary = report?.summary || {}

  return (
    report?.has_previous_cir === true ||
    report?.previous_cir_available === true ||
    Number(report?.previous_cir_items_count || 0) > 0 ||
    Number(summary?.previous_cir_items_count || 0) > 0 ||
    Number(summary?.registered_memory_items_count || 0) > 0 ||
    asArray(report?.previous_cir_years_used).length > 0 ||
    asArray(report?.previous_years).length > 0 ||
    asArray(report?.registered_previous_cirs).length > 0
  )
}

function unwrapCirPreviousMemory(value: any): AnyObject {
  if (!value || typeof value !== "object") return {}

  const candidates = [
    value?.report,
    value?.comparison_report,
    value?.cir_memory_report,
    value?.cir_previous_report,
    value?.previous_cir_report,
    value?.data,
    value?.result,
    value,
  ]

  for (const candidate of candidates) {
    if (!candidate || typeof candidate !== "object") continue
    if (hasDirectCirPreviousFields(candidate)) return candidate
  }

  return {}
}

function hasDirectCirPreviousFields(value: any) {
  if (!value || typeof value !== "object") return false
  return (
    value?.has_previous_cir === true ||
    value?.previous_cir_available === true ||
    value?.summary ||
    asArray(value?.verrou_comparisons).length > 0 ||
    asArray(value?.comparisons).length > 0 ||
    asArray(value?.continuity_comparisons).length > 0 ||
    asArray(value?.new_or_not_found).length > 0 ||
    asArray(value?.evolution_or_partial_continuity).length > 0 ||
    asArray(value?.continuity_strong).length > 0 ||
    asArray(value?.previous_cir_years_used).length > 0 ||
    asArray(value?.previous_years).length > 0 ||
    asArray(value?.registered_previous_cirs).length > 0
  )
}

function getMemory(diagnostic: AnyObject) {
  const display = diagnostic?.display || {}

  // IMPORTANT V108 : on donne la priorité au rapport indépendant/top-level,
  // pas à display.cir_memory qui peut être un objet vide hérité du diagnostic.
  const candidates = [
    diagnostic?.cir_memory_report,
    diagnostic?.cir_previous_report,
    diagnostic?.previous_cir_report,
    diagnostic?.comparison_report,
    diagnostic?.cirPreviousComparisonReport,
    display?.cir_memory_report,
    display?.cir_previous_report,
    display?.previous_cir_report,
    display?.comparison_report,
    display?.cir_memory,
  ]

  // 1) D'abord un rapport qui contient vraiment des comparaisons.
  for (const candidate of candidates) {
    const report = unwrapCirPreviousMemory(candidate)
    if (hasCirPreviousComparisons(report)) return report
  }

  // 2) Sinon un rapport qui prouve seulement que le CIR précédent existe.
  for (const candidate of candidates) {
    const report = unwrapCirPreviousMemory(candidate)
    if (hasCirPreviousMemory(report)) return report
  }

  return {}
}


function getComparisonRawItems(diagnostic: AnyObject) {
  const display = diagnostic?.display || {}
  const memory = getMemory(diagnostic)

  const raw = [
    ...asArray(memory?.verrou_comparisons),
    ...asArray(memory?.comparisons),
    ...asArray(memory?.continuity_comparisons),
    ...asArray(memory?.new_or_not_found),
    ...asArray(memory?.evolution_or_partial_continuity),
    ...asArray(memory?.continuity_strong),

    // Fallbacks anciens formats
    ...asArray(display?.cir_memory_verrou_comparisons),
    ...asArray(display?.cir_memory_comparisons),
    ...asArray(diagnostic?.verrou_comparisons),
    ...asArray(diagnostic?.comparisons),
  ]

  // Dédup simple : si le même current_item revient dans plusieurs listes, on garde le premier.
  const seen = new Set<string>()
  const out: any[] = []

  for (const item of raw) {
    if (!item) continue
    const key = JSON.stringify({
      id: item?.current_item?.id || item?.id || item?.current?.id,
      text: String(item?.current_item?.text || item?.current?.text || item?.text || "").slice(0, 240),
      status: item?.decision?.status || "",
    })
    if (seen.has(key)) continue
    seen.add(key)
    out.push(item)
  }

  return out
}


function getSimilarityScore(item: any) {
  return percent(
    item?.best_match?.similarity_score ??
      item?.best_match?.similarity_details?.score ??
      item?.similarity_score ??
      item?.similarity ??
      item?.score,
    0
  )
}

function getContinuityScore(item: any) {
  return percent(
    item?.decision?.continuity_score ??
      item?.best_match?.final_scores?.continuity_score ??
      item?.continuity_score ??
      item?.continuity,
    getSimilarityScore(item)
  )
}

function getNoveltyScore(item: any) {
  return percent(
    item?.decision?.novelty_score ??
      item?.best_match?.final_scores?.novelty_score ??
      item?.novelty_score ??
      item?.novelty,
    Math.max(0, 100 - getContinuityScore(item))
  )
}

function getCandidateSharedThemes(candidate: any) {
  const themes =
    candidate?.similarity_details?.shared_themes ||
    candidate?.best_match?.similarity_details?.shared_themes ||
    candidate?.shared_themes ||
    []

  return asArray(themes).map((theme) => asText(theme)).filter(Boolean)
}

function getCurrentThemes(item: any) {
  const themes =
    item?.best_match?.similarity_details?.current_themes ||
    item?.similarity_details?.current_themes ||
    item?.current_item?.themes ||
    item?.current_item?.theme_ids ||
    item?.current_item?.theme_id ||
    item?.theme_id ||
    []

  return asArray(themes).map((theme) => asText(theme)).filter(Boolean)
}

function getCandidatePrevious(candidate: any) {
  return candidate?.previous_candidate || candidate?.best_match?.previous_candidate || {}
}

function getCandidateScore(candidate: any) {
  return percent(
    candidate?.similarity_score ??
      candidate?.similarity_details?.score ??
      candidate?.score,
    0
  )
}

function previousSectionPreferenceScore(sectionKey: string, role: string, currentThemes: string[], currentText: string) {
  const section = normalizeForSearch(`${sectionKey} ${role}`)
  const current = normalizeForSearch(currentText)

  let score = 0

  if (currentThemes.includes("etat_art_non_transferable")) {
    if (section.includes("insuffisance")) score += 35
    if (section.includes("etat_art")) score += 25
  }

  if (currentThemes.includes("vibration_acoustique")) {
    if (section.includes("verrou")) score += 18
    if (section.includes("etat_art")) score += 10
    if (section.includes("objectif")) score += 8
  }

  if (currentThemes.includes("thermique_refroidissement")) {
    if (section.includes("verrou")) score += 18
    if (section.includes("objectif")) score += 12
    if (section.includes("insuffisance")) score += 8
  }

  if (currentThemes.includes("qualite_air_sechage")) {
    if (section.includes("verrou")) score += 18
    if (section.includes("objectif")) score += 12
  }

  if (currentThemes.includes("usure_fiabilite_etancheite")) {
    if (section.includes("verrou")) score += 25
    if (section.includes("insuffisance")) score += 12
  }

  if (currentThemes.includes("performance_pression_debit")) {
    if (section.includes("objectif")) score += 18
    if (section.includes("verrou")) score += 12
  }

  if (current.includes("contrepoids") || current.includes("masselotte") || current.includes("equilibrage")) {
    if (section.includes("etat_art")) score += 14
    if (section.includes("verrou")) score += 12
    if (section.includes("travaux")) score += 16
  }

  return score
}

function chooseBestCandidate(item: any) {
  const currentItem = item?.current_item || item?.current || item
  const currentText = asText(currentItem?.text || item?.text)
  const currentThemes = getCurrentThemes(item)

  const candidates = [
    item?.best_match,
    ...asArray(item?.candidates),
  ].filter(Boolean)

  if (candidates.length === 0) return item?.best_match || null

  const scored = candidates.map((candidate) => {
    const previous = getCandidatePrevious(candidate)
    const sharedThemes = getCandidateSharedThemes(candidate)
    const sectionKey = asText(previous?.section_key || previous?.section_type)
    const role = asText(previous?.role)

    const candidateScore =
      getCandidateScore(candidate) +
      sharedThemes.length * 8 +
      previousSectionPreferenceScore(sectionKey, role, currentThemes, currentText)

    return { candidate, candidateScore }
  })

  scored.sort((a, b) => b.candidateScore - a.candidateScore)
  return scored[0]?.candidate || item?.best_match || null
}

function getSharedThemes(item: any, selectedCandidate: any) {
  const themes =
    selectedCandidate?.similarity_details?.shared_themes ||
    item?.best_match?.similarity_details?.shared_themes ||
    item?.similarity_details?.shared_themes ||
    item?.shared_themes ||
    []

  return asArray(themes).map((theme) => asText(theme)).filter(Boolean)
}

function splitIntoBlocks(text: string) {
  const clean = asText(text).replace(/\s+/g, " ").trim()
  if (!clean) return []

  const sentences = clean
    .split(/(?<=[.!?])\s+(?=[A-ZÉÈÀÂÎÔÛÇ])/)
    .map((p) => p.replace(/\s+/g, " ").trim())
    .filter((p) => p.length > 35)

  const blocks: string[] = []

  for (let i = 0; i < sentences.length; i += 1) {
    const one = sentences[i]
    if (one && one.length <= 900) blocks.push(one)

    const two = [sentences[i], sentences[i + 1]].filter(Boolean).join(" ")
    if (two.length > 80 && two.length <= 1100) blocks.push(two)

    const three = [sentences[i], sentences[i + 1], sentences[i + 2]].filter(Boolean).join(" ")
    if (three.length > 120 && three.length <= 1250) blocks.push(three)
  }

  const paragraphs = clean
    .split(/\n{2,}|\r\n{2,}/)
    .map((p) => p.replace(/\s+/g, " ").trim())
    .filter((p) => p.length > 40 && p.length <= 1200)

  return Array.from(new Set([...blocks, ...paragraphs])).filter((block) => block.length > 35)
}



function currentKeywordsFromItem(item: any, selectedCandidate: any) {
  const keywords =
    selectedCandidate?.similarity_details?.current_keywords ||
    item?.best_match?.similarity_details?.current_keywords ||
    item?.current_keywords ||
    []

  return asArray(keywords)
    .map((kw) => normalizeForSearch(asText(kw)))
    .filter((kw) => kw.length > 2)
    .slice(0, 25)
}

function themeKeywords(themes: string[]) {
  const words = new Set<string>()

  for (const theme of themes) {
    const list = THEME_KEYWORDS[theme] || []
    for (const keyword of list) words.add(normalizeForSearch(keyword))
  }

  return Array.from(words).filter(Boolean)
}

function isGenericPreviousBlock(block: string) {
  const normalized = normalizeForSearch(block)

  const genericPatterns = [
    "nous devons donc developper des solutions techniques nouvelles",
    "ainsi le dispositif du module de compression etant un systeme complexe",
    "necessaire a chaque nouvelle implementation",
    "realiser une analyse mecanique fine",
    "consequences de cette implementation",
    "obtention des parametres du compresseur",
    "pression temperature vitesse de rotation debit et hygrometrie",
  ]

  return genericPatterns.some((pattern) => normalized.includes(pattern))
}

function isTooGenericKeyword(keyword: string) {
  const normalized = normalizeForSearch(keyword)

  return [
    "compresseur",
    "compresseurs",
    "developpement",
    "developper",
    "solution",
    "solutions",
    "techniques",
    "travaux",
    "performances",
    "parametres",
    "permettant",
    "objectif",
    "atteindre",
    "dispositif",
    "mecanique",
    "ensemble",
    "donc",
    "ainsi",
  ].includes(normalized)
}

function specificThemeScore(block: string, themes: string[], currentText: string) {
  const normalized = normalizeForSearch(block)
  const current = normalizeForSearch(currentText)

  let score = 0

  if (themes.includes("vibration_acoustique")) {
    if (/vibrations? importantes?|comportement vibratoire|nuisances acoustiques|niveau sonore|bruit|sources de bruit|trajet d aspiration|resonateur/.test(normalized)) score += 42
    if (/aspiration|gaine|acoustique|bruit/.test(current) && /aspiration|resonateur|bruit|acoustique/.test(normalized)) score += 35
  }

  if (themes.includes("thermique_refroidissement")) {
    if (/temperatures? elevees?|echauffement|refroidissement|refrigerant|debit d eau|eau de refroidissement/.test(normalized)) score += 45
    if (/refrigerant|temperature|debit d eau|refroidissement|100bar/.test(current) && /temperatures? elevees?|eau liquide|refroidissement|20 ou 40 bars|pression/.test(normalized)) score += 35
  }

  if (themes.includes("qualite_air_sechage")) {
    if (/air sec|point de rosee|hygrometrie|eau liquide|condensats?|secheur|humidite/.test(normalized)) score += 46
    if (/condensat|separateur|air sec|humidite|refrigerant/.test(current) && /eau liquide|hygrometrie|air sec|point de rosee/.test(normalized)) score += 32
  }

  if (themes.includes("usure_fiabilite_etancheite")) {
    if (/usure|fiabilite|etancheite|reniflard|fuite de l huile|huile|resistance mecanique|segments?/.test(normalized)) score += 44
    if (/segment|soufflage|reniflard|huile|usure|etancheite/.test(current) && /usure|reniflard|fuite de l huile|resistance mecanique|segments?/.test(normalized)) score += 36
  }

  if (themes.includes("performance_pression_debit")) {
    if (/debit variant entre 90 et 100|90.*100 m3|300 bars|pression de refoulement|performances techniques souhaitees/.test(normalized)) score += 34
  }

  if (themes.includes("etat_art_non_transferable")) {
    if (/non transferables?|ne sont pas applicables|connaissances existantes|plateau oscillant|barillet|bien inferieures aux performances recherchees|nous n avons pas identifie/.test(normalized)) score += 45
  }

  if (themes.includes("compromis_contraintes")) {
    if (/compromis entre l ensemble de ces performances|performances et caracteristiques|contraintes imposees|systeme complexe/.test(normalized)) score += 24
  }

  if (/contrepoids|masselotte|equilibrage|plomb|fonte|masse/.test(current)) {
    if (/masselotte|equilibrage|forces d inertie|vibrations|resistance mecanique|comportement vibratoire|compromis/.test(normalized)) score += 48
  }

  return score
}

function scorePreviousBlock(
  block: string,
  currentKeywords: string[],
  themes: string[],
  currentText = ""
) {
  const normalized = normalizeForSearch(block)
  const current = normalizeForSearch(currentText)
  let score = 0

  for (const keyword of currentKeywords) {
    const cleanKeyword = normalizeForSearch(keyword)
    if (
      cleanKeyword.length >= 3 &&
      !isTooGenericKeyword(cleanKeyword) &&
      normalized.includes(cleanKeyword)
    ) {
      score += cleanKeyword.length >= 7 ? 9 : 5
    }
  }

  for (const keyword of themeKeywords(themes)) {
    const cleanKeyword = normalizeForSearch(keyword)
    if (
      cleanKeyword.length >= 3 &&
      !isTooGenericKeyword(cleanKeyword) &&
      normalized.includes(cleanKeyword)
    ) {
      score += 8
    }
  }

  score += specificThemeScore(block, themes, currentText)

  const generic = isGenericPreviousBlock(block)
  if (generic) score -= 95

  const genericWordCount = [
    "solution",
    "solutions",
    "techniques",
    "developper",
    "parametres",
    "dispositif",
    "implementation",
    "compresseur",
  ].filter((word) => normalized.includes(word)).length

  const specificWordCount = [
    "vibration",
    "acoustique",
    "bruit",
    "aspiration",
    "temperature",
    "refroidissement",
    "refrigerant",
    "eau liquide",
    "hygrometrie",
    "air sec",
    "condensat",
    "usure",
    "reniflard",
    "fuite",
    "segment",
    "masselotte",
    "contrepoids",
    "equilibrage",
    "300 bars",
    "90",
    "100 m3",
  ].filter((word) => normalized.includes(word)).length

  if (genericWordCount >= 4 && specificWordCount <= 2) score -= 35

  if (
    current.includes("aspiration") &&
    !/aspiration|acoustique|bruit|resonateur|nuisances sonores/.test(normalized)
  ) score -= 18

  if (
    current.includes("refrigerant") &&
    !/temperature|eau liquide|refroidissement|pression|refrigerant|echauffement/.test(normalized)
  ) score -= 18

  if (
    current.includes("contrepoids") &&
    !/masselotte|equilibrage|vibration|resistance mecanique|compromis|forces d inertie/.test(normalized)
  ) score -= 18

  if (
    (current.includes("segment") || current.includes("soufflage")) &&
    !/usure|reniflard|fuite|huile|etancheite|resistance mecanique|segment/.test(normalized)
  ) score -= 22

  return score
}



function extractTargetedPreviousExcerpt(params: {
  previousText: string
  currentText: string
  currentThemes: string[]
  sharedThemes: string[]
  item: any
  selectedCandidate: any
}) {
  const { previousText, currentText, currentThemes, sharedThemes, item, selectedCandidate } = params

  const blocks = splitIntoBlocks(previousText)
  if (blocks.length === 0) return truncate(previousText, 900)

  const keywords = [
    ...currentKeywordsFromItem(item, selectedCandidate),
    ...normalizeForSearch(currentText)
      .split(" ")
      .filter((w) => w.length > 5 && !isTooGenericKeyword(w))
      .slice(0, 22),
  ]

  const themes = Array.from(new Set([...currentThemes, ...sharedThemes]))

  const scored = blocks
    .map((block, index) => ({
      block,
      index,
      score: scorePreviousBlock(block, keywords, themes, currentText),
      generic: isGenericPreviousBlock(block),
    }))
    .sort((a, b) => b.score - a.score)

  const bestNonGeneric = scored.find((entry) => !entry.generic && entry.score > 0)
  const best = bestNonGeneric || scored.find((entry) => entry.score > 8) || scored[0]

  if (!best || best.score <= -20) {
    const firstSpecific = blocks.find(
      (block) => !isGenericPreviousBlock(block) && hasTechnicalSignal(block)
    )
    return truncate(firstSpecific || blocks[0], 900)
  }

  const selected = [best]

  const before = blocks[best.index - 1]
  const after = blocks[best.index + 1]

  if (best.block.length < 520 && after && !isGenericPreviousBlock(after)) {
    const afterScore = scorePreviousBlock(after, keywords, themes, currentText)
    if (afterScore > 0) {
      selected.push({
        block: after,
        index: best.index + 1,
        score: afterScore,
        generic: false,
      })
    }
  }

  if (best.block.length < 420 && before && !isGenericPreviousBlock(before)) {
    const beforeScore = scorePreviousBlock(before, keywords, themes, currentText)
    if (beforeScore > best.score * 0.55) {
      selected.unshift({
        block: before,
        index: best.index - 1,
        score: beforeScore,
        generic: false,
      })
    }
  }

  const text = selected
    .sort((a, b) => a.index - b.index)
    .map((entry) => entry.block)
    .join("\n\n")

  return truncate(text, 1200)
}


function normalizePassage(
  raw: any,
  side: PassageSide,
  index: number,
  fallbackLabel: string,
  fallbackYear?: string
): ContinuityPassage | null {
  if (!raw) return null

  if (typeof raw === "string") {
    const text = raw.trim()
    if (!text) return null

    return {
      id: `${side}-${index}-${text.slice(0, 30)}`,
      side,
      label: fallbackLabel,
      text,
      year: fallbackYear,
    }
  }

  const text = getFirstText(
    raw.text,
    raw.passage,
    raw.excerpt,
    raw.content,
    raw.current_text,
    raw.previous_text,
    raw.source_text,
    raw.evidence_text
  )

  if (!text) return null

  return {
    id:
      asText(raw.id || raw.passage_id || raw.source_id) ||
      `${side}-${index}-${text.slice(0, 30)}`,
    side,
    label: fallbackLabel,
    text,
    document: asText(raw.document || raw.filename || raw.file_name || raw.source_document),
    section: asText(raw.section || raw.section_title || raw.role || raw.pack),
    year: asText(raw.year) || fallbackYear,
    sourcePath: asText(raw.source_path || raw.path),
    viewerUrl: asText(raw.viewer_url || raw.preview_url || raw.file_url),
    page: Number.isFinite(Number(raw.page)) ? Number(raw.page) : undefined,
    fullText: asText(raw.full_text || raw.document_text || raw.source_full_text),
  }
}

function normalizePassages(
  raw: any,
  side: PassageSide,
  fallbackLabel: string,
  fallbackYear?: string
) {
  return asArray(raw)
    .flatMap((item) => {
      if (item?.supporting_passages) return asArray(item.supporting_passages)
      return [item]
    })
    .map((item, index) => normalizePassage(item, side, index, fallbackLabel, fallbackYear))
    .filter(Boolean) as ContinuityPassage[]
}

function hasTechnicalSignal(text: string) {
  const clean = text.toLowerCase()

  return /compresseur|tgm100|pression|bar|débit|debit|m3\/h|vibration|vibratoire|acoustique|bruit|contrepoids|masselotte|équilibr|equilibr|réfrigérant|refrigerant|température|temperature|refroidissement|débit d'eau|debit d'eau|condensat|séparateur|separateur|segment|usure|étanchéité|etancheite|reniflard|soufflage|air sec|hygrométrie|hygrometrie|sous-marin|snle|essai|mesure|épreuve|epreuve|hydraulique/.test(
    clean
  )
}

function isAdministrativeNoise(text: string) {
  const clean = text.toLowerCase()

  const adminSignals = [
    /téléphone|telephone/,
    /written by|rédigé|redige/,
    /date modification/,
    /chemin du bas des indes/,
    /cormeilles-en-parisis/,
    /urban-valley/,
    /page \d+ sur \d+/,
    /référence cahier des charges|reference cahier des charges/,
    /échantillons|echantillons|conservés 3 mois|conserves 3 mois/,
  ]

  const hasAdmin = adminSignals.some((regex) => regex.test(clean))
  const hasOnlyWeakTech =
    !hasTechnicalSignal(clean) ||
    (/telephone|urban-valley|chemin du bas des indes/.test(clean) &&
      !/essai|mesure|vibration|acoustique|température|temperature|réfrigérant|refrigerant|contrepoids|compresseur/.test(
        clean
      ))

  return hasAdmin && hasOnlyWeakTech
}

function isWeakFragment(text: string) {
  const clean = text.replace(/\s+/g, " ").trim()

  if (clean.length < 60) return true
  if (isAdministrativeNoise(clean)) return true

  const words = clean.split(/\s+/)
  const numericLike = words.filter((word) => /\d/.test(word)).length
  const ratio = words.length > 0 ? numericLike / words.length : 0

  if (ratio > 0.45 && !hasTechnicalSignal(clean)) return true

  return false
}

function buildQuality(item: any, selectedCandidate: any, currentText: string, previousText: string) {
  const similarityScore = getCandidateScore(selectedCandidate) || getSimilarityScore(item)
  const continuityScore = getContinuityScore(item)
  const noveltyScore = getNoveltyScore(item)
  const sharedThemesCount = getSharedThemes(item, selectedCandidate).length

  const technical = hasTechnicalSignal(currentText)
  const noise = isWeakFragment(currentText)

  let keep = true
  let qualityLevel: QualityLevel = "weak"
  let qualityLabel = "Repère faible à vérifier"
  let qualityExplanation =
    "Correspondance faible : à utiliser uniquement comme indice, pas comme preuve directe."

  if (noise) {
    keep = false
    qualityExplanation =
      "Fragment masqué car il ressemble à une référence administrative, une adresse, une fiche fournisseur ou un extrait sans valeur CIR suffisante."
  } else if (!technical) {
    keep = false
    qualityExplanation =
      "Fragment masqué car aucun signal technique suffisant n’a été détecté."
  } else if (similarityScore < 25 && sharedThemesCount < 2) {
    keep = false
    qualityExplanation =
      "Fragment masqué car le score et les thèmes communs sont trop faibles."
  } else if (similarityScore >= 50 || continuityScore >= 60) {
    qualityLevel = "strong"
    qualityLabel = "Continuité forte"
    qualityExplanation =
      "Les passages portent sur un même axe technique avec une correspondance suffisamment forte."
  } else if (similarityScore >= 35 || sharedThemesCount >= 3) {
    qualityLevel = "partial"
    qualityLabel = "Continuité partielle à valider"
    qualityExplanation =
      "La comparaison est pertinente, mais elle doit être validée par le consultant car le dossier courant apporte de nouveaux éléments."
  } else {
    qualityLevel = "weak"
    qualityLabel = "Repère faible à vérifier"
    qualityExplanation =
      "La comparaison peut aider à repérer une continuité, mais elle n’est pas assez forte pour être présentée comme une preuve directe."
  }

  if (!previousText || previousText.length < 80) {
    keep = false
    qualityExplanation =
      "Fragment masqué car le passage du CIR précédent est insuffisant."
  }

  return {
    keep,
    qualityLevel,
    qualityLabel,
    qualityExplanation,
    similarityScore,
    continuityScore,
    noveltyScore,
    sharedThemesCount,
  }
}

function buildContinuityItems(diagnostic: AnyObject) {
  const display = diagnostic?.display || {}
  const memory = getMemory(diagnostic)

  const previousYears =
    display?.cir_memory_previous_years ||
    memory?.previous_cir_years_used ||
    memory?.previous_years ||
    []

  const fallbackPreviousYear = asText(previousYears?.[0] || memory?.previous_year || "")
  const rawItems = getComparisonRawItems(diagnostic)

  const uniqueItems = rawItems.filter((item, index, arr) => {
    const key = JSON.stringify({
      id: item?.current_item?.id || item?.id,
      text: item?.current_item?.text || item?.text,
    }).slice(0, 500)

    return (
      arr.findIndex((other) =>
        JSON.stringify({
          id: other?.current_item?.id || other?.id,
          text: other?.current_item?.text || other?.text,
        })
          .slice(0, 500)
          .includes(key.slice(0, 120))
      ) === index
    )
  })

  const allItems = uniqueItems.map((item, index) => {
    const currentItem = item?.current_item || item?.current || item
    const selectedCandidate = chooseBestCandidate(item)
    const previousCandidate = getCandidatePrevious(selectedCandidate)
    const currentThemes = getCurrentThemes(item)
    const sharedThemes = getSharedThemes(item, selectedCandidate)

    const title =
      getFirstText(
        item.title,
        item.theme_label,
        item.verrou_title,
        currentItem?.theme_label,
        currentItem?.theme_id,
        currentItem?.title,
        currentItem?.section_title
      ) || `Continuité technique ${index + 1}`

    const previousYear = asText(previousCandidate?.year || fallbackPreviousYear)
    const currentText = asText(currentItem?.text || item?.text)
    const rawPreviousText = asText(previousCandidate?.text || selectedCandidate?.text || item?.best_match?.text)

    const targetedPreviousText = extractTargetedPreviousExcerpt({
      previousText: rawPreviousText,
      currentText,
      currentThemes,
      sharedThemes,
      item,
      selectedCandidate,
    })

    const quality = buildQuality(item, selectedCandidate, currentText, targetedPreviousText)

    const currentPassages = normalizePassages(
      item.current_passages ||
        item.current_evidence ||
        item.current_sources ||
        item.current_chunks ||
        currentItem?.supporting_passages ||
        currentItem?.text ||
        currentItem?.source_text,
      "current",
      "Passage du dossier courant"
    )

    const previousSectionTitle = asText(previousCandidate?.section_title || previousCandidate?.section_key || previousCandidate?.role)
    const previousSourceFile = asText(previousCandidate?.source_file || "CIR précédent")
    const previousMemoryPath = asText(previousCandidate?.previous_memory_path || previousCandidate?.source_path)

    const previousPassages: ContinuityPassage[] = targetedPreviousText
      ? [
          {
            id: `previous-targeted-${index}`,
            side: "previous",
            label: `Extrait ciblé du CIR précédent`,
            text: targetedPreviousText,
            document: previousSourceFile,
            section: previousSectionTitle,
            year: previousYear,
            sourcePath: previousMemoryPath,
            fullText: rawPreviousText,
          },
        ]
      : normalizePassages(
          previousCandidate?.supporting_passages ||
            previousCandidate?.text ||
            previousCandidate?.source_text ||
            item?.best_match?.text,
          "previous",
          "Passage du CIR précédent",
          previousYear
        )

    const fallbackCommon: ContinuityPassage[] = []

    if (previousPassages.length > 0 || currentPassages.length > 0) {
      const wording =
        quality.qualityLevel === "strong"
          ? "Les deux passages ci-dessous constituent une preuve forte de continuité technique."
          : quality.qualityLevel === "partial"
            ? "Les deux passages ci-dessous montrent une continuité partielle à valider."
            : "Les deux passages ci-dessous constituent un repère faible à vérifier."

      fallbackCommon.push({
        id: `common-${index}`,
        side: "common",
        label: quality.qualityLabel,
        text:
          `Le CIR précédent contient un extrait ciblé dans « ${previousSectionTitle || "section CIR précédente"} ». ` +
          `Le dossier courant apporte un élément proche ou évolutif sur « ${currentItem?.theme_id || title} ». ${wording}`,
        document: "Comparaison CIR N-1 / dossier courant",
        year: previousYear,
      })
    }

    return {
      id:
        asText(item.id || item.theme_id || currentItem?.theme_id || currentItem?.id || item.verrou_id) ||
        `continuity-${index}`,
      title,
      theme: asText(currentItem?.theme_id || item.theme || item.category),
      similarityScore: quality.similarityScore,
      continuityScore: quality.continuityScore,
      noveltyScore: quality.noveltyScore,
      sharedThemesCount: quality.sharedThemesCount,
      qualityLevel: quality.qualityLevel,
      qualityLabel: quality.qualityLabel,
      qualityExplanation: quality.qualityExplanation,
      currentPassages,
      previousPassages,
      commonPassages: fallbackCommon,
      _keep: quality.keep,
    }
  })

  const visibleItems = allItems
    .filter((item: any) => item._keep)
    .sort((a, b) => {
      const rank = { strong: 3, partial: 2, weak: 1 }
      return (
        rank[b.qualityLevel] - rank[a.qualityLevel] ||
        b.similarityScore - a.similarityScore ||
        b.sharedThemesCount - a.sharedThemesCount
      )
    })
    .map(({ _keep, ...item }: any) => item as ContinuityItem)

  const hiddenCount = allItems.length - visibleItems.length

  return { visibleItems, hiddenCount, totalCount: allItems.length }
}

function buildPreviewUrl(params: {
  passage: ContinuityPassage
  projectId: number
  apiBaseUrl: string
  organisme: string
  projectName: string
  previousYear: string
}) {
  const { passage, projectId, apiBaseUrl, organisme, projectName, previousYear } = params

  if (passage.viewerUrl) return passage.viewerUrl

  const base = apiBaseUrl.replace(/\/$/, "")
  const highlight = encodeURIComponent(passage.text.slice(0, 3500))

  if (passage.sourcePath && !passage.sourcePath.toLowerCase().endsWith(".json")) {
    const sourcePath = encodeURIComponent(passage.sourcePath)
    const url = `${base}/projects/${projectId}/source-preview?source_path=${sourcePath}&highlight=${highlight}`

    if (passage.sourcePath.toLowerCase().endsWith(".pdf")) {
      return `${url}#search=${encodeURIComponent(passage.text.slice(0, 80))}`
    }

    return url
  }

  if (passage.side === "previous") {
    const year = encodeURIComponent(passage.year || previousYear)
    const org = encodeURIComponent(organisme)
    const proj = encodeURIComponent(projectName)

    return `${base}/projects/${projectId}/cir-previous/preview?organisme=${org}&project=${proj}&year=${year}&highlight=${highlight}`
  }

  return ""
}

function highlightText(fullText: string, passage: string) {
  if (!fullText) {
    return (
      <p className="text-sm leading-6 bg-yellow-100/80 rounded-md p-3 whitespace-pre-wrap">
        {passage}
      </p>
    )
  }

  const needle = passage.replace(/\s+/g, " ").trim().slice(0, 160)
  const normalizedFull = fullText.replace(/\s+/g, " ")
  const index = normalizedFull.toLowerCase().indexOf(needle.toLowerCase())

  if (index === -1) {
    return (
      <div className="space-y-3">
        <p className="text-xs text-muted-foreground">
          Le passage exact n’a pas été retrouvé dans le texte complet. Extrait source :
        </p>
        <p className="text-sm leading-6 bg-yellow-100/80 rounded-md p-3 whitespace-pre-wrap">
          {passage}
        </p>
        <div className="text-sm leading-6 whitespace-pre-wrap border rounded-md p-3 max-h-[65vh] overflow-auto">
          {fullText}
        </div>
      </div>
    )
  }

  const before = normalizedFull.slice(Math.max(0, index - 900), index)
  const match = normalizedFull.slice(index, index + needle.length)
  const after = normalizedFull.slice(index + needle.length, index + needle.length + 1400)

  return (
    <p className="text-sm leading-6 whitespace-pre-wrap">
      {before}
      <mark className="bg-yellow-200 rounded px-1 py-0.5">{match}</mark>
      {after}
    </p>
  )
}

function qualityBadgeClass(level: QualityLevel) {
  if (level === "strong") return "bg-emerald-50 text-emerald-700 border-emerald-200"
  if (level === "partial") return "bg-blue-50 text-blue-700 border-blue-200"
  return "bg-amber-50 text-amber-700 border-amber-200"
}

function PassageButton({
  passage,
  onOpen,
}: {
  passage: ContinuityPassage
  onOpen: (passage: ContinuityPassage) => void
}) {
  const isPrevious = passage.side === "previous"
  const isCurrent = passage.side === "current"

  return (
    <button
      type="button"
      onClick={() => onOpen(passage)}
      className={`w-full text-left rounded-lg border p-3 transition-all hover:shadow-sm ${
        isPrevious
          ? "border-blue-200 bg-blue-50/60 hover:bg-blue-50"
          : isCurrent
            ? "border-emerald-200 bg-emerald-50/60 hover:bg-emerald-50"
            : "border-amber-200 bg-amber-50/60 hover:bg-amber-50"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-xs font-semibold text-foreground">{passage.label}</p>

          {(passage.document || passage.section || passage.year) && (
            <p className="text-[11px] text-muted-foreground mt-0.5 truncate">
              {[passage.year, passage.document, passage.section].filter(Boolean).join(" · ")}
            </p>
          )}
        </div>

        <Maximize2 className="size-4 text-muted-foreground flex-shrink-0" />
      </div>

      <p className="text-xs leading-5 text-muted-foreground mt-2">
        {truncate(passage.text, 360)}
      </p>
    </button>
  )
}

function SourceDrawer({
  passage,
  expanded,
  onToggleExpanded,
  onClose,
  previewUrl,
}: {
  passage: ContinuityPassage | null
  expanded: boolean
  onToggleExpanded: () => void
  onClose: () => void
  previewUrl: string
}) {
  if (!passage) return null

  return (
    <div className="fixed inset-0 z-50">
      <div className="absolute inset-0 bg-black/20" onClick={onClose} />

      <aside
        className={`absolute right-0 top-0 h-full bg-background border-l border-border shadow-2xl transition-all ${
          expanded ? "w-[92vw]" : "w-[52vw]"
        } max-w-[1400px] min-w-[440px]`}
      >
        <div className="h-14 border-b border-border px-4 flex items-center justify-between gap-3">
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground truncate">
              {passage.document ||
                (passage.side === "previous" ? "CIR précédent" : "Source documentaire")}
            </p>
            <p className="text-xs text-muted-foreground truncate">
              {[passage.year, passage.section, passage.sourcePath].filter(Boolean).join(" · ")}
            </p>
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="ghost" size="sm" onClick={onToggleExpanded}>
              {expanded ? <Minimize2 className="size-4" /> : <Maximize2 className="size-4" />}
            </Button>

            <Button variant="ghost" size="sm" onClick={onClose}>
              <X className="size-4" />
            </Button>
          </div>
        </div>

        <div className="h-[calc(100%-3.5rem)] overflow-auto p-4 space-y-4">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <Link2 className="size-4 text-brand" />
                Passage sélectionné
              </CardTitle>
              <CardDescription className="text-xs">
                Ce passage sert de repère de continuité technique entre le CIR précédent et le dossier courant.
              </CardDescription>
            </CardHeader>

            <CardContent>
              <p className="text-sm leading-6 bg-yellow-100/80 rounded-md p-3 whitespace-pre-wrap">
                {passage.text}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileSearch className="size-4 text-brand" />
                Document complet / extrait contextualisé
              </CardTitle>
              <CardDescription className="text-xs">
                Le document source est chargé depuis le backend. Pour un PDF, le fichier original s’affiche. Pour un DOCX ou une mémoire CIR, l’aperçu texte est affiché avec le passage surligné.
              </CardDescription>
            </CardHeader>

            <CardContent>
              {previewUrl ? (
                <iframe
                  title="Document source"
                  src={previewUrl}
                  className="w-full h-[74vh] rounded-md border border-border bg-white"
                />
              ) : (
                <div className="rounded-md border border-border p-4 max-h-[72vh] overflow-auto">
                  {highlightText(passage.fullText || "", passage.text)}
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </aside>
    </div>
  )
}

export default function CirPreviousContinuityTab({
  diagnostic,
  projectId,
  apiBaseUrl,
  organisme,
  projectName,
  currentYear,
}: CirPreviousContinuityTabProps) {
  const { visibleItems, hiddenCount, totalCount } = useMemo(
    () => buildContinuityItems(diagnostic),
    [diagnostic]
  )

  const memory = getMemory(diagnostic)
  const display = diagnostic?.display || {}

  const previousYears =
    display?.cir_memory_previous_years ||
    memory?.previous_cir_years_used ||
    memory?.previous_years ||
    []

  const previousYear =
    asText(previousYears?.[0] || memory.previous_year || memory.year || memory.cir_year) ||
    (Number.isFinite(Number(currentYear)) ? String(Number(currentYear) - 1) : "N-1")

  const [selectedPassage, setSelectedPassage] = useState<ContinuityPassage | null>(null)
  const [drawerExpanded, setDrawerExpanded] = useState(false)

  const registeredPreviousCirs = asArray(
    (diagnostic as any)?.registered_previous_cirs || memory?.registered_previous_cirs
  )
  const memorySummary = memory?.summary || (diagnostic as any)?.cir_memory_summary || {}

  const hasPreviousCir = Boolean(
    memory?.has_previous_cir === true ||
      memory?.previous_cir_available === true ||
      Number(memory?.previous_cir_items_count || 0) > 0 ||
      Number(memorySummary?.previous_cir_items_count || 0) > 0 ||
      Number(memory?.registered_previous_cirs_count || 0) > 0 ||
      Number(memorySummary?.registered_previous_cirs_count || 0) > 0 ||
      registeredPreviousCirs.length > 0 ||
      (Array.isArray(previousYears) && previousYears.length > 0) ||
      totalCount > 0 ||
      (diagnostic as any)?.previous_cir_available === true ||
      (diagnostic as any)?.has_previous_cir === true ||
      (diagnostic as any)?.cir_memory_has_previous === true
  )

  const previewUrl = selectedPassage
    ? buildPreviewUrl({
        passage: selectedPassage,
        projectId,
        apiBaseUrl,
        organisme,
        projectName,
        previousYear,
      })
    : ""

  if (!hasPreviousCir) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Continuité avec le CIR précédent</CardTitle>
          <CardDescription className="text-xs">
            
          </CardDescription>
        </CardHeader>

        <CardContent>
          <p className="text-sm text-muted-foreground">
            Aucun CIR précédent exploitable n’est encore détecté par cette vue. Si le CIR N-1 est déjà enregistré, lance la comparaison CIR précédent puis actualise la page.
          </p>
        </CardContent>
      </Card>
    )
  }

  const averageContinuity =
    visibleItems.length > 0
      ? Math.round(
          visibleItems.reduce((sum, item) => sum + (item.continuityScore || 0), 0) /
            visibleItems.length
        )
      : 0

  return (
    <div className="space-y-6">
      <Card className="border-brand/20 bg-brand/5">
        <CardHeader>
          <CardTitle className="text-sm flex items-center gap-2">
            <ShieldCheck className="size-4 text-brand" />
            Comparaison CIR N-1 avec extraits ciblés
          </CardTitle>

          <CardDescription className="text-xs">
            Cette vue n’affiche plus toute la section du CIR précédent. Elle extrait seulement le passage le plus proche du sujet courant pour éviter de répéter « Verrous et incertitudes » partout.
          </CardDescription>
        </CardHeader>

        <CardContent className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">CIR précédent</p>
            <p className="text-xl font-bold text-foreground">{previousYear}</p>
          </div>

          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Comparaisons utiles</p>
            <p className="text-xl font-bold text-foreground">{visibleItems.length}</p>
          </div>

          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Masquées</p>
            <p className="text-xl font-bold text-foreground">{hiddenCount}</p>
          </div>

          <div className="rounded-lg border bg-background p-3">
            <p className="text-xs text-muted-foreground">Continuité moyenne</p>
            <p className="text-xl font-bold text-foreground">{averageContinuity}%</p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Passages retenus pour la continuité</CardTitle>
          <CardDescription className="text-xs">
            Cliquez sur un passage pour ouvrir le document source dans un panneau latéral.
          </CardDescription>
        </CardHeader>

        <CardContent className="space-y-4">
          {visibleItems.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {totalCount === 0
                ? "Le CIR précédent est disponible, mais aucune comparaison N-1/N n’a été chargée dans ce rapport. Clique sur « Lancer la comparaison CIR précédent »."
                : "Aucune comparaison assez fiable n’a été retenue après filtrage."}
            </p>
          )}

          {visibleItems.map((item) => (
            <div key={item.id} className="rounded-xl border border-border p-4 space-y-4 bg-background">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p className="text-sm font-semibold text-foreground">{item.title}</p>

                  {item.theme && (
                    <p className="text-xs text-muted-foreground mt-0.5">{item.theme}</p>
                  )}

                  <p className="text-xs text-muted-foreground mt-2 max-w-3xl">
                    {item.qualityExplanation}
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline" className={qualityBadgeClass(item.qualityLevel)}>
                    {item.qualityLabel}
                  </Badge>

                  <Badge variant="outline">similarité {item.similarityScore}%</Badge>

                  <Badge variant="outline">thèmes communs {item.sharedThemesCount}</Badge>

                  {item.noveltyScore > 0 && (
                    <Badge variant="outline" className="bg-emerald-50 text-emerald-700 border-emerald-200">
                      apport courant {item.noveltyScore}%
                    </Badge>
                  )}
                </div>
              </div>

              {item.commonPassages.length > 0 && (
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-foreground">Lecture consultant</p>

                  <div className="grid grid-cols-1 gap-2">
                    {item.commonPassages.map((passage) => (
                      <PassageButton key={passage.id} passage={passage} onOpen={setSelectedPassage} />
                    ))}
                  </div>
                </div>
              )}

              <div className="grid grid-cols-1 lg:grid-cols-[1fr_auto_1fr] gap-3 items-start">
                <div className="space-y-2">
                  <p className="text-xs font-semibold text-blue-700">Extrait ciblé du CIR précédent</p>

                  {item.previousPassages.length > 0 ? (
                    item.previousPassages.slice(0, 1).map((passage) => (
                      <PassageButton key={passage.id} passage={passage} onOpen={setSelectedPassage} />
                    ))
                  ) : (
                    <p className="text-xs text-muted-foreground rounded-lg border p-3">
                      Aucun extrait ciblé du CIR précédent n’est attaché à cette comparaison.
                    </p>
                  )}
                </div>

                <div className="hidden lg:flex h-full items-center pt-8">
                  <ArrowRight className="size-5 text-muted-foreground" />
                </div>

                <div className="space-y-2">
                  <p className="text-xs font-semibold text-emerald-700">Passage du dossier courant</p>

                  {item.currentPassages.length > 0 ? (
                    item.currentPassages.slice(0, 2).map((passage) => (
                      <PassageButton key={passage.id} passage={passage} onOpen={setSelectedPassage} />
                    ))
                  ) : (
                    <p className="text-xs text-muted-foreground rounded-lg border p-3">
                      Aucun extrait détaillé du dossier courant n’est attaché à cette comparaison.
                    </p>
                  )}
                </div>
              </div>
            </div>
          ))}
        </CardContent>
      </Card>

      <SourceDrawer
        passage={selectedPassage}
        expanded={drawerExpanded}
        previewUrl={previewUrl}
        onToggleExpanded={() => setDrawerExpanded((prev) => !prev)}
        onClose={() => setSelectedPassage(null)}
      />
    </div>
  )
}

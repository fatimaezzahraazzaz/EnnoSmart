// V5 UI snapshot reusing the V4 interface; V4 source remains untouched.
"use client"

import { useEffect, useState } from "react"
import { ArrowDown, ArrowUp } from "lucide-react"
import { Button } from "@/components/ui/button"
import { decideArticle, getArticle, orderArticles, safeUrl, type Article, type Claim, type Decision, type Session } from "@/lib/scholar-v5"

const fieldLabels: [keyof Article["analysis"], string][] = [
  ["problem", "Problème"], ["contribution", "Contribution"], ["method", "Méthode"], ["technical_mechanism", "Mécanismes et algorithmes"],
  ["equations", "Équations"], ["parameters", "Paramètres"], ["experimental_setup", "Protocole expérimental"],
  ["datasets_materials", "Données et matériaux"], ["results", "Résultats"], ["metrics", "Métriques"],
  ["tables_figures", "Tableaux et figures"], ["comparisons", "Comparaisons"], ["limitations", "Limites démontrées"],
  ["conditions_of_validity", "Conditions de validité"],
]

const basisLabels: Record<string, string> = { fulltext: "analyse ciblée du texte intégral", abstract: "analyse sur l’abstract seul", metadata: "métadonnées seules" }

export const decisionLabels: Record<Decision, string> = { accept: "Garder", hold: "En attente", reject: "Écarter", restore: "Rétablir" }

function articleUrl(a: Article) {
  return safeUrl(a.doi ? `https://doi.org/${a.doi}` : a.arxiv_id ? `https://arxiv.org/abs/${a.arxiv_id}` : a.url)
}

/** The saved scientific card with its exact proofs; nothing is regenerated here. */
export function ArticleCard({ sid, articleId }: { sid: string; articleId: string }) {
  const [article, setArticle] = useState<Article | null>(null)
  const [error, setError] = useState("")
  useEffect(() => {
    const controller = new AbortController()
    getArticle(sid, articleId, controller.signal).then(setArticle).catch(e => { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "Fiche indisponible.") })
    return () => controller.abort()
  }, [sid, articleId])
  if (error) return <p role="alert" className="text-destructive">{error}</p>
  if (!article) return <p role="status">Chargement de la fiche…</p>
  const proofs = Object.fromEntries((article.analysis.evidence || []).map(e => [e.evidence_id, e]))
  const fields = fieldLabels.filter(([key]) => Array.isArray(article.analysis[key]) && (article.analysis[key] as Claim[]).length)
  return <div className="mt-3 space-y-3 text-sm">
    <p className="text-xs text-muted-foreground">{article.analysis_basis ? basisLabels[article.analysis_basis] : "Aucune analyse sauvegardée"}{article.analysis.reading_coverage ? " · lecture ciblée, non exhaustive" : ""}{article.analysis.fulltext_truncated ? " · extraction tronquée" : ""}</p>
    {!fields.length && <p className="text-muted-foreground">Aucune affirmation étayée n’est encore enregistrée pour cette publication.</p>}
    {fields.map(([key, label]) => <section key={key}><h4 className="font-medium">{label}</h4><ul className="mt-1 space-y-2">{(article.analysis[key] as Claim[]).map((claim, i) => <li key={i} className="rounded-lg border p-2">
      <p>{claim.statement}{claim.origin === "inference" && <span className="ml-1 text-xs text-muted-foreground">(inférence)</span>}</p>
      {claim.evidence_ids.map(eid => proofs[eid] && <blockquote key={eid} className="mt-2 border-l-2 pl-3 text-xs text-muted-foreground">« {proofs[eid].exact_quote || proofs[eid].text} » — {proofs[eid].section || "section non identifiée"}{(proofs[eid].page_number || proofs[eid].page) ? `, page ${proofs[eid].page_number || proofs[eid].page}` : ""}</blockquote>)}
    </li>)}</ul></section>)}
  </div>
}

/** Consultant validation of the proposed publications: keep, hold, discard, reorder. */
export default function SelectionPanel({ session, disabled, onChanged, ask }: {
  session: Session; disabled: boolean; onChanged: () => Promise<void>; ask: (text: string) => void
}) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [open, setOpen] = useState<string>("")
  const byId = Object.fromEntries(session.articles.map(a => [a.id, a]))
  const order = session.recommended_article_ids.filter(id => byId[id])
  const held = (session.held_article_ids || []).filter(id => byId[id] && !order.includes(id))
  if (!order.length && !held.length) return null
  async function run(task: () => Promise<unknown>) {
    if (busy || disabled) return
    setBusy(true); setError("")
    try { await task(); await onChanged() } catch (e) { setError(e instanceof Error ? e.message : "Décision non enregistrée.") }
    finally { setBusy(false) }
  }
  const move = (index: number, delta: number) => {
    const next = [...order]; const [item] = next.splice(index, 1); next.splice(index + delta, 0, item)
    return run(() => orderArticles(session.id, next))
  }
  const row = (id: string, index: number | null) => {
    const a = byId[id]; const url = articleUrl(a)
    return <li key={id} className="border-t pt-3">
      <p className="font-medium">{index !== null && `${index + 1}. `}{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline underline-offset-4">{a.title}</a> : a.title}</p>
      <p className="mt-1 text-xs text-muted-foreground">{a.year || "Année non confirmée"} · {a.analysis_basis ? basisLabels[a.analysis_basis] : "pas encore analysée"}{a.selection_override === true ? " · gardée par le consultant" : ""}{!a.verified ? " · identité bibliographique à vérifier" : ""}</p>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        {(["accept", "hold", "reject"] as Decision[]).map(d => <Button key={d} type="button" size="sm" variant={d === "accept" && a.selection_override === true ? "secondary" : "ghost"} disabled={busy || disabled} onClick={() => run(() => decideArticle(session.id, id, d))}>{decisionLabels[d]}</Button>)}
        {index !== null && <><Button type="button" size="icon" variant="ghost" className="size-8" aria-label={`Monter ${a.title}`} disabled={busy || disabled || index === 0} onClick={() => move(index, -1)}><ArrowUp aria-hidden="true" className="size-4" /></Button>
          <Button type="button" size="icon" variant="ghost" className="size-8" aria-label={`Descendre ${a.title}`} disabled={busy || disabled || index === order.length - 1} onClick={() => move(index, 1)}><ArrowDown aria-hidden="true" className="size-4" /></Button></>}
        <Button type="button" size="sm" variant="ghost" aria-expanded={open === id} onClick={() => setOpen(open === id ? "" : id)}>{open === id ? "Masquer la fiche" : "Fiche et preuves"}</Button>
        <Button type="button" size="sm" variant="ghost" disabled={disabled} onClick={() => ask(`Approfondis la publication « ${a.title} » (identifiant ${a.id}) : méthodes, paramètres, protocoles, résultats numériques et limites, en réutilisant les analyses enregistrées.`)}>Approfondir</Button>
      </div>
      {open === id && <ArticleCard sid={session.id} articleId={id} />}
    </li>
  }
  return <details className="mx-auto mt-5 max-w-3xl rounded-xl border p-3 text-sm" open>
    <summary className="cursor-pointer font-medium">Publications proposées ({order.length}){held.length ? ` · en attente (${held.length})` : ""}</summary>
    <p className="mt-2 text-muted-foreground">Vos décisions sont enregistrées et prévalent sur le classement de l’assistant. Garder une publication ne valide ni une affirmation non vérifiée, ni une éligibilité CIR.</p>
    {error && <p role="alert" className="mt-2 text-destructive">{error}</p>}
    <ol className="mt-3 space-y-3">{order.map((id, index) => row(id, index))}</ol>
    {held.length > 0 && <><h4 className="mt-4 font-medium">En attente</h4><ul className="mt-2 space-y-3">{held.map(id => row(id, null))}</ul></>}
    <Button type="button" variant="outline" size="sm" className="mt-3" disabled={disabled} onClick={() => ask("Cherche d’autres publications pertinentes, plus récentes si possible, sans répéter les recherches déjà faites.")}>Demander d’autres publications</Button>
  </details>
}

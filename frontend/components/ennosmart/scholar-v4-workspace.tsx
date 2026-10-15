"use client"

import { useEffect, useState } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import { changeDocument, exportDocument, getDocument, getFigure, safeUrl, type ScientificDocument, type ScientificFigure, type ScientificSection } from "@/lib/scholar-v4"

const field = "w-full rounded-lg border bg-background px-3 py-2 text-sm focus-visible:outline-primary"

function Markdown({ text }: { text: string }) {
  return <div className="prose prose-sm max-w-none break-words dark:prose-invert"><ReactMarkdown remarkPlugins={[remarkGfm]} components={{
    table: props => <div className="overflow-x-auto"><table {...props} className="w-full border-collapse" /></div>,
    a: ({ href, children }) => {
      const url = safeUrl(href || "")
      return url && !url.includes("cookies_not_supported") ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline">{children}</a> : <span>{children}</span>
    },
  }}>{text}</ReactMarkdown></div>
}

function Figure({ sid, figure }: { sid: string; figure: ScientificFigure }) {
  const [url, setUrl] = useState("")
  const [error, setError] = useState("")
  useEffect(() => {
    let active = true; let objectUrl = ""
    getFigure(sid, figure.id).then(blob => { objectUrl = URL.createObjectURL(blob); if (active) setUrl(objectUrl) })
      .catch(e => { if (active) setError(String(e.message)) })
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl) }
  }, [sid, figure])
  return <figure className="my-6">
    {url && <img src={url} alt={figure.scientific_argument} className="mx-auto max-h-[650px] max-w-full object-contain" />}
    {error && <p role="alert">{error}</p>}
    <figcaption className="mt-2 text-sm leading-6"><strong>{figure.title}.</strong> {figure.caption}{figure.origin === "agent_synthesis" && " Schéma de synthèse généré à partir des sources."}{figure.license_url && <a href={safeUrl(figure.license_url)} className="ml-2 underline">Conditions de réutilisation</a>}</figcaption>
  </figure>
}

type Action = (payload: Record<string, unknown>) => Promise<void>

function ManuscriptSection({ section, number, data, sid, disabled, act, ask }: {
  section: ScientificSection; number: number; data: ScientificDocument; sid: string;
  disabled: boolean; act: Action; ask: (text: string) => void
}) {
  const draft = section.drafts.at(-1)!
  const [text, setText] = useState(draft.text)
  const [paragraph, setParagraph] = useState("")
  const [historicalDraft, setHistoricalDraft] = useState("")
  useEffect(() => { setText(draft.text); setParagraph(""); setHistoricalDraft("") }, [draft])
  const toVisible = (value: string) => value.replace(/\[(paper_[\w-]+|ev_[\w-]+)\]/g, (_, id: string) => {
    const article = data.articles.find(a => a.id === id)
    const proof = data.evidence[id]
    const url = article ? (article.doi ? `https://doi.org/${article.doi}` : article.arxiv_id ? `https://arxiv.org/abs/${article.arxiv_id}` : article.url) : proof?.source_url
    return url && safeUrl(url) ? `[${article?.title || "Source"}](${url})` : article?.title || "[Référence à vérifier]"
  })
  const figures = data.figures.filter(f => f.section_id === section.id)
  const evidenceIds = [...new Set([...(draft.evidence_ids || []), ...figures.flatMap(f => f.evidence_ids)])]
  return <article id={`scholar-section-${section.id}`} className="border-t py-7 scroll-mt-4" aria-label={section.title}>
    <h3 className="mb-5 text-lg font-semibold">{number}. {section.title.replace(/^\s*\d+[.)]\s+/, "")}</h3>
    <Markdown text={draft.text} />
    {figures.map(figure => <div key={figure.id}><Figure sid={sid} figure={figure} /><div className="flex flex-wrap gap-2"><Button variant="ghost" size="sm" disabled={disabled} onClick={() => ask(`Modifie la figure « ${figure.title} » de la section « ${section.title} » à partir des preuves enregistrées avec create_scientific_visual.`)}>Réviser cette figure dans le chat</Button><Button variant="ghost" size="sm" disabled={disabled} onClick={() => act({ action: "remove_figure", figure_id: figure.id })}>Retirer la figure</Button></div></div>)}
    <div className="mt-5 flex flex-wrap gap-2">
      <Button variant="outline" size="sm" disabled={disabled} onClick={() => ask(`Approfondis la section ${number} « ${section.title} » en réutilisant les analyses et preuves enregistrées. Sauvegarde uniquement cette révision locale.`)}>Réviser cette section dans le chat</Button>
    </div>
    <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium">Modifier le texte ou restaurer une version</summary>
      <div className="mt-3 space-y-3">
        <label className="block">Passage à modifier<select className={field} value={paragraph} disabled={disabled} onChange={e => { setParagraph(e.target.value); setText(e.target.value ? toVisible(draft.paragraphs?.find(p => p.id === e.target.value)?.text || "") : draft.text) }}><option value="">Section complète</option>{draft.paragraphs?.map((p, i) => <option key={p.id} value={p.id}>Paragraphe {i + 1} — {toVisible(p.text).slice(0, 70)}</option>)}</select></label>
        <label className="block">Texte du brouillon<textarea className={`${field} min-h-60`} disabled={disabled} value={text} onChange={e => setText(e.target.value)} /></label>
        <Button disabled={disabled || !text.trim()} onClick={() => act({ action: "edit_draft", section_id: section.id, text, paragraph_id: paragraph || null })}>Enregistrer la révision</Button>
        {section.drafts.length > 1 && <label className="block">Restaurer une version<select className={field} value="" disabled={disabled} onChange={e => { if (e.target.value) act({ action: "restore_draft", section_id: section.id, draft_id: e.target.value }) }}><option value="">Choisir une version</option>{section.drafts.slice(0, -1).map((d, i) => <option key={d.id} value={d.id}>Version {i + 1}</option>)}</select></label>}
      </div>
    </details>
    {section.drafts.length > 1 && <details className="mt-4 text-sm"><summary className="cursor-pointer font-medium">Consulter les anciennes versions ({section.drafts.length - 1})</summary>
      <select aria-label={`Version de ${section.title}`} className={`${field} mt-3`} value={historicalDraft} onChange={e => setHistoricalDraft(e.target.value)}><option value="">Version actuelle</option>{section.drafts.slice(0, -1).map((d, i) => <option key={d.id} value={d.id}>Version {i + 1}</option>)}</select>
      {historicalDraft && <div className="mt-3 border-t pt-3"><Markdown text={section.drafts.find(d => d.id === historicalDraft)?.text || ""} /></div>}
    </details>}
    {evidenceIds.length > 0 && <details className="mt-5 text-sm"><summary className="cursor-pointer font-medium">Preuves et localisation des sources</summary><div className="mt-3 space-y-4">{evidenceIds.map(eid => {
      const evidence = data.evidence[eid]; if (!evidence) return null
      const article = data.articles.find(a => a.id === evidence.article_id)
      const source = evidence.source_url.includes("cookies_not_supported") && article?.doi ? `https://doi.org/${article.doi}` : evidence.source_url
      return <blockquote key={eid} className="border-l-2 pl-4"><p className="whitespace-pre-wrap leading-6">{evidence.exact_quote}</p><p className="mt-2 text-xs text-muted-foreground">{evidence.source_version || "Version non vérifiée"} · {evidence.section || "Section non identifiée"}{evidence.page_number && ` · page PDF ${evidence.page_number}`}</p>{safeUrl(source) && <a href={safeUrl(source)} target="_blank" rel="noopener noreferrer" className="mt-1 block break-all text-primary underline">Consulter la source</a>}</blockquote>
    })}</div></details>}
  </article>
}

export default function ScholarV4Workspace({ sid, updatedAt, running, ask, onChanged }: {
  sid: string; updatedAt: string; running: boolean; ask: (text: string) => void; onChanged: () => Promise<void>
}) {
  const [data, setData] = useState<ScientificDocument | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const [version, setVersion] = useState("")
  useEffect(() => {
    const controller = new AbortController()
    getDocument(sid, controller.signal).then(document => { setData(document); setVersion(document.document.version_id || ""); setError("") })
      .catch(e => { if (!controller.signal.aborted) setError(e.message) })
    return () => controller.abort()
  }, [sid, updatedAt])
  async function act(payload: Record<string, unknown>) {
    if (!data || busy || running) return
    setBusy(true); setError("")
    try {
      await changeDocument(sid, { ...payload, expected_version: data.outline.version || 0 })
      const document = await getDocument(sid)
      setData(document); setVersion(document.document.version_id || "")
      await onChanged()
    } catch (e) { setError(e instanceof Error ? e.message : "Modification indisponible.") }
    finally { setBusy(false) }
  }
  async function download(format: "docx" | "pdf") {
    setBusy(true); setError("")
    try {
      const blob = await exportDocument(sid, version, format)
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement("a")
      anchor.href = url; anchor.download = `EnnoScholar-V4-v${data?.versions.find(v => v.id === version)?.revision || ""}.${format}`; anchor.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (e) { setError(e instanceof Error ? e.message : "Export indisponible.") }
    finally { setBusy(false) }
  }
  if (!data) return <div>{error ? <p role="alert" className="text-destructive">{error}</p> : <p role="status">Chargement du manuscrit…</p>}</div>
  const saved = (data.outline.sections || []).map((section, index) => ({ section, number: index + 1 })).filter(row => row.section.drafts.length > 0)
  if (!saved.length) return null
  const disabled = busy || running
  const articleIds = [...new Set([
    ...saved.flatMap(({ section }) => section.drafts.at(-1)?.article_ids || []),
    ...data.figures.filter(figure => saved.some(({ section }) => section.id === figure.section_id))
      .flatMap(figure => figure.evidence_ids.map(id => data.evidence[id]?.article_id).filter((id): id is string => Boolean(id))),
  ])]
  return <section className="mx-auto max-w-3xl" aria-label="Manuscrit de l’état de l’art" aria-busy={busy}>
    <h2 className="mb-3 text-xl font-semibold">{data.outline.title || "État de l’art scientifique"}</h2>
    <nav aria-label="Sections rédigées" className="mb-5 flex flex-wrap gap-2">{saved.map(({ section, number }) => <a key={section.id} href={`#scholar-section-${section.id}`} className="rounded-full border px-3 py-1 text-xs hover:bg-muted">{number}. {section.title}</a>)}</nav>
    {error && <p role="alert" className="mb-3 text-destructive">{error}</p>}
    {saved.map(({ section, number }) => <ManuscriptSection key={section.id} section={section} number={number} data={data} sid={sid} disabled={disabled} act={act} ask={ask} />)}
    {articleIds.length > 0 && <section className="border-t py-6"><h3 className="mb-3 text-lg font-semibold">Références bibliographiques</h3><ol className="list-decimal space-y-2 pl-6 text-sm">{articleIds.map(id => {
      const article = data.articles.find(a => a.id === id)
      if (!article) return null
      const url = safeUrl(article.doi ? `https://doi.org/${article.doi}` : article.arxiv_id ? `https://arxiv.org/abs/${article.arxiv_id}` : article.url)
      return <li key={id}>{url ? <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline">{article.title}</a> : article.title}</li>
    })}</ol></section>}
    <div className="border-t py-5"><label className="block text-sm">Version enregistrée à exporter<select className={field} value={version} disabled={disabled} onChange={e => setVersion(e.target.value)}><option value="">Choisir une version</option>{data.versions.map(v => <option key={v.id} value={v.id}>Version {v.revision} — {v.approval ? "approuvée" : "brouillon"}</option>)}</select></label><div className="mt-3 flex flex-wrap gap-2"><Button variant="outline" disabled={disabled || !version} onClick={() => download("docx")}>Exporter en Word</Button><Button variant="outline" disabled={disabled || !version} onClick={() => download("pdf")}>Exporter en PDF</Button><Button variant="ghost" disabled={disabled || data.outline.status !== "approved" || saved.length !== (data.outline.sections?.length || 0) || Boolean(data.document.approval)} onClick={() => act({ action: "approve_document" })}>Valider le document</Button></div></div>
  </section>
}

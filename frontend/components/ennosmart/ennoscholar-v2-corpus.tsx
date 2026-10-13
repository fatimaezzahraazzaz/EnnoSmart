"use client"

import { useRef, useState } from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { getScholarV2Articles } from "@/lib/api"
import type { ScholarV2Article } from "@/lib/scholar-v2"

const ACCESS = { metadata_only: "Métadonnées uniquement", abstract_only: "Abstract disponible", fulltext: "Texte intégral récupéré · passages sélectionnés" }
const PAGE_SIZE = 20

export default function ScholarV2Corpus({ sessionId, total, preview }: { sessionId: string; total: number; preview: ScholarV2Article[] }) {
  const [page, setPage] = useState({ articles: preview, offset: 0, total, loaded: false })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const request = useRef(0)

  async function load(offset: number) {
    const id = ++request.current
    setBusy(true)
    setError("")
    try {
      const result = await getScholarV2Articles(sessionId, offset, PAGE_SIZE)
      if (id === request.current) setPage({ articles: result.articles, offset: result.offset, total: result.total, loaded: true })
    } catch (failure) {
      if (id === request.current) setError(failure instanceof Error ? failure.message : "Chargement impossible. Réessayez.")
    } finally {
      if (id === request.current) setBusy(false)
    }
  }

  return <details className="mb-5 rounded-xl border border-border bg-card" onToggle={(event) => {
    if (event.currentTarget.open && !page.loaded && !busy) void load(0)
  }}>
    <summary className="min-h-11 cursor-pointer rounded-xl px-4 py-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
      Articles conservés dans la conversation ({page.total})
    </summary>
    <div className="space-y-4 border-t border-border p-4" aria-busy={busy}>
      <p className="text-sm text-muted-foreground">Corpus conservé séparément de la sélection présentée dans la réponse.</p>
      {busy && <p role="status" className="flex items-center gap-2 text-sm"><Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" />Chargement des articles…</p>}
      {error && <div role="alert" className="space-y-2"><p className="text-sm text-destructive">{error}</p><Button variant="outline" onClick={() => void load(page.offset)} className="min-h-11">Réessayer</Button></div>}
      <ol start={page.offset + 1} className="list-decimal space-y-4 pl-5">
        {page.articles.map((article) => {
          const url = article.doi ? `https://doi.org/${article.doi}` : article.url
          return <li key={article.id} className="min-w-0 space-y-1 pl-1 text-sm [overflow-wrap:anywhere]">
            {/^https?:\/\//i.test(url) ? <a href={url} target="_blank" rel="noopener noreferrer" className="font-medium text-brand underline underline-offset-4 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">{article.title}</a> : <p className="font-medium">{article.title}</p>}
            <p className="text-muted-foreground">{[article.year, article.authors.slice(0, 3).join(", "), article.sources.join(" · ") || article.source].filter(Boolean).join(" — ")}</p>
            <p>{ACCESS[article.access_level]}{article.relevance_tag && ` · ${article.relevance_tag}`}</p>
            {article.doi && <p className="text-muted-foreground">DOI : {article.doi}</p>}
            {article.abstract && <details><summary className="min-h-11 cursor-pointer py-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">Afficher l’abstract{!page.loaded && " (extrait)"}</summary><p className="whitespace-pre-wrap leading-6">{article.abstract}</p></details>}
          </li>
        })}
      </ol>
      <nav aria-label="Pagination des articles conservés" className="flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
        <Button variant="outline" className="min-h-11" disabled={busy || page.offset === 0} onClick={() => void load(Math.max(0, page.offset - PAGE_SIZE))}>Précédents</Button>
        <p className="text-sm tabular-nums" aria-live="polite" aria-atomic="true">Articles {page.offset + 1} à {page.offset + page.articles.length} sur {page.total}</p>
        <Button variant="outline" className="min-h-11" disabled={busy || (page.loaded && page.offset + PAGE_SIZE >= page.total)} onClick={() => void load(page.loaded ? page.offset + PAGE_SIZE : 0)}>Suivants</Button>
      </nav>
    </div>
  </details>
}

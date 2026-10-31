// V5 UI snapshot reusing the V4 interface; V4 source remains untouched.
"use client"

import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog"
import { getDiagnosticProject, getProjectContext, updateProjectContext, type ContextPage, type DiagnosticContext, type Session } from "@/lib/scholar-v5"
import { LockChoices } from "./scholar-v4-start-dialog"

const labels: Record<string, string> = { main_objective: "Objectif principal", objective_points: "Sous-objectifs", technical_context: "Contexte technique", project_summary: "Synthèse du projet", methods: "Méthodes", demarches: "Démarches R&D", parameters: "Paramètres", metrics: "Métriques", results: "Résultats", quote: "Passage source", document_name: "Document", page: "Page", statement: "Formulation", source_role: "Rôle de la source", temporal_status: "Période / réalisation", evidence_ids: "Références aux preuves", justification: "Justification", description: "Description", difficulties: "Difficultés", needs_review: "À vérifier", quote_verified: "Citation vérifiée par le diagnostic" }
function SourceValue({ value }: { value: unknown }) {
  if (value == null || value === "") return null
  if (Array.isArray(value)) return <ul className="space-y-2 border-l pl-3">{value.map((v, i) => <li key={i}><SourceValue value={v} /></li>)}</ul>
  if (typeof value === "object") return <dl className="space-y-2">{Object.entries(value).map(([k, v]) => <div key={k}><dt className="text-xs font-medium text-muted-foreground">{labels[k] || k.replaceAll("_", " ")}</dt><dd className="mt-1 whitespace-pre-wrap break-words"><SourceValue value={v} /></dd></div>)}</dl>
  return <>{typeof value === "boolean" ? value ? "Oui" : "Non" : String(value)}</>
}

function ContextRecord({ item }: { item: Record<string, unknown> }) {
  const heading = item.title || item.statement || item.quote
  if (!heading) return <SourceValue value={item} />
  return <><p className="whitespace-pre-wrap font-medium leading-6">{String(heading)}</p>
    {item.justification ? <p className="mt-2 whitespace-pre-wrap text-muted-foreground">{String(item.justification)}</p> : null}
    {item.document_name ? <p className="mt-2 text-xs text-muted-foreground">{String(item.document_name)}{item.page != null ? ` · Page ${item.page}` : ""}</p> : null}
    <details className="mt-3"><summary className="cursor-pointer text-sm text-primary">Provenance et détails conservés</summary><div className="mt-3 border-t pt-3"><SourceValue value={item} /></div></details></>
}

export default function ScholarV4ProjectContext({ session, disabled, onChanged }: { session: Session; disabled: boolean; onChanged: (session: Session) => void }) {
  const context = session.diagnostic_context!
  const [open, setOpen] = useState(false)
  const [view, setView] = useState<"context" | "scope" | "selection" | "refresh">("context")
  const [kind, setKind] = useState("facts")
  const [allContext, setAllContext] = useState(false)
  const [page, setPage] = useState<ContextPage | null>(null)
  const [preview, setPreview] = useState<DiagnosticContext | null>(null)
  const [selected, setSelected] = useState<string[]>(context.active_lock_ids)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => { setOpen(false); setPage(null); setPreview(null) }, [session.id])
  async function load(nextKind = kind, offset = 0, includeAll = allContext) {
    setBusy(true); setError("")
    try { setPage(await getProjectContext(session.id, nextKind, offset, includeAll ? "all" : "active")); setKind(nextKind); setAllContext(includeAll) }
    catch (e) { setError(e instanceof Error ? e.message : "Contexte indisponible.") } finally { setBusy(false) }
  }
  async function inspect() { setView("context"); setOpen(true); await load("facts") }
  async function prepareRefresh() {
    setBusy(true); setError("")
    try { const fresh = await getDiagnosticProject(context.project.id); setPreview(fresh); setSelected(fresh.locks.filter(v => v.decision === "garde").map(v => v.id)); setView("refresh") }
    catch (e) { setError(e instanceof Error ? e.message : "Actualisation indisponible.") } finally { setBusy(false) }
  }
  async function save() {
    if (view === "context") return
    setBusy(true); setError("")
    try {
      const target = view === "refresh" ? preview! : context
      onChanged(await updateProjectContext(session.id, { action: view, expected_revision: context.revision,
        lock_ids: selected, source_version: target.source_version, confirmed: true,
        allow_unkept: !target.locks.some(v => v.decision === "garde") }))
      setOpen(false)
    } catch (e) { setError(e instanceof Error ? e.message : "Modification impossible.") } finally { setBusy(false) }
  }
  return <>
    <section aria-label="Projet et périmètre scientifique actif" className="shrink-0 border-b bg-muted/25 px-4 py-2 text-sm"><div className="mx-auto flex max-w-5xl flex-wrap items-center gap-2"><div className="min-w-0 flex-1"><p className="font-medium">{context.project.name} <span className="font-normal text-muted-foreground">· {context.project.organisme} · CIR {context.project.year}</span></p><p className="mt-1 text-xs text-muted-foreground">Verrous actifs : {context.locks.filter(v => context.active_lock_ids.includes(v.id)).map(v => v.title).join(" ; ")}</p></div><Button variant="ghost" size="sm" onClick={inspect}>Contexte et preuves</Button><Button variant="outline" size="sm" disabled={disabled} onClick={() => { setView("scope"); setSelected(context.active_lock_ids); setError(""); setOpen(true) }}>Changer le périmètre</Button></div></section>
    <Dialog open={open} onOpenChange={value => { if (!busy) setOpen(value) }}><DialogContent className="sm:max-w-2xl"><DialogHeader><DialogTitle>{view === "context" ? "Contexte du projet" : view === "refresh" ? "Actualiser le diagnostic source" : "Choisir les verrous à étudier"}</DialogTitle><DialogDescription>{context.project.name} · Diagnostic du {new Date((preview && view === "refresh" ? preview : context).diagnostic_date).toLocaleDateString("fr-FR")}. Les recherches et versions déjà enregistrées sont conservées.</DialogDescription></DialogHeader>
      {view === "context" ? <><div className="flex flex-wrap gap-2">{[["facts", "Faits techniques"], ["locks", "Verrous actifs"], ["evidence", "Passages sources"], ["synthesis", "Contexte général"]].map(([key, label]) => <Button size="sm" variant={kind === key ? "secondary" : "ghost"} key={key} disabled={busy} onClick={() => load(key)}>{label}</Button>)}</div>
        <p className="text-xs text-muted-foreground">Les faits du projet et les résultats publiés constituent des sources distinctes. Les éléments généraux peuvent concerner d’autres verrous ; seuls les verrous actifs pilotent la recherche.</p>
        {page?.source_changed && <p className="rounded-lg border p-3">Le diagnostic a changé. Cette conversation conserve sa version source jusqu’à votre actualisation explicite.</p>}
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={allContext} disabled={busy} onChange={e => load(kind, 0, e.target.checked)} className="size-4 accent-primary" />Consulter aussi les éléments hors du périmètre actif</label>
        {page && !busy && <div className="space-y-4">{page.items.map((item, i) => <div key={i} className="rounded-xl border p-4"><ContextRecord item={item} /></div>)}{!page.items.length && <p>Aucun élément documenté dans cette catégorie.</p>}{page.next_offset != null && <Button variant="outline" onClick={() => load(kind, page.next_offset!)}>Page suivante</Button>}</div>}
        {page?.history?.length ? <details><summary className="cursor-pointer font-medium">Historique du périmètre et des sources</summary><div className="mt-3"><SourceValue value={page.history} /></div></details> : null}
        <Button variant="outline" disabled={disabled || busy} onClick={prepareRefresh}>Vérifier et choisir une version actualisée</Button></> : <>
        {view === "refresh" && <p className="rounded-lg bg-muted p-3">Confirmez la nouvelle source et ses verrous. L’ancien snapshot restera conservé dans l’historique ; les arguments existants seront signalés comme à revoir.</p>}
        <LockChoices context={view === "refresh" && preview ? preview : view === "scope" ? { ...context, locks: context.locks.filter(v => context.selected_lock_ids.includes(v.id)) } : context} selected={selected} onChange={setSelected} />
        {view === "scope" && <Button variant="ghost" onClick={() => { setView("selection"); setSelected(context.selected_lock_ids) }}>Modifier aussi la sélection de départ</Button>}
      </>}
      {busy && <p role="status">Chargement…</p>}{error && <p role="alert" className="text-destructive">{error}</p>}
      {view !== "context" && <DialogFooter><Button variant="ghost" disabled={busy} onClick={inspect}>Retour au contexte</Button><Button disabled={disabled || busy || !selected.length} onClick={save}>{view === "refresh" ? "Confirmer l’actualisation" : "Appliquer le périmètre"}</Button></DialogFooter>}
    </DialogContent></Dialog>
  </>
}

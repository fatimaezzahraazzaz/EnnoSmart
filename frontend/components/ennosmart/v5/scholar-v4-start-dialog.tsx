// V5 UI snapshot reusing the V4 interface; V4 source remains untouched.
"use client"

import { useEffect, useState } from "react"
import { BookOpen, FolderOpen, ArrowLeft, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription, DialogFooter } from "@/components/ui/dialog"
import { createSession, listDiagnosticProjects, getDiagnosticProject, type DiagnosticProject, type DiagnosticContext, type Session } from "@/lib/scholar-v5"

export function LockChoices({ context, selected, onChange }: { context: DiagnosticContext; selected: string[]; onChange: (ids: string[]) => void }) {
  const kept = context.locks.filter(v => v.decision === "garde")
  const available = kept.length ? kept : context.locks
  return <fieldset className="space-y-3"><legend className="mb-2 font-medium">Verrous à étudier</legend>
    {!kept.length && <p className="rounded-lg bg-muted p-3 text-sm">Aucun verrou n’a été marqué « Garder ». Votre sélection reste à valider par le consultant.</p>}
    <Button type="button" variant="ghost" size="sm" onClick={() => onChange(selected.length === available.length ? [] : available.map(v => v.id))}>{selected.length === available.length ? "Tout désélectionner" : "Tout sélectionner"}</Button>
    {available.map((lock, i) => <label key={lock.id} className="flex cursor-pointer items-start gap-3 rounded-xl border p-3 hover:bg-muted/50 focus-within:ring-2 focus-within:ring-primary">
      <input type="checkbox" className="mt-1 size-4 shrink-0 accent-primary" checked={selected.includes(lock.id)} onChange={e => onChange(e.target.checked ? [...selected, lock.id] : selected.filter(id => id !== lock.id))} />
      <span className="min-w-0"><span className="block font-medium">{i + 1}. {lock.title}</span><span className="mt-1 block text-xs text-muted-foreground">{lock.decision === "garde" ? "Garder — décision du consultant" : `Décision : ${lock.decision} — non validé`}</span>{lock.justification && <span className="mt-2 block whitespace-pre-wrap text-sm text-muted-foreground">{lock.justification}</span>}</span>
    </label>)}
  </fieldset>
}

export default function ScholarV4StartDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (value: boolean) => void; onCreated: (session: Session) => Promise<void> }) {
  const [step, setStep] = useState<"mode" | "projects" | "locks" | "confirm">("mode")
  const [projects, setProjects] = useState<DiagnosticProject[]>([])
  const [context, setContext] = useState<DiagnosticContext | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  useEffect(() => { if (open) { setStep("mode"); setContext(null); setSelected([]); setError("") } }, [open])
  async function loadProjects() {
    setStep("projects"); setBusy(true); setError("")
    try { setProjects(await listDiagnosticProjects()) } catch (e) { setError(e instanceof Error ? e.message : "Projets indisponibles.") } finally { setBusy(false) }
  }
  async function chooseProject(project: DiagnosticProject) {
    setBusy(true); setError("")
    try { const data = await getDiagnosticProject(project.project.id); setContext(data); setSelected(data.locks.filter(v => v.decision === "garde").map(v => v.id)); setStep("locks") }
    catch (e) { setError(e instanceof Error ? e.message : "Diagnostic indisponible.") } finally { setBusy(false) }
  }
  async function create(linked: boolean) {
    setBusy(true); setError("")
    try {
      const session = await createSession(linked && context ? { mode: "diagnostic_linked", title: context.project.name,
        project_id: context.project.id, diagnostic_id: context.diagnostic_id, source_version: context.source_version,
        selected_lock_ids: selected, confirmed: true, allow_unkept: !context.locks.some(v => v.decision === "garde") } : { mode: "express" })
      await onCreated(session); onOpenChange(false)
    } catch (e) { setError(e instanceof Error ? e.message : "Création impossible.") } finally { setBusy(false) }
  }
  return <Dialog open={open} onOpenChange={value => { if (!busy) onOpenChange(value) }}><DialogContent className="sm:max-w-2xl">
    <DialogHeader><DialogTitle>{step === "mode" ? "Comment souhaitez-vous commencer ?" : step === "projects" ? "Choisir un projet diagnostiqué" : step === "locks" ? context?.project.name : "Confirmer le périmètre scientifique"}</DialogTitle>
      <DialogDescription>{step === "mode" ? "Deux parcours, les mêmes outils scientifiques." : step === "projects" ? "Seuls vos projets accessibles avec un diagnostic exploitable sont proposés." : "Les résultats d’EnnoDiagnostic sont consultés en lecture seule."}</DialogDescription></DialogHeader>
    {step === "mode" && <div className="grid gap-3 sm:grid-cols-2"><button type="button" disabled={busy} onClick={loadProjects} className="rounded-xl border p-5 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"><FolderOpen aria-hidden="true" className="mb-4 size-6 text-primary" /><strong className="block">À partir d’un projet diagnostiqué</strong><span className="mt-2 block leading-6 text-muted-foreground">Utiliser les verrous, objectifs et travaux R&D déjà analysés dans EnnoDiagnostic.</span></button><button type="button" disabled={busy} onClick={() => create(false)} className="rounded-xl border p-5 text-left hover:bg-muted focus-visible:outline-2 focus-visible:outline-primary"><BookOpen aria-hidden="true" className="mb-4 size-6 text-primary" /><strong className="block">EnnoScholar Express</strong><span className="mt-2 block leading-6 text-muted-foreground">Explorer librement un sujet scientifique, des publications ou un verrou technique.</span></button></div>}
    {step === "projects" && !busy && <div className="space-y-3">{projects.map(p => <button type="button" key={p.project.id} onClick={() => chooseProject(p)} className="block w-full rounded-xl border p-4 text-left hover:bg-muted focus-visible:outline-primary"><strong>{p.project.name}</strong><span className="mt-1 block text-muted-foreground">{p.project.organisme} · CIR {p.project.year}{p.project.subproject_name ? ` · ${p.project.subproject_name}` : ""}</span><span className="mt-1 block text-xs text-muted-foreground">Diagnostic du {new Date(p.diagnostic_date).toLocaleDateString("fr-FR")} · {p.diagnostic_status}</span></button>)}{!projects.length && !error && <p className="py-4 text-muted-foreground">Aucun diagnostic exploitable accessible. Vous pouvez commencer avec EnnoScholar Express.</p>}<Button variant="outline" onClick={() => create(false)}>Continuer avec EnnoScholar Express</Button></div>}
    {step === "locks" && context && <LockChoices context={context} selected={selected} onChange={setSelected} />}
    {step === "confirm" && context && <div className="space-y-3"><p><strong>{context.project.name}</strong> · {context.project.organisme} · CIR {context.project.year}</p><ol className="list-decimal space-y-2 pl-5">{context.locks.filter(v => selected.includes(v.id)).map(v => <li key={v.id}>{v.title}{v.decision !== "garde" && " — non validé"}</li>)}</ol><p className="rounded-lg bg-muted p-3 text-muted-foreground">La conversation sera préparée avec ce contexte. La recherche démarrera uniquement à l’envoi de votre message. Vous pourrez ensuite vous concentrer sur un seul verrou.</p></div>}
    {busy && <p role="status" className="flex items-center gap-2"><Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />Chargement…</p>}
    {error && <p role="alert" className="text-destructive">{error}{step === "projects" && <Button variant="ghost" onClick={loadProjects}>Réessayer</Button>}</p>}
    {step !== "mode" && <DialogFooter><Button variant="ghost" disabled={busy} onClick={() => setStep(step === "confirm" ? "locks" : step === "locks" ? "projects" : "mode")}><ArrowLeft aria-hidden="true" className="size-4" />Retour</Button>{step === "locks" && <Button disabled={busy || !selected.length} onClick={() => setStep("confirm")}>Continuer avec {selected.length} verrou{selected.length > 1 ? "s" : ""}</Button>}{step === "confirm" && <Button disabled={busy} onClick={() => create(true)}>Confirmer et créer la conversation</Button>}</DialogFooter>}
  </DialogContent></Dialog>
}

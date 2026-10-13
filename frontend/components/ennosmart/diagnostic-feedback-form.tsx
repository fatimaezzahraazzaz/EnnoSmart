"use client"

import { useEffect, useState, type FormEvent } from "react"
import { CheckCircle2, ChevronDown, ImagePlus, Loader2, MessageSquarePlus, Send, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"
import { Label } from "@/components/ui/label"
import { sendDiagnosticFeedback, type ProjectRead } from "@/lib/api"

const sections = [
  { group: "Scores et synthèse", items: [
    ["eligibility", "Score d’éligibilité CIR"],
    ["defensibility", "Score de défendabilité"],
    ["synthesis", "Synthèse"],
  ] },
  { group: "Sections du diagnostic", items: [
    ["objective", "Objectifs"], ["lock", "Verrous"], ["method", "Méthodes"],
    ["demarche", "Démarches"], ["result", "Résultats"], ["metric", "Métriques"],
    ["parameter", "Paramètres"],
  ] },
  { group: "Autres rubriques", items: [
    ["ai_detection", "Détection IA"], ["previous_cir", "CIR précédent"],
    ["comparison", "Comparaison des documents"], ["document_chat", "Chat documentaire"],
    ["other", "Autre"],
  ] },
]
type Screenshot = { section: string; file: File }

function ScreenshotPreview({ file }: { file: File }) {
  const [url, setUrl] = useState("")
  useEffect(() => {
    const next = URL.createObjectURL(file)
    setUrl(next)
    return () => URL.revokeObjectURL(next)
  }, [file])
  return url ? <img src={url} alt={`Capture : ${file.name}`} className="h-12 w-16 shrink-0 rounded border object-contain" /> : null
}

export function DiagnosticFeedbackForm({ project }: { project: ProjectRead }) {
  const [open, setOpen] = useState(false)
  const [comments, setComments] = useState<Record<string, string>>({})
  const [screenshots, setScreenshots] = useState<Screenshot[]>([])
  const [sending, setSending] = useState(false)
  const [error, setError] = useState("")
  const [sent, setSent] = useState(false)
  const hasContent = Object.values(comments).some((value) => value.trim()) || screenshots.length > 0
  const filledCount = sections.flatMap((group) => group.items).filter(
    ([key]) => comments[key]?.trim() || screenshots.some((item) => item.section === key),
  ).length

  function addScreenshots(section: string, files: File[]) {
    setError("")
    if (files.some((file) => !["image/png", "image/jpeg", "image/webp"].includes(file.type))) {
      setError("Les captures doivent être des images PNG, JPEG ou WebP.")
      return
    }
    if (files.some((file) => file.size > 5 * 1024 * 1024)) {
      setError("Chaque capture est limitée à 5 Mo.")
      return
    }
    const next = [...screenshots, ...files.map((file) => ({ section, file }))]
    if (next.length > 15 || next.reduce((total, item) => total + item.file.size, 0) > 25 * 1024 * 1024) {
      setError("Vous pouvez joindre 15 captures maximum, pour un total de 25 Mo.")
      return
    }
    setScreenshots(next)
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (sending || !hasContent) return
    setSending(true)
    setError("")
    try {
      await sendDiagnosticFeedback(project.id, comments, screenshots)
      setSent(true)
      setComments({})
      setScreenshots([])
    } catch (err) {
      setError(err instanceof Error ? err.message : "Impossible d’envoyer votre retour.")
    } finally {
      setSending(false)
    }
  }

  return (
    <>
      <Button className="min-h-11 shrink-0 gap-2 rounded-xl bg-brand px-4 text-white shadow-sm hover:bg-brand/90 focus-visible:ring-brand/30" onClick={() => { setSent(false); setOpen(true) }}>
        <MessageSquarePlus className="size-4" aria-hidden="true" /> Faire un retour
      </Button>
      <Dialog open={open} onOpenChange={(next) => { if (!sending) setOpen(next) }}>
        <DialogContent className="flex max-h-[90dvh] flex-col overflow-hidden sm:max-w-3xl">
          <DialogHeader className="shrink-0 pr-7">
            <DialogTitle>Votre retour sur EnnoDiagnostic</DialogTitle>
            <DialogDescription>
              {project.organisme} · {project.project_name} · {project.year}
            </DialogDescription>
          </DialogHeader>
          {sent ? (
            <div className="space-y-4 py-8 text-center" role="status">
              <CheckCircle2 className="mx-auto size-10 text-success" aria-hidden="true" />
              <p className="font-medium">Votre retour a été envoyé. Merci.</p>
              <p className="text-sm text-muted-foreground">Destinataire : fatima-ezzahra.azzaz@ennodev.com</p>
              <Button onClick={() => setOpen(false)}>Fermer</Button>
            </div>
          ) : (
            <form onSubmit={submit} className="flex min-h-0 flex-col gap-4">
              <p className="text-sm leading-6 text-muted-foreground">
                Ouvrez les rubriques concernées et décrivez ce qui doit être corrigé.
                Tous les champs sont facultatifs : un commentaire ou une capture suffit.
              </p>
              <div className="min-h-0 space-y-5 overflow-y-auto overscroll-contain pr-2">
                {sections.map((group) => (
                  <section key={group.group} aria-label={group.group}>
                    <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.group}</h3>
                    <div className="divide-y rounded-xl border border-border">
                      {group.items.map(([key, label]) => {
                        const attachments = screenshots.filter((item) => item.section === key)
                        const filled = Boolean(comments[key]?.trim() || attachments.length)
                        return (
                          <details key={key} className="group">
                            <summary className="flex min-h-12 cursor-pointer list-none items-center gap-3 rounded-lg px-4 py-3 text-sm font-medium focus-visible:outline-2 focus-visible:outline-brand [&::-webkit-details-marker]:hidden">
                              <span className="flex-1">{label}</span>
                              {filled && <span className="rounded-full bg-brand/10 px-2 py-1 text-xs text-brand">Renseigné</span>}
                              <ChevronDown className="size-4 shrink-0 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
                            </summary>
                            <div className="space-y-3 px-4 pb-4">
                              <Label htmlFor={`feedback-${key}`}>Votre remarque — facultatif</Label>
                              <Textarea
                                id={`feedback-${key}`} value={comments[key] || ""} maxLength={4000} rows={3}
                                disabled={sending}
                                placeholder="Que constatez-vous ? Quel résultat attendiez-vous ?"
                                onChange={(event) => setComments((previous) => ({ ...previous, [key]: event.target.value }))}
                                onPaste={(event) => {
                                  const files = Array.from(event.clipboardData.files)
                                  if (files.length) { event.preventDefault(); addScreenshots(key, files) }
                                }}
                              />
                              <div className="flex flex-wrap items-center gap-3">
                                <label className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-md border px-3 text-sm hover:bg-muted focus-within:ring-2 focus-within:ring-brand">
                                  <ImagePlus className="size-4" aria-hidden="true" /> Ajouter une capture
                                  <input
                                    type="file" accept="image/png,image/jpeg,image/webp" multiple disabled={sending}
                                    aria-label={`Ajouter une capture pour ${label}`} className="sr-only"
                                    onChange={(event) => { addScreenshots(key, Array.from(event.target.files || [])); event.target.value = "" }}
                                  />
                                </label>
                                <span className="text-xs text-muted-foreground">PNG, JPEG ou WebP · 5 Mo par image. Vous pouvez aussi coller une capture.</span>
                              </div>
                              {attachments.map((item, index) => (
                                <div key={`${item.file.name}-${index}`} className="flex items-center gap-3 rounded-lg bg-muted/50 p-2">
                                  <ScreenshotPreview file={item.file} />
                                  <span className="min-w-0 flex-1 break-all text-xs">{item.file.name}</span>
                                  <Button type="button" variant="ghost" size="icon" disabled={sending}
                                    aria-label={`Retirer ${item.file.name}`}
                                    onClick={() => setScreenshots((previous) => previous.filter((entry) => entry !== item))}>
                                    <X className="size-4" aria-hidden="true" />
                                  </Button>
                                </div>
                              ))}
                            </div>
                          </details>
                        )
                      })}
                    </div>
                  </section>
                ))}
              </div>
              <div className="shrink-0 space-y-3 border-t pt-3">
                {error && <p className="text-sm text-destructive" role="alert">{error}</p>}
                <p className="text-xs text-muted-foreground">Envoyé à fatima-ezzahra.azzaz@ennodev.com avec votre nom et le projet sélectionné.</p>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <span className="text-xs text-muted-foreground" role="status">{filledCount} rubrique(s) renseignée(s) · {screenshots.length} capture(s)</span>
                  <div className="flex gap-2">
                    <Button type="button" variant="outline" disabled={sending} onClick={() => setOpen(false)}>Fermer</Button>
                    <Button type="submit" disabled={sending || !hasContent} aria-busy={sending}>
                      {sending ? <Loader2 className="size-4 animate-spin" aria-hidden="true" /> : <Send className="size-4" aria-hidden="true" />}
                      {sending ? "Envoi en cours…" : "Envoyer mon retour"}
                    </Button>
                  </div>
                </div>
              </div>
            </form>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

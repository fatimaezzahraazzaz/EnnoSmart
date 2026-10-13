"use client"

import type { AppPage } from "@/components/ennosmart/app-shell"
import { documents, evidences } from "@/lib/mock-data"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { FileSearch, Highlighter } from "lucide-react"

type Props = { navigateTo?: (page: AppPage) => void }

export default function DocumentsPage({ navigateTo }: Props) {
  return (
    <div className="p-6 space-y-6 animate-fadeIn">
      <div><p className="text-sm font-medium text-brand">Traçabilité</p><h1 className="text-3xl font-bold tracking-tight">Documents & sources</h1><p className="text-muted-foreground">Visualisation des documents, passages extraits et rôles NLP.</p></div>
      <div className="grid gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <Card><CardHeader><CardTitle>Documents du dossier</CardTitle><CardDescription>{documents.length} documents mockés</CardDescription></CardHeader><CardContent className="space-y-3">{documents.map((d) => <button key={d.id} className="w-full rounded-xl border p-3 text-left hover:bg-accent transition-all"><div className="flex items-center justify-between gap-2"><p className="font-medium text-sm">{d.name}</p><Badge variant="outline">{d.status}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{d.type} · {d.passages} passages · OCR {d.quality}</p></button>)}</CardContent></Card>
        <Card><CardHeader><CardTitle className="flex items-center gap-2"><FileSearch className="size-5 text-brand" />Aperçu source</CardTitle><CardDescription>Exemple de passage surligné et relié au diagnostic.</CardDescription></CardHeader><CardContent className="space-y-4"><div className="rounded-xl border bg-muted/30 p-5 leading-7 text-sm"><span className="rounded bg-emerald-100 px-1 text-emerald-900">Vibration extrêmement forte.</span> Le problème vient du fait que <span className="rounded bg-amber-100 px-1 text-amber-900">la poulie est très déséquilibrée</span>. Ce passage est relié au verrou <b>Comportement instable ou non maîtrisé</b>.</div><div className="grid gap-3 md:grid-cols-2">{evidences.slice(0,4).map((e) => <div key={e.id} className="rounded-xl border p-3"><div className="flex items-center gap-2"><Highlighter className="size-4 text-brand" /><Badge variant="secondary">{e.role}</Badge></div><p className="mt-2 text-sm text-muted-foreground line-clamp-3">{e.text}</p><p className="mt-2 text-xs text-muted-foreground">{e.document} · {e.score}%</p></div>)}</div><Button variant="outline" onClick={() => navigateTo?.("diagnosis")}>Retour EnnoDiagnostic</Button></CardContent></Card>
      </div>
    </div>
  )
}

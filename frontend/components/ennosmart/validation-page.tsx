"use client"

import type { AppPage } from "@/components/ennosmart/app-shell"
import { articles, validationChecklist, verrous } from "@/lib/mock-data"
import { ArticleTagBadge, CirTagBadge } from "@/lib/status-utils"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { CheckCircle2, Circle, Download, ShieldCheck, XCircle } from "lucide-react"

type Props = { navigateTo?: (page: AppPage) => void }

export default function ValidationPage({ navigateTo }: Props) {
  return (
    <div className="p-6 space-y-6 animate-fadeIn">
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between"><div><p className="text-sm font-medium text-brand">Human-in-the-loop</p><h1 className="text-3xl font-bold tracking-tight">Validation consultant</h1><p className="text-muted-foreground">Page centrale pour valider verrous, articles, état de l’art et risques.</p></div><Button><Download className="size-4" />Exporter rapport de validation</Button></div>
      <div className="grid gap-6 lg:grid-cols-[0.8fr_1.2fr]">
        <Card><CardHeader><CardTitle className="flex items-center gap-2"><ShieldCheck className="size-5 text-brand" />Checklist</CardTitle><CardDescription>Le dossier n’est prêt que lorsque toutes les étapes importantes sont validées.</CardDescription></CardHeader><CardContent className="space-y-3">{validationChecklist.map((c) => <div key={c.id} className="flex items-center gap-3 rounded-xl border p-3">{c.done ? <CheckCircle2 className="size-5 text-emerald-600" /> : <Circle className="size-5 text-muted-foreground" />}<span className={c.done ? "font-medium" : "text-muted-foreground"}>{c.label}</span></div>)}<div className="pt-3 flex gap-2"><Button className="flex-1">Valider</Button><Button variant="outline" className="flex-1">Demander correction</Button></div></CardContent></Card>
        <div className="space-y-6"><Card><CardHeader><CardTitle>Verrous à valider</CardTitle></CardHeader><CardContent className="space-y-3">{verrous.map((v) => <div key={v.id} className="rounded-xl border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium">{v.title}</p><CirTagBadge tag={v.tag} /></div><p className="mt-2 text-sm text-muted-foreground">{v.justification}</p><div className="mt-3 flex gap-2"><Button variant="outline" size="sm">Valider</Button><Button variant="destructive" size="sm"><XCircle className="size-4" />Rejeter</Button></div></div>)}</CardContent></Card><Card><CardHeader><CardTitle>Articles scientifiques</CardTitle><CardDescription>Seuls les articles sélectionnés seront utilisés dans l’état de l’art.</CardDescription></CardHeader><CardContent className="space-y-3">{articles.slice(0,5).map((a) => <div key={a.id} className="rounded-xl border p-3"><div className="flex flex-wrap items-center justify-between gap-2"><p className="font-medium text-sm">{a.title}</p><ArticleTagBadge tag={a.tag} /></div><p className="mt-1 text-xs text-muted-foreground">{a.source} · {a.year} · {a.citations} citations</p></div>)}</CardContent></Card></div>
      </div>
    </div>
  )
}

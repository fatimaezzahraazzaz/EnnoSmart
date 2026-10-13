"use client"

import { useId, useRef, useState } from "react"
import { CirclePlay } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"

export function DiagnosticVideoGuide() {
  const [open, setOpen] = useState(false)
  const [playbackRate, setPlaybackRate] = useState(1)
  const videoRef = useRef<HTMLVideoElement>(null)
  const speedId = useId()

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger render={
        <Button variant="outline" className="min-h-11 shrink-0 gap-2 rounded-xl border-brand/30 bg-brand/10 px-3 text-brand shadow-sm hover:bg-brand/15" />
      }>
        <CirclePlay className="size-5" aria-hidden="true" />
        Guide vidéo
      </DialogTrigger>
      <DialogContent className="sm:max-w-4xl">
        <DialogHeader className="pr-8">
          <DialogTitle>Guide vidéo EnnoDiagnostic</DialogTitle>
          <DialogDescription>Découvrez comment utiliser EnnoDiagnostic et examiner votre diagnostic.</DialogDescription>
        </DialogHeader>
        <div className="flex flex-wrap items-center justify-end gap-2">
          <label htmlFor={speedId} className="text-sm font-medium">Vitesse de lecture</label>
          <select
            id={speedId}
            value={playbackRate}
            className="min-h-11 rounded-lg border border-border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            onChange={(event) => {
              const rate = Number(event.target.value)
              setPlaybackRate(rate)
              if (videoRef.current) videoRef.current.playbackRate = rate
            }}
          >
            <option value={1}>1× · Normale</option>
            <option value={1.25}>1,25×</option>
            <option value={1.5}>1,5×</option>
            <option value={1.75}>1,75×</option>
            <option value={2}>2×</option>
          </select>
        </div>
        {open && (
          <video
            ref={videoRef}
            className="max-h-[70dvh] w-full rounded-lg bg-black"
            controls
            playsInline
            preload="metadata"
            aria-label="Guide vidéo d’utilisation d’EnnoDiagnostic"
            src="/guides/ennodiagnostic.mp4"
            onLoadedMetadata={(event) => { event.currentTarget.playbackRate = playbackRate }}
          >
            Votre navigateur ne peut pas lire cette vidéo. <a href="/guides/ennodiagnostic.mp4">Ouvrir le guide vidéo</a>.
          </video>
        )}
      </DialogContent>
    </Dialog>
  )
}

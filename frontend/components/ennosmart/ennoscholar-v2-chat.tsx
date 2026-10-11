"use client"

import { useEffect, useRef, useState, useSyncExternalStore, type FormEvent } from "react"
import { BookOpen, Loader2, RotateCcw, Send, UserRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { cn } from "@/lib/utils"
import { sendScholarV2Message } from "@/lib/api"
import type { ScholarV2Article, ScholarV2Citation } from "@/lib/scholar-v2"
import ScholarV2Markdown from "./ennoscholar-v2-markdown"
import ScholarV2Corpus from "./ennoscholar-v2-corpus"

const WELCOME = "Bonjour, je suis EnnoScholar V2.\nDécrivez votre sujet, votre verrou scientifique, un article ou votre plan d'état de l'art, et je peux vous aider à rechercher et analyser la littérature scientifique."
type Message = { id: string; role: "user" | "assistant"; text: string; citations?: ScholarV2Citation[] }
type Conversation = { sessionId: string | null; messages: Message[]; busy: boolean; error: string; articlesTotal: number; articlePreview: ScholarV2Article[] }
const initialConversation: Conversation = { sessionId: null, messages: [{ id: "welcome", role: "assistant", text: WELCOME }], busy: false, error: "", articlesTotal: 0, articlePreview: [] }
// Display state in browser RAM only. The authoritative context and articles live in backend RAM.
const conversations = new Map<number, Conversation>()
const listeners = new Set<() => void>()
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
const update = (userId: number, state: Conversation) => { conversations.set(userId, state); listeners.forEach((listener) => listener()) }

export default function EnnoScholarV2Chat({ userId }: { userId: number }) {
  const conversation = useSyncExternalStore(subscribe, () => conversations.get(userId) || initialConversation, () => initialConversation)
  const [draft, setDraft] = useState("")
  const end = useRef<HTMLDivElement>(null)
  const input = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth", block: "nearest" })
  }, [conversation.messages, conversation.busy])

  async function submit(event?: FormEvent) {
    event?.preventDefault()
    const current = conversations.get(userId) || initialConversation
    const text = draft.trim()
    if (!text || current.busy) return
    const pending = { ...current, busy: true, error: "", messages: [...current.messages, { id: crypto.randomUUID(), role: "user" as const, text }] }
    update(userId, pending)
    setDraft("")
    try {
      const response = await sendScholarV2Message({ session_id: current.sessionId, message: text })
      update(userId, { ...pending, sessionId: response.session_id, articlesTotal: response.articles_total, articlePreview: response.articles, busy: false, messages: [...pending.messages,
        { id: crypto.randomUUID(), role: "assistant", text: response.answer, citations: response.citations }] })
      input.current?.focus()
    } catch (error) {
      update(userId, { ...current, busy: false, error: error instanceof Error ? error.message : "La recherche n'a pas pu être terminée." })
      setDraft(text)
    }
  }

  return (
    <section className="mx-auto flex min-h-[calc(100dvh-12rem)] w-full max-w-4xl flex-col px-3 py-4 sm:px-6" aria-label="Conversation EnnoScholar V2">
      <div className="mb-5 flex items-center justify-between gap-3 border-b border-border pb-4">
        <h2 className="text-lg font-semibold tracking-tight text-foreground">EnnoScholar V2</h2>
        <Button variant="ghost" className="min-h-11 gap-2" disabled={conversation.busy} onClick={() => { update(userId, initialConversation); setDraft(""); input.current?.focus() }}>
          <RotateCcw className="size-4" aria-hidden="true" /><span className="hidden sm:inline">Nouvelle conversation</span><span className="sr-only sm:hidden">Nouvelle conversation</span>
        </Button>
      </div>
      <div role="log" aria-label="Historique des messages" aria-live="polite" aria-relevant="additions" className="flex-1 space-y-6 pb-6">
        {conversation.messages.map((message) => <article key={message.id} className={cn("flex gap-3", message.role === "user" && "flex-row-reverse")}>
          <div className={cn("mt-1 grid size-8 shrink-0 place-items-center rounded-full", message.role === "user" ? "bg-muted text-muted-foreground" : "bg-brand/10 text-brand")} aria-hidden="true">
            {message.role === "user" ? <UserRound className="size-4" /> : <BookOpen className="size-4" />}
          </div>
          <div className={cn("min-w-0 max-w-[calc(100%-2.75rem)] rounded-2xl px-4 py-3 sm:max-w-[85%]", message.role === "user" ? "bg-muted text-foreground" : "bg-card text-foreground")}>
            <p className="sr-only">{message.role === "user" ? "Vous" : "EnnoScholar V2"}</p>
            {message.role === "user" ? <p className="whitespace-pre-wrap break-words text-sm leading-7 sm:text-base">{message.text}</p> : <ScholarV2Markdown text={message.text} citations={message.citations} />}
          </div>
        </article>)}
        {conversation.busy && <p role="status" className="flex items-center gap-2 pl-11 text-sm text-muted-foreground"><Loader2 className="size-4 motion-safe:animate-spin" aria-hidden="true" />Recherche en cours…</p>}
        <div ref={end} className="scroll-mb-52" />
      </div>
      {conversation.sessionId && conversation.articlesTotal > 0 && <ScholarV2Corpus
        key={`${conversation.sessionId}:${conversation.messages.at(-1)?.id}`} sessionId={conversation.sessionId}
        total={conversation.articlesTotal} preview={conversation.articlePreview} />}
      <form onSubmit={submit} className="sticky bottom-0 space-y-2 border-t border-border bg-background pb-2 pt-4">
        {conversation.error && <p role="alert" className="text-sm text-destructive">{conversation.error}</p>}
        <label htmlFor="scholar-v2-message" className="sr-only">Votre message à EnnoScholar V2</label>
        <div className="flex items-end gap-2 rounded-xl border border-border bg-card p-2 shadow-sm focus-within:ring-2 focus-within:ring-brand/30">
          <Textarea ref={input} id="scholar-v2-message" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="Écrivez votre message…" rows={3} maxLength={20000} disabled={conversation.busy}
            className="max-h-52 min-h-20 resize-y border-0 bg-transparent text-base shadow-none focus-visible:ring-0"
            onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void submit() } }} />
          <Button type="submit" disabled={conversation.busy || !draft.trim()} className="min-h-11 min-w-11 shrink-0" aria-label="Envoyer le message">
            <Send className="size-4" aria-hidden="true" /><span className="hidden sm:ml-2 sm:inline">Envoyer</span>
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">Entrée pour envoyer · Maj + Entrée pour une nouvelle ligne</p>
      </form>
    </section>
  )
}

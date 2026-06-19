"use client"

import { useState, useRef, useEffect } from "react"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import {
  BrainCircuit,
  Send,
  FileText,
  User,
  ChevronDown,
  ChevronUp,
  Sparkles,
  BookOpen,
  Copy,
  Check,
} from "lucide-react"

interface Source {
  doc: string
  page: string
  excerpt: string
}

interface Message {
  id: string
  role: "user" | "assistant"
  content: string
  sources?: Source[]
  streaming?: boolean
}

const initialMessages: Message[] = [
  {
    id: "1",
    role: "assistant",
    content:
      "Bonjour ! Je suis votre assistant CIR IA. Je peux analyser les documents de votre dossier et répondre à vos questions sur l'éligibilité, les critères CIR/CII ou les bonnes pratiques de documentation.\n\nQue puis-je faire pour vous ?",
    sources: [],
  },
]

const suggestedQuestions = [
  "Quels critères d'incertitude scientifique sont documentés dans le rapport technique ?",
  "Les ETP déclarés sont-ils cohérents avec les timesheets ?",
  "Quelles dépenses de sous-traitance sont éligibles au CIR ?",
  "Y a-t-il des points de risque pour un contrôle fiscal ?",
]

const mockResponses: Record<string, { content: string; sources: Source[] }> = {
  default: {
    content:
      "D'après l'analyse des documents fournis, voici ce que j'ai trouvé :\n\nLe rapport technique Q4 2024 mentionne explicitement trois verrous scientifiques liés à l'optimisation des algorithmes de compression. Ces éléments constituent une base solide pour justifier l'incertitude technique au sens de l'article 49 septies F de l'annexe III du CGI.\n\nCependant, les pages 14 à 16 présentent des travaux qui semblent relever davantage du développement standard que de la recherche appliquée. Je vous recommande de requalifier ou de préciser la nature exploratoire de ces activités.",
    sources: [
      { doc: "Rapport_Technique_Q4_2024.pdf", page: "Pages 4–9, 14–16", excerpt: "«…les travaux visent à surmonter des verrous scientifiques relatifs à la compression asymétrique temps-réel…»" },
      { doc: "Description_Projet_R&D.docx", page: "Page 2", excerpt: "«…l'état de l'art ne permet pas à ce jour de répondre aux exigences de performance cible…»" },
    ],
  },
}

export default function ChatPage() {
  const [messages, setMessages] = useState<Message[]>(initialMessages)
  const [input, setInput] = useState("")
  const [isTyping, setIsTyping] = useState(false)
  const [expandedSources, setExpandedSources] = useState<string[]>([])
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" })
  }, [messages])

  const sendMessage = (text?: string) => {
    const content = text ?? input.trim()
    if (!content || isTyping) return

    const userMsg: Message = {
      id: Date.now().toString(),
      role: "user",
      content,
    }

    const assistantId = (Date.now() + 1).toString()
    const assistantMsg: Message = {
      id: assistantId,
      role: "assistant",
      content: "",
      sources: [],
      streaming: true,
    }

    setMessages((prev) => [...prev, userMsg, assistantMsg])
    setInput("")
    setIsTyping(true)

    const response = mockResponses.default
    let index = 0
    const chars = response.content.split("")

    const interval = setInterval(() => {
      index += 3
      const partial = chars.slice(0, index).join("")
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId ? { ...m, content: partial } : m
        )
      )
      if (index >= chars.length) {
        clearInterval(interval)
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, content: response.content, sources: response.sources, streaming: false }
              : m
          )
        )
        setIsTyping(false)
      }
    }, 20)
  }

  const toggleSources = (id: string) => {
    setExpandedSources((prev) =>
      prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]
    )
  }

  const copyMessage = (id: string, content: string) => {
    navigator.clipboard.writeText(content)
    setCopiedId(id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  return (
    <div className="h-full flex flex-col lg:flex-row gap-0 max-h-[calc(100vh-3.5rem)]">
      {/* Chat area */}
      <div className="flex-1 flex flex-col min-w-0 min-h-0">
        {/* Chat header */}
        <div className="px-6 py-4 border-b border-border bg-card flex items-center gap-3 flex-shrink-0">
          <div className="size-8 rounded-lg bg-primary flex items-center justify-center">
            <BrainCircuit className="size-4 text-primary-foreground" />
          </div>
          <div>
            <p className="text-sm font-semibold text-foreground">Assistant RAG CIR</p>
            <p className="text-xs text-muted-foreground">TechInov SAS — 5 documents indexés</p>
          </div>
          <div className="ml-auto flex items-center gap-1.5">
            <div className="size-1.5 rounded-full bg-success" />
            <span className="text-xs text-muted-foreground">En ligne</span>
          </div>
        </div>

        {/* Messages */}
        <ScrollArea className="flex-1 px-4 py-4">
          <div className="space-y-5 max-w-3xl mx-auto pb-4">
            {messages.map((message) => (
              <div key={message.id} className={`flex gap-3 ${message.role === "user" ? "flex-row-reverse" : ""}`}>
                {/* Avatar */}
                <div className={`size-8 rounded-full flex items-center justify-center flex-shrink-0 ${
                  message.role === "assistant"
                    ? "bg-primary"
                    : "bg-brand/15 border border-brand/25"
                }`}>
                  {message.role === "assistant" ? (
                    <Sparkles className="size-4 text-primary-foreground" />
                  ) : (
                    <User className="size-4 text-brand" />
                  )}
                </div>

                {/* Bubble */}
                <div className={`flex-1 max-w-[85%] ${message.role === "user" ? "items-end" : "items-start"} flex flex-col gap-1`}>
                  <div className={`rounded-2xl px-4 py-3 ${
                    message.role === "user"
                      ? "bg-primary text-primary-foreground rounded-tr-sm"
                      : "bg-card border border-border rounded-tl-sm"
                  }`}>
                    <p className="text-sm leading-relaxed whitespace-pre-line">{message.content}</p>
                    {message.streaming && (
                      <span className="inline-block size-1.5 rounded-full bg-brand animate-pulse ml-1" />
                    )}
                  </div>

                  {/* Sources */}
                  {message.role === "assistant" && message.sources && message.sources.length > 0 && !message.streaming && (
                    <div className="w-full space-y-1">
                      <button
                        onClick={() => toggleSources(message.id)}
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                      >
                        <BookOpen className="size-3.5" />
                        {message.sources.length} source{message.sources.length > 1 ? "s" : ""} utilisée{message.sources.length > 1 ? "s" : ""}
                        {expandedSources.includes(message.id) ? (
                          <ChevronUp className="size-3" />
                        ) : (
                          <ChevronDown className="size-3" />
                        )}
                      </button>

                      {expandedSources.includes(message.id) && (
                        <div className="space-y-2">
                          {message.sources.map((src, i) => (
                            <div key={i} className="rounded-lg border border-border bg-muted/40 p-3 space-y-1.5">
                              <div className="flex items-center gap-2">
                                <FileText className="size-3.5 text-muted-foreground" />
                                <p className="text-xs font-semibold text-foreground">{src.doc}</p>
                                <Badge variant="secondary" className="text-[10px] h-4 px-1.5 ml-auto">
                                  {src.page}
                                </Badge>
                              </div>
                              <p className="text-xs text-muted-foreground italic leading-relaxed border-l-2 border-brand/30 pl-2">
                                {src.excerpt}
                              </p>
                            </div>
                          ))}
                        </div>
                      )}

                      <button
                        onClick={() => copyMessage(message.id, message.content)}
                        className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {copiedId === message.id ? (
                          <><Check className="size-3.5 text-success" /> Copié</>
                        ) : (
                          <><Copy className="size-3.5" /> Copier la réponse</>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              </div>
            ))}
            <div ref={bottomRef} />
          </div>
        </ScrollArea>

        {/* Input */}
        <div className="px-4 pb-4 pt-2 border-t border-border bg-card flex-shrink-0">
          <div className="max-w-3xl mx-auto space-y-2">
            {/* Suggestions */}
            {messages.length <= 1 && (
              <div className="flex flex-wrap gap-1.5 pb-1">
                {suggestedQuestions.map((q) => (
                  <button
                    key={q}
                    onClick={() => sendMessage(q)}
                    className="text-xs px-3 py-1.5 rounded-full border border-border bg-background hover:border-brand/40 hover:bg-brand/5 hover:text-brand transition-all text-muted-foreground text-left"
                  >
                    {q}
                  </button>
                ))}
              </div>
            )}

            <div className="flex gap-2 items-end">
              <Textarea
                ref={textareaRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Posez votre question sur le dossier CIR… (Entrée pour envoyer)"
                className="flex-1 min-h-10 max-h-32 resize-none text-sm"
                rows={1}
              />
              <Button
                onClick={() => sendMessage()}
                disabled={!input.trim() || isTyping}
                className="bg-primary hover:bg-primary/90 text-primary-foreground size-10 p-0 flex-shrink-0"
              >
                <Send className="size-4" />
                <span className="sr-only">Envoyer</span>
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground text-center">
              Les réponses sont générées à partir de vos documents. Vérifiez toujours les informations critiques.
            </p>
          </div>
        </div>
      </div>

      {/* Sources panel — desktop */}
      <aside className="hidden lg:flex w-72 flex-col border-l border-border bg-card flex-shrink-0">
        <div className="px-4 py-4 border-b border-border">
          <p className="text-sm font-semibold text-foreground">Documents indexés</p>
          <p className="text-xs text-muted-foreground mt-0.5">5 fichiers disponibles</p>
        </div>
        <ScrollArea className="flex-1">
          <div className="p-3 space-y-2">
            {[
              { name: "Rapport_Technique_Q4_2024.pdf", pages: "42 pages", type: "Rapport" },
              { name: "Timesheet_Equipe_RD.xlsx", pages: "8 onglets", type: "Timesheet" },
              { name: "Brevet_Methode_Compression.pdf", pages: "18 pages", type: "Brevet" },
              { name: "Factures_Prestataires_2024.pdf", pages: "12 pages", type: "Factures" },
              { name: "Description_Projet_R&D.docx", pages: "6 pages", type: "Description" },
            ].map((doc) => (
              <div key={doc.name} className="flex items-start gap-2.5 p-2.5 rounded-lg hover:bg-accent cursor-pointer transition-colors">
                <div className="size-8 rounded-md bg-muted flex items-center justify-center flex-shrink-0 mt-0.5">
                  <FileText className="size-3.5 text-muted-foreground" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium text-foreground truncate">{doc.name}</p>
                  <p className="text-[10px] text-muted-foreground">{doc.pages}</p>
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>

        <Separator />
        <div className="p-4">
          <Card className="border border-brand/20 bg-brand/5">
            <CardContent className="p-3 space-y-1">
              <div className="flex items-center gap-1.5">
                <Sparkles className="size-3.5 text-brand" />
                <p className="text-xs font-semibold text-brand">RAG activé</p>
              </div>
              <p className="text-[10px] text-muted-foreground leading-relaxed">
                Les réponses sont construites à partir de vos documents via une recherche sémantique.
              </p>
            </CardContent>
          </Card>
        </div>
      </aside>
    </div>
  )
}

"use client"

import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
import type { ScholarV2Citation } from "@/lib/scholar-v2"

function safeUrl(value: string) {
  try {
    const url = new URL(value)
    return ["http:", "https:"].includes(url.protocol) ? url.href : ""
  } catch {
    return ""
  }
}

export function withWebCitations(text: string, citations: ScholarV2Citation[]) {
  // Responses offsets refer to characters; Array.from keeps emoji/surrogate pairs intact.
  const characters = Array.from(text)
  const located = citations.filter((c) => c.cited && c.start_index !== null && c.end_index !== null && safeUrl(c.url))
    .sort((a, b) => a.start_index! - b.start_index!)
  let result = "", cursor = 0
  for (const [index, citation] of located.entries()) {
    const start = citation.start_index!, end = citation.end_index!
    if (start < cursor || start < 0 || end < start || end > characters.length) continue
    result += characters.slice(cursor, start).join("")
    const passage = characters.slice(start, end).join("")
    const label = /[\uE200-\uE202]/u.test(passage) || !passage.trim() ? String(index + 1) : passage.replace(/[\[\]]/g, "")
    result += `[${label}](<${safeUrl(citation.url).replace(/>/g, "%3E")}>)`
    cursor = end
  }
  return result + characters.slice(cursor).join("")
}

export default function ScholarV2Markdown({ text, citations = [] }: { text: string; citations?: ScholarV2Citation[] }) {
  const sources = citations.filter((c, index) => c.cited && safeUrl(c.url) && citations.findIndex((other) => other.cited && other.url === c.url) === index)
  return (
    <div className="min-w-0 space-y-3 break-words text-sm leading-7 sm:text-base">
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml urlTransform={safeUrl} components={{
        a: ({ children, href }) => <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand underline decoration-brand/40 underline-offset-4 hover:decoration-brand">{children}</a>,
        p: ({ children }) => <p className="mb-3 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="mb-3 list-disc space-y-1 pl-5">{children}</ul>,
        ol: ({ children }) => <ol className="mb-3 list-decimal space-y-1 pl-5">{children}</ol>,
        h1: ({ children }) => <h2 className="mb-3 mt-5 text-xl font-semibold">{children}</h2>,
        h2: ({ children }) => <h3 className="mb-2 mt-4 text-lg font-semibold">{children}</h3>,
        h3: ({ children }) => <h4 className="mb-2 mt-3 font-semibold">{children}</h4>,
        blockquote: ({ children }) => <blockquote className="my-3 border-l-2 border-brand/40 pl-4 text-muted-foreground">{children}</blockquote>,
        pre: ({ children }) => <pre className="my-3 overflow-x-auto rounded-lg bg-muted p-3 text-xs">{children}</pre>,
        table: ({ children }) => <div className="my-3 overflow-x-auto"><table className="w-full border-collapse text-sm">{children}</table></div>,
        th: ({ children }) => <th className="border border-border bg-muted px-3 py-2 text-left">{children}</th>,
        td: ({ children }) => <td className="border border-border px-3 py-2 align-top">{children}</td>,
        img: () => null,
      }}>{withWebCitations(text, citations)}</ReactMarkdown>
      {sources.length > 0 && <div className="flex flex-wrap gap-x-4 gap-y-1 border-t border-border/70 pt-3 text-xs">
        {sources.map((source, index) => <a key={source.url} href={safeUrl(source.url)} target="_blank" rel="noopener noreferrer" className="text-brand underline underline-offset-4">[{index + 1}] {source.title || new URL(source.url).hostname}</a>)}
      </div>}
    </div>
  )
}

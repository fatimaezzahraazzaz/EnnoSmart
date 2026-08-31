import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync, existsSync } from "node:fs"
import { createRequire } from "node:module"
import { fileURLToPath } from "node:url"
import { createElement } from "react"
import { renderToStaticMarkup } from "react-dom/server"
import ts from "typescript"
import {
  hydrateImprovementResearchSources,
  normalizeSourceDecision,
  researchSourceArticleId,
  researchSourcePdfLink,
  researchSourceReady,
  researchSourceSearchId,
  improvementResearchByMessage,
} from "./improvement-research-sources.ts"

const snapshot = { candidate_id: "candidate-a", title: "Article", consultant_decision: "pending", article_id: null }
const kept = { ...snapshot, article_id: 468, consultant_decision: "accepted", article_card_ready: false, fulltext_status: "fulltext_unavailable_after_mcp" }

test("retaining an unextracted article updates historical chat cards after article_id is assigned", () => {
  const [hydrated] = hydrateImprovementResearchSources([snapshot], [kept])
  assert.equal(normalizeSourceDecision(hydrated.consultant_decision), "accepted")
  assert.equal(researchSourceArticleId(hydrated), 468)
  assert.equal(researchSourceReady(hydrated), false)
  assert.equal(snapshot.article_id, null)
})

test("successful extraction and rejection are reflected without rewriting historical metadata", () => {
  const [ready] = hydrateImprovementResearchSources([snapshot], [{ ...kept, article_card_ready: true }])
  assert.equal(researchSourceReady(ready), true)
  const [rejected] = hydrateImprovementResearchSources([kept], [{ ...kept, consultant_decision: "rejected", article_card_ready: false }])
  assert.equal(normalizeSourceDecision(rejected.consultant_decision), "rejected")
  assert.equal(researchSourceReady(rejected), false)
})

test("candidate identity wins over shared article identity; unrelated sources stay unchanged", () => {
  const other = { candidate_id: "other", article_id: 468, consultant_decision: "rejected" }
  const unrelated = { candidate_id: "unrelated", title: snapshot.title }
  const rows = hydrateImprovementResearchSources([snapshot, unrelated], [kept, other])
  assert.equal(rows[0].consultant_decision, "accepted")
  assert.equal(rows[1], unrelated)
})

test("legacy cards can synchronize by article identity", () => {
  const [row] = hydrateImprovementResearchSources([{ article_id: 468 }], [kept])
  assert.equal(row.consultant_decision, "accepted")
  assert.equal(researchSourceArticleId({ fulltext_preparation: { article_id: 468 } }), 468)
  assert.equal(researchSourceArticleId({ article_id: "invalid" }), 0)
})

test("two searches keep the same candidate's decisions independent", () => {
  const first = { ...snapshot, guided_session_id: "first" }
  const second = { ...kept, guided_session_id: "second", consultant_decision: "rejected" }
  const [row] = hydrateImprovementResearchSources([first], [second])
  assert.equal(row.consultant_decision, "pending")
  assert.equal(row.guided_session_id, "first")
})

test("both message attachments and kept state survive another search and a reload", () => {
  const first = { ...kept, guided_session_id: "first" }
  const second = { ...snapshot, candidate_id: "candidate-b", guided_session_id: "second" }
  const messages = [
    { message_id: "answer1", role: "assistant", metadata: { scholar_handoff: { guided_session_id: "first", sources: [snapshot] } } },
    { message_id: "answer2", role: "assistant", metadata: { scholar_handoff: { guided_session_id: "second", sources: [second] } } },
    { message_id: "receipt", role: "assistant", metadata: {} },
  ]
  const context = JSON.parse(JSON.stringify({ research_sources: [first, second], scholar_handoff: { guided_session_id: "second", sources: [second] } }))
  const attachments = improvementResearchByMessage(messages, context)
  assert.equal(attachments.size, 2)
  assert.equal(attachments.get("answer1")[0].consultant_decision, "accepted")
  assert.equal(attachments.get("answer1")[0].guided_session_id, "first")
  assert.equal(attachments.get("answer2")[0].candidate_id, "candidate-b")
  assert.equal(attachments.has("receipt"), false)
})

test("legacy fallback uses the search response, never the last selection receipt", () => {
  const rows = improvementResearchByMessage([
    { message_id: "search", role: "assistant", intent: "awaiting_evidence", metadata: { routing: { needs_scholar: true } } },
    { message_id: "receipt", role: "assistant", intent: "research_sources_decided" },
  ], { scholar_handoff: { guided_session_id: "first", sources: [kept] } })
  assert.equal(rows.get("search")[0].guided_session_id, "first")
  assert.equal(rows.has("receipt"), false)
})

test("legacy upload origin is resolved by candidate membership, not by newest search", () => {
  assert.equal(researchSourceSearchId(snapshot, { research_history: [
    { guided_session_id: "first", sources: [snapshot] },
    { guided_session_id: "second", sources: [{ candidate_id: "another" }] },
  ] }), "first")
  assert.equal(researchSourceSearchId(snapshot, { research_history: [] }), "")
})

test("download chooses the PDF first, preserves query parameters and labels publication fallbacks", () => {
  const pdf = "https://publisher.test/article.pdf?version=2"
  assert.deepEqual(researchSourcePdfLink({ pdf_url: pdf, url: "https://publisher.test/article" }), { url: pdf, direct: true })
  assert.deepEqual(researchSourcePdfLink({ url: pdf }), { url: pdf, direct: true })
  assert.deepEqual(researchSourcePdfLink({ doi: "10.1000/example" }), { url: "https://doi.org/10.1000/example", direct: false })
  assert.deepEqual(researchSourcePdfLink({ url: "javascript:alert(1)" }), { url: "", direct: false })
})

// Render the actual TSX component and shared Button without a browser, API,
// upload or extraction. Alias resolution stays local to this test loader.
const require = createRequire(import.meta.url)
const root = new URL("../", import.meta.url)
const modules = new Map()
function loadLocal(path) {
  const filename = fileURLToPath(new URL(path, root))
  if (modules.has(filename)) return modules.get(filename).exports
  const module = { exports: {} }
  modules.set(filename, module)
  const compiled = ts.transpileModule(readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
    fileName: filename,
  }).outputText
  const localRequire = (id) => {
    if (!id.startsWith("@/")) return require(id)
    const relative = id.slice(2)
    const extension = [".ts", ".tsx"].find((ext) => existsSync(new URL(relative + ext, root)))
    return loadLocal(relative + extension)
  }
  new Function("require", "module", "exports", compiled)(localRequire, module, module.exports)
  return module.exports
}
const { ImprovementSourceActions } = loadLocal("components/ennosmart/improvement-source-actions.tsx")
const render = (source, busy = false) => renderToStaticMarkup(createElement(ImprovementSourceActions, {
  source, busy, onDecision: () => {}, onUploadPdf: async () => {},
}))

test("kept but unextracted card shows green kept state plus download and upload in the card", () => {
  const html = render({ ...kept, pdf_url: "https://publisher.test/article.pdf" })
  assert.match(html, /bg-emerald-700/)
  assert.match(html, /Gardé/)
  assert.match(html, /Extraction interrompue/)
  assert.match(html, /Télécharger le PDF/)
  assert.match(html, /Importer le PDF/)
  assert.match(html, /accept="application\/pdf,\.pdf"/)
  assert.match(html, /href="https:\/\/publisher.test\/article.pdf"/)
  assert.doesNotMatch(html, /anti.?bot|paywall|payant|Preuve prête/)
})

test("an extracted card stays kept without a false interrupted extraction alert", () => {
  const html = render({ ...kept, article_card_ready: true })
  assert.match(html, /Gardé/)
  assert.match(html, /Preuve prête pour la rédaction/)
  assert.doesNotMatch(html, /Extraction interrompue|Importer le PDF/)
})

test("undecided or rejected cards are not falsely shown as kept or extraction failures", () => {
  for (const source of [snapshot, { ...kept, consultant_decision: "rejected" }]) {
    const html = render(source)
    assert.match(html, /Garder/)
    assert.doesNotMatch(html, /Gardé|Extraction interrompue|Importer le PDF/)
  }
})

test("without a direct PDF the card gives the publication link instead of inventing a PDF URL", () => {
  const html = render({ ...kept, doi: "10.1000/example" })
  assert.match(html, /Télécharger depuis la publication/)
  assert.match(html, /href="https:\/\/doi.org\/10.1000\/example"/)
})

test("missing article identity disables upload and explains the prerequisite", () => {
  const html = render({ ...kept, article_id: null })
  assert.match(html, /L’import sera disponible/)
  assert.match(html, /<input[^>]*disabled=""/)
  assert.match(html, /lien indisponible/)
})

test("non-article sources are not incorrectly presented as PDF extraction failures", () => {
  const html = render({ ...kept, fulltext_status: "not_applicable_technical_or_context_source" })
  assert.match(html, /Gardé/)
  assert.doesNotMatch(html, /Extraction interrompue|Importer le PDF/)
})

function uploadHandler(dependencies) {
  const file = readFileSync(new URL("components/ennosmart/ennoamelioration-page.tsx", root), "utf8")
  const ast = ts.createSourceFile("page.tsx", file, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  let initializer
  function visit(node) {
    if (ts.isVariableDeclaration(node) && node.name.getText(ast) === "uploadSourcePdf") initializer = node.initializer
    ts.forEachChild(node, visit)
  }
  visit(ast)
  assert.ok(initializer)
  const compiled = ts.transpileModule(`const handler = ${initializer.getText(ast)}`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText
  return new Function(...Object.keys(dependencies), compiled + "\nreturn handler;")(...Object.values(dependencies))
}

test("PDF upload targets the same article/conversation and refreshes its persisted selection", async () => {
  const calls = []
  const current = { session_id: "improvement-session", context: { scholar_handoff: { guided_session_id: "newer-search" } } }
  const updated = { ...current, context: { research_sources: [{ ...kept, article_card_ready: true }] } }
  const file = { name: "article.pdf" }
  const handle = uploadHandler({
    projectId: 8, current, busy: false, backgroundActive: false, researchSourceArticleId, researchSourceSearchId,
    articleConsultUrl: () => "https://publisher.test/article",
    setBusy: (value) => calls.push(["busy", value]), setError: () => {},
    uploadAndExtractArticlePdf: async (...args) => calls.push(["upload", ...args]),
    decideImprovementSources: async (...args) => { calls.push(["sync", ...args]); return { session: updated } },
    setCurrent: (value) => calls.push(["current", value]), refreshList: async () => {},
  })
  await handle({ ...kept, guided_session_id: "research-session" }, file)
  assert.deepEqual(calls.find(([kind]) => kind === "upload"), ["upload", 8, 468, file, "https://publisher.test/article", "research-session"])
  assert.deepEqual(calls.find(([kind]) => kind === "sync"), ["sync", 8, "improvement-session", ["candidate-a"], "accepted", "", "research-session"])
  assert.deepEqual(calls.find(([kind]) => kind === "current"), ["current", updated])
  assert.deepEqual(calls.at(-1), ["busy", false])
})

test("failed upload cannot mark a PDF as extracted and releases the busy state", async () => {
  const calls = []
  const handle = uploadHandler({
    projectId: 8, current: { session_id: "improvement-session", context: { scholar_handoff: { guided_session_id: "research-session" } } },
    busy: false, backgroundActive: false, researchSourceArticleId, researchSourceSearchId, articleConsultUrl: () => "",
    setBusy: (value) => calls.push(value), setError: () => {},
    uploadAndExtractArticlePdf: async () => { throw new Error("PDF non conforme") },
    decideImprovementSources: async () => assert.fail("No selection refresh after failed upload"),
    setCurrent: () => assert.fail("No fake extraction success"), refreshList: async () => {},
  })
  await assert.rejects(() => handle({ ...kept, guided_session_id: "research-session" }, { name: "bad.pdf" }), /PDF non conforme/)
  assert.deepEqual(calls, [true, false])
})

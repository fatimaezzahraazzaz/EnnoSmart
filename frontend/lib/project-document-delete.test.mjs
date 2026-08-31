import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import ts from "typescript"
import { deleteDocument, getDocuments, getProjectOverviews, setTokens } from "./api.ts"

test("document deletion calls the authenticated project endpoint and invalidates cached lists", async () => {
  const previous = { window: globalThis.window, localStorage: globalThis.localStorage, fetch: globalThis.fetch }
  const values = new Map()
  globalThis.window = {}
  globalThis.localStorage = { getItem: (key) => values.get(key), setItem: (key, value) => values.set(key, value) }
  setTokens({ access_token: "test", refresh_token: "test" })
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return new Response(JSON.stringify(options.method === "DELETE" ? { ok: true, document_id: 15 } : []), { status: 200 })
  }
  try {
    await getDocuments(9)
    await getProjectOverviews()
    await getDocuments(9)
    assert.equal(calls.length, 2)
    await deleteDocument(9, 15)
    assert.ok(calls[2].url.endsWith("/projects/9/documents/15"))
    assert.equal(calls[2].options.method, "DELETE")
    assert.equal(calls[2].options.headers.get("Authorization"), "Bearer test")
    await getDocuments(9)
    await getProjectOverviews()
    assert.equal(calls.length, 5)
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: "Accès interdit" }), { status: 403 })
    await assert.rejects(deleteDocument(9, 15), /Accès interdit/)
  } finally {
    Object.assign(globalThis, previous)
  }
})

function pageHarness({ confirmed = true, remove = async () => ({ ok: true }) } = {}) {
  const source = readFileSync(new URL("../components/ennosmart/project-detail-page.tsx", import.meta.url), "utf8")
  const project = { id: 9, project_name: "Test", organisme: "Test", year: "2026", status: "Créé" }
  const document = { id: 15, project_id: 9, filename: "source.pdf" }
  const states = [project, [project], [], null, [document], false, null, "", "", "all", 1, 10, null, "", ""]
  let cursor = 0
  let confirmations = 0
  let deletions = 0
  const nativeRequire = createRequire(import.meta.url)
  const fakeRequire = (name) => {
    if (name === "react") return {
      useState: () => { const key = cursor++; return [states[key], (v) => states[key] = typeof v === "function" ? v(states[key]) : v] },
      useEffect: () => {}, useMemo: (fn) => fn(),
    }
    if (name === "@/lib/api") return { deleteDocument: async (...args) => { deletions++; assert.deepEqual(args, [9, 15]); return remove() } }
    if (name === "@/lib/project-session") return { getCurrentProjectId: () => 9 }
    if (name === "lucide-react" || name.startsWith("@/components/")) return new Proxy({}, { get: (_, key) => String(key) })
    return nativeRequire(name)
  }
  const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX } }).outputText
  const module = { exports: {} }
  new Function("require", "module", "exports", "window", compiled)(fakeRequire, module, module.exports,
    { confirm: () => { confirmations++; return confirmed } })
  function render() {
    cursor = 0
    const nodes = []
    function walk(node) {
      if (!node || typeof node !== "object") return
      if (Array.isArray(node)) { node.forEach(walk); return }
      if (typeof node.type === "function") { walk(node.type(node.props)); return }
      nodes.push(node)
      walk(node.props?.children)
    }
    walk(module.exports.default({ navigateTo: () => {} }))
    return nodes
  }
  return { render, states, confirmations: () => confirmations, deletions: () => deletions }
}

test("both desktop and mobile offer only a working delete action, cancellation keeps the document", async () => {
  const page = pageHarness({ confirmed: false })
  const buttons = page.render().filter((node) => node.props?.["aria-label"] === "Supprimer source.pdf")
  assert.equal(buttons.length, 2)
  buttons[0].props.onClick()
  await new Promise(setImmediate)
  assert.equal(page.confirmations(), 1)
  assert.equal(page.deletions(), 0)
  assert.equal(page.states[4].length, 1)
})

test("pending deletion is disabled and success removes the row and updates the count", async () => {
  let finish
  const page = pageHarness({ remove: () => new Promise((resolve) => { finish = resolve }) })
  page.render().find((node) => node.props?.["aria-label"] === "Supprimer source.pdf").props.onClick()
  assert.equal(page.states[12], 15)
  const pending = page.render().filter((node) => node.props?.["aria-label"] === "Supprimer source.pdf")
  assert.ok(pending.every((node) => node.props.disabled))
  finish({ ok: true })
  await new Promise(setImmediate)
  assert.equal(page.deletions(), 1)
  assert.equal(page.states[4].length, 0)
  assert.equal(page.states[12], null)
  assert.match(page.states[14], /supprimé/)
})

test("failure keeps the document visible and reports the error inline", async () => {
  const page = pageHarness({ remove: async () => { throw new Error("Accès interdit") } })
  page.render().find((node) => node.props?.["aria-label"] === "Supprimer source.pdf").props.onClick()
  await new Promise(setImmediate)
  assert.equal(page.states[4].length, 1)
  assert.equal(page.states[12], null)
  assert.ok(page.render().some((node) => node.props?.role === "alert" && node.props.children === "Accès interdit"))
})

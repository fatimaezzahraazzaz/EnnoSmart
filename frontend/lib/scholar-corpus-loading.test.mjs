import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import vm from "node:vm"
import ts from "typescript"

// Exercise the actual component handlers without a browser, React mocks or
// requests to the user's backend. TypeScript's parser extracts their bodies.
const source = ts.createSourceFile("chat.tsx", readFileSync(new URL(
  "../components/ennosmart/ennoscholar-plan-chat.tsx", import.meta.url,
), "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const wanted = new Set(["refreshConversationCorpus", "createConversation"])
const functions = []
function visit(node) {
  if (ts.isFunctionDeclaration(node) && wanted.has(node.name?.text)) functions.push(node.getText(source))
  ts.forEachChild(node, visit)
}
visit(source)
assert.equal(functions.length, wanted.size)
const handlers = ts.transpileModule(functions.join("\n"), {
  compilerOptions: { target: ts.ScriptTarget.ES2022 },
}).outputText

function setup(getCorpus, mode = "diagnostic_backed") {
  const state = { articles: [], corpusError: null }
  const context = {
    projectId: 5, sessionId: "new-chat", corpusRequestRef: { current: 0 },
    getGuidedResearchCorpus: getCorpus,
    createGuidedResearchSession: async () => ({ session: {
      project_id: 5, session_id: "new-chat", entry_module: "ennoscholar", messages: [],
      context: { operating_mode: mode },
    } }),
    localStorage: { setItem() {} }, storageKey: () => "test",
    refreshSessions: async () => [],
  }
  for (const name of ["Initializing", "Error", "Notice", "SessionId", "Messages", "OperatingMode", "SessionDraftMarkdown", "CorpusLoading", "CorpusError"]) {
    context[`set${name}`] = value => { state[name[0].toLowerCase() + name.slice(1)] = value }
  }
  context.setConversationArticles = value => { state.articles = value }
  vm.createContext(context)
  vm.runInContext(handlers, context)
  return { state, ...context }
}

test("creating a diagnostic chat immediately loads all nine existing proofs", async () => {
  const chat = setup(async (projectId, sessionId) => {
    assert.equal(projectId, 5)
    assert.equal(sessionId, "new-chat")
    return { ok: true, articles: Array.from({ length: 9 }, (_, i) => ({ id: i + 1 })) }
  })
  await chat.createConversation()
  assert.equal(chat.state.articles.length, 9)
  assert.equal(chat.state.corpusLoading, false)
  assert.equal(chat.state.corpusError, null)
})

test("standalone creation uses its scoped endpoint, with no project fallback", async () => {
  const chat = setup(async () => ({ ok: true, articles: [] }), "standalone_chat")
  await chat.createConversation()
  assert.equal(chat.state.articles.length, 0)
  assert.equal(chat.state.operatingMode, "standalone_chat")
})

test("an unavailable corpus is an error, not a successful empty result", async () => {
  const chat = setup(async () => ({ ok: false }))
  await chat.createConversation()
  assert.match(chat.state.corpusError, /indisponible/)
  assert.equal(chat.state.corpusLoading, false)
  assert.equal(chat.state.error, null)
})

test("a late response cannot replace the corpus of a newly opened chat", async () => {
  let finishOld
  const chat = setup(async (_, session) => session === "old"
    ? new Promise(resolve => { finishOld = resolve })
    : { ok: true, articles: [{ id: 9 }] })
  const pending = chat.refreshConversationCorpus("old")
  await chat.refreshConversationCorpus("new")
  finishOld({ ok: true, articles: [{ id: 1 }] })
  await pending
  assert.equal(chat.state.articles[0].id, 9)
})

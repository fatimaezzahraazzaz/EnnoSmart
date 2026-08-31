import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import ts from "typescript"

const source = readFileSync(new URL("../components/ennosmart/diagnosis-page.tsx", import.meta.url), "utf8")
const ast = ts.createSourceFile("diagnosis-page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const require = createRequire(import.meta.url)
const all = []
function visit(node) { all.push(node); ts.forEachChild(node, visit) }
visit(ast)

function evaluate(code, context = {}) {
  const compiled = ts.transpileModule(code, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
  } }).outputText
  const module = { exports: {} }
  new Function("require", "module", "exports", ...Object.keys(context), compiled)(
    require, module, module.exports, ...Object.values(context),
  )
  return module.exports
}
function findButton(label) {
  const button = all.find((node) => ts.isJsxElement(node)
    && node.openingElement.tagName.getText(ast) === "Button"
    && node.children.some((child) => ts.isJsxText(child) && child.text.trim() === label))
  assert.ok(button, `Missing button ${label}`)
  return button
}
const functionText = (name) => {
  const fn = ast.statements.find((node) => ts.isFunctionDeclaration(node) && node.name?.text === name)
  assert.ok(fn, `Missing function ${name}`)
  return fn.getText(ast)
}
const selection = all.find((node) => ts.isVariableDeclaration(node) && node.name.getText(ast) === "selectedVerrousForScholar")
const { getGroups, getSelected } = evaluate(`
  ${functionText("getValidationDecisionGroups")}
  export const getGroups = getValidationDecisionGroups;
  export function getSelected(verrous) { return ${selection.initializer.getText(ast)}; }
`)

const controls = { Button: "button", Search: "svg", ArrowRight: "svg" }
function renderButton(label, context) {
  return evaluate(`export const view = (${findButton(label).getText(ast)});`, {
    ...controls, ...context,
  }).view
}
function nodes(root) {
  if (!root || typeof root !== "object") return []
  if (Array.isArray(root)) return root.flatMap(nodes)
  return [root, ...nodes(root.props?.children)]
}
function textContent(root) {
  if (typeof root === "string" || typeof root === "number") return String(root)
  if (!root) return ""
  if (Array.isArray(root)) return root.map(textContent).join("")
  return textContent(root.props?.children)
}
const verrou = (id, status) => ({ id, title: `Verrou ${id}`, consultant_status: status })

test("one retained lock enables EnnoScholar even when other locks are pending", () => {
  const verrous = [verrou(1, "garde"), verrou(2, "en_attente"), verrou(3, "reformuler")]
  const before = structuredClone(verrous)
  let navigations = 0
  const button = renderButton("Passer à EnnoScholar", {
    selectedVerrousForScholar: getSelected(verrous), pendingReviewCount: 2,
    onOpenScholar: () => navigations++,
  })
  assert.equal(button.props.disabled, false)
  button.props.onClick()
  assert.equal(navigations, 1)
  assert.deepEqual(getSelected(verrous).map((item) => item.id), [1])
  assert.deepEqual(verrous, before)
})

test("no retained lock keeps the handoff disabled", () => {
  const button = renderButton("Passer à EnnoScholar", {
    selectedVerrousForScholar: getSelected([verrou(1, "rejete"), verrou(2, "en_attente")]),
    pendingReviewCount: 1, onOpenScholar() {},
  })
  assert.equal(button.props.disabled, true)
})

test("unavailable navigation stays disabled", () => {
  const button = renderButton("Passer à EnnoScholar", {
    selectedVerrousForScholar: [verrou(1, "garde")], pendingReviewCount: 0, onOpenScholar: undefined,
  })
  assert.equal(button.props.disabled, true)
})

test("review locks goes directly to the validation tab", () => {
  const destinations = []
  const button = renderButton("Revoir les verrous", {
    setActiveTab: (tab) => destinations.push(tab),
    setDiagnosticSection: () => assert.fail("Must not return to the diagnostic subsection"),
  })
  button.props.onClick()
  assert.deepEqual(destinations, ["validation"])
})

test("validation has three categories and preserves legacy pending decisions", () => {
  const verrous = [verrou(1, "garde"), verrou(2, "rejete"), verrou(3, "en_attente"),
    verrou(4, "reformuler"), verrou(5, null)]
  const before = structuredClone(verrous)
  const groups = getGroups(verrous)
  assert.deepEqual(groups.map(({ label }) => label), ["Retenu", "Non retenu", "En attente"])
  assert.deepEqual(groups.map(({ items }) => items.map(({ id }) => id)), [[1], [2], [3, 4, 5]])
  assert.deepEqual(verrous, before)
  assert.equal(groups.flatMap(({ items }) => items).length, verrous.length)
})

test("validation counters and lists render those same three groups", () => {
  const tab = all.find((node) => ts.isJsxElement(node)
    && node.openingElement.tagName.getText(ast) === "TabsContent"
    && node.openingElement.attributes.properties.some((attr) => ts.isJsxAttribute(attr)
      && attr.name.getText(ast) === "value" && attr.initializer?.text === "validation"))
  assert.ok(tab)
  const verrous = [verrou(1, "garde"), verrou(2, "rejete"), verrou(3, "reformuler")]
  const { view } = evaluate(`${functionText("decisionClass")}
    export const view = (${tab.getText(ast)});`, {
    ...controls, TabsContent: "section", Card: "article", CardContent: "div", CardHeader: "header",
    CardTitle: "h2", CardDescription: "p", Badge: "Badge", Upload: "svg", CheckCircle2: "svg",
    cirFinalRegistered: false, setActiveTab() {}, validationDecisionGroups: getGroups(verrous),
  })
  const text = textContent(view)
  for (const label of ["Retenu", "Non retenu", "En attente"]) assert.ok(text.includes(label))
  assert.doesNotMatch(text, /À consolider|À examiner/)
  for (const item of verrous) assert.equal(text.split(item.title).length - 1, 1)
  assert.deepEqual(nodes(view).filter((node) => node.type === "Badge").map(textContent), ["1", "1", "1"])
  assert.ok(nodes(view).some((node) => node.props?.className === "grid gap-3 sm:grid-cols-3"))
})

test("empty validation retains three empty categories", () => {
  assert.deepEqual(getGroups([]).map(({ items }) => items.length), [0, 0, 0])
})

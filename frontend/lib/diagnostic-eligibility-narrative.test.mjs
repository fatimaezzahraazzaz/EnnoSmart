import assert from "node:assert/strict"
import test from "node:test"
import { readFileSync } from "node:fs"
import { createRequire } from "node:module"
import ts from "typescript"

// Run the real card and its real proof resolver without mounting the rest of
// the diagnostic page or issuing requests to the running application.
const source = readFileSync(new URL("../components/ennosmart/diagnosis-page.tsx", import.meta.url), "utf8")
const ast = ts.createSourceFile("diagnosis-page.tsx", source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
const names = new Set([
  "UnifiedEligibilityStudyCardV191", "eligibilityProofToEvidenceV191", "eligibilityProofKeyV193",
  "dedupeEligibilityProofsV191", "isFrenchEligibilityTextV190", "isStrongProjectEligibilityTextV192",
  "cleanDisplayText", "getEligibilityProofClaimsV153", "unwrapBackendDiagnosticReportV93",
  "normalizeKeyV93", "fixFrenchMojibakeV93", "parseJsonMaybeV93",
])
const functions = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && names.has(node.name?.text))
assert.equal(functions.length, names.size)
const isolated = `
const Card = 'Card', CardHeader = 'CardHeader', CardTitle = 'CardTitle', CardContent = 'CardContent',
  CardDescription = 'CardDescription', BrainCircuit = 'BrainCircuit', Badge = 'Badge',
  SourceEvidenceCitations = 'SourceEvidenceCitations';
${functions.map((node) => node.getText(ast)).join("\n")}
export { UnifiedEligibilityStudyCardV191 as CardView, getEligibilityProofClaimsV153 as getClaims };
`
const compiled = ts.transpileModule(isolated, { compilerOptions: {
  module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022,
} }).outputText
const module = { exports: {} }
new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), module, module.exports)
const { CardView, getClaims } = module.exports

const boilerplate = "Les preuves rattachées sont insuffisantes pour qualifier le noyau R&D. Maillons documentés : incertitude/verrou. Maillons à consolider : hypothèse, expérimentation, résultat, apprentissage. Validation du consultant CIR requise."
const proof = (id, text) => ({ evidence_id: id, document: "rapport.docx", excerpt: text })
const f0 = proof("F0", "Indice de défendabilité : 80 %, couverture documentaire : 80 %.")
const f1 = proof("F1", "La stabilité thermique reste incertaine.")
const f2 = proof("F2", "Nous avons réalisé les essais thermiques.")
const f3 = proof("F3", "Le logiciel a été installé selon la procédure existante.")
const technical = "Le verrou porte sur la stabilité thermique. L’hypothèse est confrontée aux essais ; les résultats délimitent les conditions étudiées."
const perimeter = "L’installation du logiciel applique une procédure connue et relève de l’ingénierie classique. La reproductibilité doit être précisée."
const frascati = "La nouveauté est documentée par la comparaison des solutions existantes ; la créativité reste à expliciter. La défendabilité est de 80 %, sous réserve de validation du consultant."

function fixture() {
  const paragraphs = [
    { text: technical, evidence_ids: ["F1", "F2"], claims: [
      { text: technical, claim_kind: "verrou", evidence_ids: ["F1", "F2"] },
    ] },
    { text: perimeter, evidence_ids: ["F3"], claims: [
      { text: perimeter, claim_kind: "perimetre_limites", evidence_ids: ["F3"] },
    ] },
    { text: frascati, evidence_ids: ["F0"], claims: [
      { text: frascati, claim_kind: "conclusion", evidence_ids: ["F0"] },
    ] },
  ]
  const evidenceReport = { score: .8, documented_share: .8, remaining_documentary_gap: .2,
    operations: Array.from({ length: 8 }, (_, i) => ({ group_id: `op-${i}`, justification_fr: boilerplate,
      functional_evidence: { uncertainty: [f1] } })),
    reference_operation: { functional_evidence: { uncertainty: [f1], experiment: [f2] } },
  }
  const display = { diagnostic_cards: [{ key: "lecture_frascati", paragraphs, evidence: [f0, f1, f2, f3] }] }
  return { score: .8, signalsCount: 8, candidateCount: 4, reading: "Ancienne lecture du score.",
    justification: `${technical}\n\n${perimeter}\n\n${frascati}`, demarche: { project_status: "rnd_core_defendable" },
    evidenceReport, proofClaims: getClaims({}, display), projectId: 8, sourceDocuments: [] }
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

test("all three backend paragraphs survive, including short limits and Frascati", () => {
  const data = fixture()
  const before = structuredClone(data)
  const rendered = CardView(data)
  const texts = nodes(rendered).filter((n) => n.type === "p").map(textContent)
  assert.ok(texts.includes(technical))
  assert.ok(texts.includes(perimeter))
  assert.ok(texts.includes(frascati))
  assert.doesNotMatch(textContent(rendered), /Maillons documentés|Les critères Frascati acquis/)
  assert.deepEqual(data, before)
})

test("citations stay local and F0 is not exposed as a document link", () => {
  const rendered = CardView(fixture())
  const citations = nodes(rendered).filter((n) => n.type === "SourceEvidenceCitations")
  assert.equal(citations.length, 2)
  assert.deepEqual(citations[0].props.evidence.map((p) => p.evidence_id), ["F1", "F2"])
  assert.deepEqual(citations[0].props.citationNumbers, [1, 2])
  assert.deepEqual(citations[1].props.evidence.map((p) => p.evidence_id), ["F3"])
  assert.deepEqual(citations[1].props.citationNumbers, [3])
})

test("backend fallback is displayed without concatenating eight operation audits", () => {
  const data = fixture()
  data.proofClaims = []
  data.justification = "La rédaction n’a pas abouti. Le score est conservé ; les preuves nécessitent une analyse par le consultant."
  const rendered = textContent(CardView(data))
  assert.ok(rendered.includes(data.justification))
  assert.doesNotMatch(rendered, /Maillons documentés|Ancienne lecture du score|Les critères Frascati acquis/)
})

test("missing narrative reports the issue instead of inventing an eligibility verdict", () => {
  const data = fixture()
  data.proofClaims = []
  data.justification = ""
  const rendered = CardView(data)
  assert.match(textContent(rendered), /La conclusion explicative n’est pas disponible/)
  assert.match(textContent(rendered), /Relancez EnnoDiagnostic/)
  assert.doesNotMatch(textContent(rendered), /Maillons documentés|potentiellement éligible/)
  assert.equal(nodes(rendered).filter((n) => n.type === "SourceEvidenceCitations").length, 0)
})

test("multiple atomic claims remain in their backend paragraph without repetition", () => {
  const data = fixture()
  const extra = "Les essais mesurent les écarts du prototype dans les conditions testées."
  data.proofClaims[0].claims.push({ text: extra, claim_kind: "resultats", proofs: [f2] })
  const rendered = CardView(data)
  const paragraphs = nodes(rendered).filter((n) => n.type === "p").map(textContent)
  assert.ok(paragraphs.includes(`${technical} ${extra}`))
  assert.equal(textContent(rendered).split(extra).length - 1, 1)
  assert.ok(paragraphs.includes(perimeter))
  assert.ok(paragraphs.includes(frascati))
})

test("legacy sourced narratives without atomic claims remain readable", () => {
  const data = fixture()
  data.proofClaims = [{ text: technical, proofs: [f1, f2] }]
  const rendered = CardView(data)
  assert.ok(textContent(rendered).includes(technical))
  assert.doesNotMatch(textContent(rendered), /Maillons documentés/)
})

import assert from "node:assert/strict"
import test from "node:test"
import { scholarSearchFailureMessage } from "./scholar-search-status.ts"

test("empty or successfully completed searches do not show a failure", () => {
  assert.equal(scholarSearchFailureMessage(null), "")
  assert.equal(scholarSearchFailureMessage({ results: [] }), "")
  assert.equal(scholarSearchFailureMessage({ results: [{ verrou_id: "v1" }] }), "")
})

test("mixed results expose every failed lock, including older report errors", () => {
  const message = scholarSearchFailureMessage({ results: [
    { verrou_id: "v1", verrou_title: "Lock one", subject_search_failed: true },
    { verrou_id: "v2" },
    { verrou_id: "v3", search_status: { execution_error: "old error" } },
    { verrou_id: "v4", query_planning_failed: true },
  ] })
  assert.match(message, /3 verrou\(s\) sur 4/)
  assert.match(message, /Lock one/)
  assert.match(message, /Verrou v3/)
  assert.match(message, /Verrou v4/)
  assert.doesNotMatch(message, /Verrou v2/)
  assert.match(message, /Relancez/)
  assert.match(message, /ne signifie pas qu’aucun article n’existe/)
})

test("a planning failure remains distinct from an empty bibliography", () => {
  const message = scholarSearchFailureMessage({ results: [
    { verrou_id: "v1", search_status: { query_planning_failed: true } },
  ] })
  assert.match(message, /1 verrou\(s\) sur 1/)
})

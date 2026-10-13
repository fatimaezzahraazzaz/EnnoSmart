// Opt-in real authenticated frontend/backend/LLM validation. No tokens in artifacts.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright")
const fs = require("node:fs")
const path = require("node:path")
const research = path.join(__dirname, "research")
async function main() {
  const { session_id: sid } = JSON.parse(fs.readFileSync(path.join(research, "replay_running.json"), "utf8"))
  const browser = await chromium.launch({ channel: "chrome", headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1024 } })
  const errors = []
  page.on("pageerror", error => errors.push(error.message))
  try {
    const login = await page.request.post("http://127.0.0.1:8002/auth/login", { data: { email: process.env.V4_TEST_EMAIL, password: process.env.V4_TEST_PASSWORD } })
    if (!login.ok()) throw new Error("Local test login failed")
    const tokens = await login.json()
    const headers = { Authorization: "Bearer " + tokens.access_token }
    const user = await (await page.request.get("http://127.0.0.1:8002/auth/me", { headers })).json()
    await page.addInitScript(({ tokens, sid, uid }) => {
      localStorage.setItem("ennosmart_access_token", tokens.access_token)
      localStorage.setItem("ennosmart_refresh_token", tokens.refresh_token)
      localStorage.setItem(`ennoscholar-v4-session:${uid}`, sid)
    }, { tokens, sid, uid: user.id })
    await page.goto("http://localhost:3000/ennoscholar-v4")
    await page.getByRole("heading", { name: "EnnoScholar V4", exact: true }).waitFor({ timeout: 60000 })
    await page.locator("#scholar-v4-history").selectOption(sid)
    const response = page.locator('article[aria-label="Réponse EnnoScholar"]').first()
    await response.waitFor({ timeout: 650000 })
    const session = await (await page.request.get(`http://127.0.0.1:8002/scholar-v4/sessions/${sid}`, { headers })).json()
    const initialMessage = session.messages.find(message => message.role === "assistant")
    const run = await (await page.request.get(`http://127.0.0.1:8002/scholar-v4/runs/${initialMessage.run_id}`, { headers })).json()
    const initialPrimary = session.articles.find(article => article.id === run.primary_article_ids[0])
    const result = { real_backend: true, real_llm: run.usage.model, session_id: sid, status: run.status,
      table_rows: await response.locator("table tbody tr").count(), clickable_links: await response.locator("a[href]").count(),
      markdown_displayed: (await response.innerText()).includes(initialPrimary.title), secondary_collapsed: true,
      primary_order_saved: session.displayed_article_ids.length > 0, used_sources_count: run.citations.length,
      discovered_url_count: run.discovered_sources_count }
    const groupLabels = { retained_complementary: "Références complémentaires retenues", unexamined: "Candidats non évalués", examined_pending: "Candidats à confirmer", rejected: "Notices écartées" }
    const category = Object.keys(groupLabels).find(key => run.library_counts?.[key] > 0)
    const secondary = response.locator("details").filter({ hasText: groupLabels[category] })
    result.secondary_collapsed = !(await secondary.getAttribute("open")) && !(await secondary.evaluate(node => node.open))
    await secondary.locator("summary").click()
    await secondary.getByRole("button", { name: "Approfondir cette publication" }).first().waitFor({ timeout: 30000 })
    result.secondary_loaded = await secondary.locator("li").count() > 0
    result.secondary_excludes_primary = await secondary.locator("li").count() === Math.min(24, run.library_counts[category])
    result.library_counts = run.library_counts
    result.coverage_status_displayed = run.research_status !== "incomplete" || (await response.innerText()).includes("Couverture scientifique à compléter")
    result.native_markup_hidden = !(await response.innerText()).includes("")
    await secondary.getByRole("button", { name: "Approfondir cette publication" }).first().click()
    result.secondary_identifiable = /paper_/.test(await page.locator("#scholar-v4-message").inputValue())
    fs.mkdirSync(".codex_tmp", { recursive: true })
    await page.screenshot({ path: ".codex_tmp/v4-finalization-live.png", fullPage: true })
    await page.reload()
    await page.locator('article[aria-label="Réponse EnnoScholar"] table').first().waitFor({ timeout: 60000 })
    result.reload_restored = (await page.locator("#scholar-v4-history").inputValue()) === sid
    const textbox = page.getByRole("textbox", { name: "Votre message scientifique" })
    const followupPrompt = "Approfondis l’article 4 du tableau précédent en six phrases : méthode, résultats et limites. Réutilise exclusivement les sources déjà enregistrées, sans nouvelle recherche."
    const existing = session.messages.findIndex(message => message.role === "user" && message.content === followupPrompt)
    let started
    if (existing >= 0 && session.messages[existing + 1]?.role === "assistant") {
      started = { id: session.messages[existing + 1].run_id }
    } else {
      const startedPromise = page.waitForResponse(r => r.url().endsWith("/scholar-v4/chat") && r.request().method() === "POST")
      await textbox.fill(followupPrompt)
      await textbox.press("Enter")
      started = await (await startedPromise).json()
    }
    console.log(JSON.stringify({ followup_started: started.id, expected_article4: session.displayed_article_ids[3] }))
    await page.waitForFunction(() => document.querySelectorAll('article[aria-label="Réponse EnnoScholar"]').length >= 2, null, { timeout: 650000 })
    const followup = await (await page.request.get(`http://127.0.0.1:8002/scholar-v4/runs/${started.id}`, { headers })).json()
    const after = await (await page.request.get(`http://127.0.0.1:8002/scholar-v4/sessions/${sid}`, { headers })).json()
    const article4 = session.articles.find(a => a.id === session.displayed_article_ids[3])
    result.followup = { run_id: started.id, status: followup.status, partial: followup.partial, expected_title: article4?.title,
      cited_expected: followup.cited_article_ids?.includes(session.displayed_article_ids[3]),
      order_preserved: JSON.stringify(after.displayed_article_ids) === JSON.stringify(session.displayed_article_ids),
      no_new_search: !followup.tool_counts.search_scientific && !followup.tool_counts.web_search,
      usage: followup.usage, duration_seconds: followup.duration_seconds, tool_counts: followup.tool_counts }
    result.runtime_errors = errors
    await page.setViewportSize({ width: 390, height: 844 })
    result.responsive = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    await page.screenshot({ path: ".codex_tmp/v4-context-mobile.png", fullPage: true })
    const prefix = process.env.V4_REPORT_PREFIX || "observed_finalization"
    fs.writeFileSync(path.join(research, prefix + "_ui_20261013.json"), JSON.stringify(result, null, 2))
    fs.writeFileSync(path.join(research, prefix + "_article4_answer_20261013.md"), followup.answer)
    console.log(JSON.stringify(result))
    if (result.status !== "completed" || result.table_rows < 3 || !result.markdown_displayed || !result.secondary_loaded || !result.reload_restored || !result.native_markup_hidden || !result.responsive || !result.coverage_status_displayed || result.followup.status !== "completed" || !result.followup.cited_expected || !result.followup.order_preserved || !result.followup.no_new_search || errors.length) throw new Error("Real chat validation failed; see observed report")
  } finally { await browser.close() }
}
main().catch(error => { console.error(error.message); process.exitCode = 1 })

// Real backend service + real configured LLM. Login is a local fixture, never a production account.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright")
const fs = require("node:fs")
async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true })
  const page = await browser.newPage({ viewport: { width: 1440, height: 1024 } })
  await page.addInitScript(() => {
    localStorage.setItem("ennosmart_access_token", "ui-fixture-only")
    localStorage.setItem("ennosmart_refresh_token", "ui-fixture-only")
  })
  try {
    await page.goto("http://localhost:3014/ennoscholar-v4")
    await page.getByRole("heading", { name: "EnnoScholar V4", exact: true }).waitFor({ timeout: 60000 })
    const history = page.locator("#scholar-v4-history")
    await history.selectOption({ label: "Validation backend et LLM V4" })
    const textbox = page.getByRole("textbox", { name: "Votre message scientifique" })
    await textbox.waitFor()
    await page.waitForFunction(() => !document.querySelector("#scholar-v4-message").disabled)
    const answerCount = await page.locator('article[aria-label="Réponse EnnoScholar"]').count()
    const responsePromise = page.waitForResponse(response => response.url().endsWith("/scholar-v4/chat") && response.request().method() === "POST")
    await textbox.fill("Rappelle en six phrases la méthode de MeshingNet3D, ses limites et ce que nous avons réellement consulté. Utilise les sources déjà conservées, sans nouvelle recherche.")
    await textbox.press("Enter")
    const response = await responsePromise
    const run = await response.json()
    if (response.status() !== 202) throw new Error("Backend rejected real chat: " + response.status())
    await page.waitForFunction(count => document.querySelectorAll('article[aria-label="Réponse EnnoScholar"]').length > count, answerCount, { timeout: 650000 })
    const displayed = await page.locator('article[aria-label="Réponse EnnoScholar"]').last().innerText()
    const final = await (await page.request.get(`http://127.0.0.1:8014/scholar-v4/runs/${run.id}`)).json()
    const report = { real_backend: true, authentication: "isolated_fixture", database: "isolated_sqlite", real_llm: final.usage?.model,
      status: final.status, partial: final.partial, output_tokens: final.usage?.output_tokens,
      displayed_in_chat: displayed.includes("MeshingNet3D"), clickable_sources: await page.locator('article[aria-label="Réponse EnnoScholar"]').last().locator("a[href]").count(),
      tool_counts: final.tool_counts, duration_seconds: final.duration_seconds, estimated_cost_usd: final.usage?.estimated_cost_usd }
    if (report.status !== "completed" || report.partial || !report.displayed_in_chat || !report.output_tokens || !report.clickable_sources) throw new Error(JSON.stringify(report))
    await page.screenshot({ path: ".codex_tmp/v4-live-chat.png", fullPage: true })
    fs.writeFileSync("tests/ennoscholar_v4/research/observed_ui_live_20261013.json", JSON.stringify(report, null, 2))
    console.log(JSON.stringify(report))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })

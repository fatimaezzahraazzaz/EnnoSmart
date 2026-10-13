// Run against ui_smoke_server.py and Next dev on port 3014. No external API calls.
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || "playwright")
const fs = require("node:fs")
const path = require("node:path")

async function main() {
  const browser = await chromium.launch({ channel: "chrome", headless: true })
  const context = await browser.newContext({ viewport: { width: 1440, height: 1024 } })
  await context.addInitScript(() => {
    localStorage.setItem("ennosmart_access_token", "ui-fixture-only")
    localStorage.setItem("ennosmart_refresh_token", "ui-fixture-only")
  })
  const page = await context.newPage()
  const errors = []
  page.on("pageerror", error => errors.push(error.message))
  const result = {}
  try {
    await page.goto("http://localhost:3014/ennoscholar-v4")
    await page.getByRole("heading", { name: "EnnoScholar V4", exact: true }).waitFor({ timeout: 60000 })
    result.sidebar = await page.getByRole("button", { name: "EnnoScholar V4", exact: true }).count() > 0
    // The existing consultant role hides V1 and EnnoAmelioration; preserve that policy.
    for (const label of ["EnnoScholar V2", "EnnoScholar V3", "EnnoScholar V3.1", "EnnoDiagnostic"]) {
      if (!await page.getByRole("button", { name: label, exact: true }).count()) throw new Error("Missing sidebar entry: " + label)
    }
    await page.getByRole("button", { name: "Nouvelle conversation", exact: true }).click()
    const textbox = page.getByRole("textbox", { name: "Votre message scientifique" })
    await textbox.fill("Premier message de test")
    await textbox.press("Shift+Enter")
    if (!(await textbox.inputValue()).includes("\n")) throw new Error("Shift Enter did not insert newline")
    await textbox.press("Enter")
    await page.getByRole("button", { name: "Arrêter", exact: true }).waitFor()
    result.activity = await page.getByRole("status").count() > 0
    await page.getByRole("heading", { name: "Résultat du test interface" }).waitFor({ timeout: 20000 })
    result.markdown = await page.locator("table").count() === 1
    result.clickable_citation = await page.getByRole("link", { name: "Source de test" }).getAttribute("href") === "https://example.invalid/publication"
    const id = await page.locator("#scholar-v4-history").inputValue()
    await page.reload()
    await page.getByRole("heading", { name: "Résultat du test interface" }).waitFor()
    result.reload = await page.locator("#scholar-v4-history").inputValue() === id
    await textbox.fill("Longue recherche simulée à arrêter")
    await textbox.press("Enter")
    await page.getByRole("button", { name: "Arrêter", exact: true }).click()
    await page.getByText("Recherche simulée arrêtée.", { exact: true }).waitFor({ timeout: 20000 })
    result.cancel = true
    await page.locator('input[type="file"]').setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("Document scientifique de test.") })
    await page.waitForFunction(() => document.querySelector("#scholar-v4-message").value.includes("notes"))
    result.upload = true
    await page.route("**/scholar-v4/chat", route => route.abort("failed"))
    await textbox.fill("Message conservé après erreur réseau")
    await textbox.press("Enter")
    await page.getByRole("alert").filter({ hasText: "Failed to fetch" }).waitFor()
    result.network_error_preserves_draft = (await textbox.inputValue()) === "Message conservé après erreur réseau"
    await page.unroute("**/scholar-v4/chat")
    fs.mkdirSync(path.join(process.cwd(), ".codex_tmp"), { recursive: true })
    await page.screenshot({ path: ".codex_tmp/v4-desktop.png", fullPage: true })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.screenshot({ path: ".codex_tmp/v4-mobile.png", fullPage: true })
    result.responsive = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
    result.runtime_errors = errors
    if (errors.length || Object.entries(result).some(([key, value]) => key !== "runtime_errors" && !value)) throw new Error(JSON.stringify(result))
    fs.writeFileSync(".codex_tmp/v4-ui-results.json", JSON.stringify(result, null, 2))
    console.log(JSON.stringify(result))
  } finally { await browser.close() }
}
main().catch(error => { console.error(error); process.exitCode = 1 })

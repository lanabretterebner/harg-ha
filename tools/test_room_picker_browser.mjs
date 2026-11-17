/** Room page picker in a real browser, behind a loopback stand-in for Supervisor ingress.
 *
 * Starts tools/room_picker_fixture.py (the app's real handler with synthetic
 * fakes), puts a small ingress-like proxy in front that strips the ingress
 * prefix and adds X-Ingress-Path and X-Remote-User-Id, and drives the page.
 * Never connects to HA, a Supervisor or a controller. Screenshots are written
 * only when PICKER_SCREENSHOT_DIR is set.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { createServer, request as httpRequest } from "node:http";
import { dirname, join, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PREFIX = "/api/hassio_ingress/synthetic_browser_ingress_1234";
const ROUTE = "/api/local-monitor/raumsensoren";
const ADMIN = "a".repeat(32);
const USER = "b".repeat(32);
const SHOTS = process.env.PICKER_SCREENSHOT_DIR || "";
let checks = 0;
const check = (condition, message) => { assert.ok(condition, message); checks += 1; };

function startFixture() {
  const python = spawn(process.env.PYTHON || "python3", ["-B", join(ROOT, "tools/room_picker_fixture.py")],
    { stdio: ["pipe", "pipe", "inherit"] });
  const saved = [];
  const port = new Promise((accept, reject) => {
    python.once("error", reject);
    python.once("exit", (code) => reject(new Error(`fixture exited with ${code}`)));
    createInterface({ input: python.stdout }).on("line", (line) => {
      if (line.startsWith("PORT ")) accept(Number(line.slice(5)));
      else if (line.startsWith("SAVED ")) saved.push(JSON.parse(line.slice(6)));
    });
  });
  return { python, saved, port };
}

function ingress(appPort, state) {
  const server = createServer((incoming, outgoing) => {
    const url = new URL(incoming.url, "http://127.0.0.1");
    if (url.pathname === "/fremde-seite") {
      // A page of another origin that tries to save.
      outgoing.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      outgoing.end(`<!doctype html><script>
        const target = ${JSON.stringify(state.target)};
        const body = JSON.stringify({ revision: "0".repeat(64), modus: "melden", heizkreis_1_art: "automatisch",
          heizkreis_2_art: "automatisch", sensoren: [] });
        window.results = [];
        fetch(target, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body })
          .then((r) => window.results.push("json:" + r.status), () => window.results.push("json:blocked"))
          .then(() => fetch(target, { method: "POST", mode: "no-cors", credentials: "include",
            headers: { "Content-Type": "text/plain" }, body }))
          .then(() => window.results.push("plain:sent"), () => window.results.push("plain:blocked"));
      </script>`);
      return;
    }
    if (!url.pathname.startsWith(PREFIX + "/")) { outgoing.writeHead(404).end(); return; }
    const headers = { ...incoming.headers };
    for (const name of Object.keys(headers)) {
      if (name.startsWith("x-remote-user") || name === "x-ingress-path") delete headers[name];
    }
    headers["x-ingress-path"] = PREFIX;
    headers["x-remote-user-id"] = state.user;
    const forward = httpRequest({ host: "127.0.0.1", port: appPort, method: incoming.method,
      path: incoming.url.slice(PREFIX.length), headers }, (response) => {
      outgoing.writeHead(response.statusCode, response.headers);
      response.pipe(outgoing);
    });
    forward.on("error", () => { outgoing.writeHead(502).end(); });
    incoming.pipe(forward);
  });
  return new Promise((accept) => server.listen(0, "127.0.0.1", () => accept(server)));
}

async function shot(page, name) {
  if (!SHOTS) return;
  await mkdir(SHOTS, { recursive: true });
  // What the viewer sees first, then the whole page (the save bar is sticky at the bottom).
  await page.screenshot({ path: join(SHOTS, name) });
  await page.screenshot({ path: join(SHOTS, name.replace(".png", "-ganz.png")), fullPage: true });
}

async function main() {
  const fixture = startFixture();
  const appPort = await fixture.port;
  const state = { user: ADMIN, target: "" };
  const proxy = await ingress(appPort, state);
  const origin = `http://127.0.0.1:${proxy.address().port}`;
  state.target = origin + PREFIX + ROUTE;
  const browser = await chromium.launch();
  const problems = [];
  try {
    for (const colorScheme of ["light", "dark"]) {
      for (const width of [1280, 390]) {
        const context = await browser.newContext({ viewport: { width, height: 900 }, colorScheme });
        const page = await context.newPage();
        page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
        page.on("console", (message) => { if (message.type() === "error") problems.push(message.text()); });
        state.user = ADMIN;
        const response = await page.goto(origin + PREFIX + ROUTE);
        check(response.status() === 200, "page loads through ingress");
        check(/script-src 'nonce-/.test(response.headers()["content-security-policy"]), "nonce CSP");
        await page.waitForSelector("#suchfeld:not([hidden])");
        check(await page.locator("#treffer").textContent() === "10 von 10 Sensoren angezeigt", "hit count");
        check((await page.locator("#anzahl").textContent()).startsWith("Ausgewählt: 2 von höchstens 24"), "count");
        const overflow = await page.evaluate(() => document.scrollingElement.scrollWidth - window.innerWidth);
        check(overflow <= 0, `no horizontal scroll at ${width} px (${overflow})`);
        const background = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
        check(colorScheme === "dark" ? background === "rgb(17, 19, 21)" : background === "rgb(255, 255, 255)",
          `${colorScheme} background ${background}`);
        await shot(page, `picker-${width}-${colorScheme}.png`);
        await context.close();
      }
    }

    // Interaction as an administrator: search, groups, flags, save.
    const context = await browser.newContext({ viewport: { width: 390, height: 900 } });
    const page = await context.newPage();
    page.on("pageerror", (error) => problems.push(`page error: ${error.message}`));
    state.user = ADMIN;
    await page.goto(origin + PREFIX + ROUTE);
    await page.waitForSelector("#suchfeld:not([hidden])");
    await page.getByLabel("Sensoren suchen (Name, Bereich oder Entität)").fill("kuche");
    check(await page.locator("#treffer").textContent() === "1 von 10 Sensoren angezeigt", "accent-free search");
    const kueche = page.locator('fieldset[data-entity="sensor.kueche"]');
    check(await kueche.isVisible(), "Küche found");
    check(!(await page.locator('fieldset[data-entity="sensor.bad"]').isVisible()), "others hidden");
    check(!(await kueche.getByLabel("darf kühler sein").isVisible()), "flags hidden while not monitored");
    await kueche.getByLabel("Überwachung").selectOption("heizkreis_2");
    check(await kueche.getByLabel("darf kühler sein").isVisible(), "circuit flags shown");
    check(!(await kueche.getByLabel("Frostwarnung").isVisible()), "frost hidden for circuits");
    await kueche.getByLabel("Ofen oder viel Sonne").check();
    await page.getByLabel("Sensoren suchen (Name, Bereich oder Entität)").fill("keller");
    const keller = page.locator('fieldset[data-entity="sensor.keller"]');
    await keller.getByLabel("Überwachung").selectOption("weitere");
    check(await keller.getByLabel("Frostwarnung").isVisible(), "frost shown for Weitere Räume");
    check(!(await keller.getByLabel("darf kühler sein").isVisible()), "circuit flags hidden for Weitere Räume");
    await keller.getByLabel("Frostwarnung").check();
    // The missing sensor stays visible and can be removed.
    await page.getByLabel("Sensoren suchen (Name, Bereich oder Entität)").fill("alter");
    const old = page.locator('fieldset[data-entity="sensor.alter_sensor"]');
    check((await old.textContent()).includes("nicht mehr in Home Assistant gefunden"), "missing sensor shown");
    await old.getByLabel("Überwachung").selectOption("");
    await page.getByLabel("Sensoren suchen (Name, Bereich oder Entität)").fill("");
    await page.getByLabel("melden - Meldungen zählen für den Gesamtzustand").check();
    await page.getByLabel("Heizkreis 2 ist").selectOption("fussboden");
    check((await page.locator("#anzahl").textContent()).startsWith("Ausgewählt: 3 von"), "count follows choices");
    // Keyboard: Space on the focused save button submits.
    await page.locator("#speichern").focus();
    await page.keyboard.press("Enter");
    await page.waitForFunction(() => document.getElementById("meldung").textContent.startsWith("Gespeichert."));
    check(await page.locator("#meldung").getAttribute("role") === "status", "success announced");
    check(fixture.saved.length === 1, "one options POST");
    check((await page.locator("#meldung").textContent()).includes("ab sofort"), "running monitor took it over");
    assert.deepEqual(fixture.saved[0], {
      haus_frost_sensoren: ["sensor.keller"], haus_sensoren: ["sensor.keller"], heizkreis_1_art: "automatisch",
      heizkreis_1_sensoren: ["sensor.wohnzimmer"], heizkreis_2_art: "fussboden",
      heizkreis_2_sensoren: ["sensor.kueche"], kuehlere_raeume: ["sensor.wohnzimmer"],
      ohne_ueberwaermung: ["sensor.kueche"], raumueberwachung_modus: "melden" });
    checks += 1;
    await shot(page, "picker-390-light-saved.png");
    // A second save from the same page uses the new revision; an unchanged selection posts nothing.
    await page.locator("#speichern").click();
    await page.waitForFunction(() => document.getElementById("meldung").textContent.startsWith("Keine Änderung"));
    check(fixture.saved.length === 1, "unchanged selection not posted");
    // Focus order: search, then the first sensor's group choice.
    await page.getByLabel("Sensoren suchen (Name, Bereich oder Entität)").focus();
    await page.keyboard.press("Tab");
    check(await page.evaluate(() => document.activeElement.id) === "g1", "Tab reaches the first sensor");
    await context.close();

    // An ordinary user sees the selection read-only.
    state.user = USER;
    const viewer = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await viewer.goto(origin + PREFIX + ROUTE);
    check(await viewer.locator("#speichern").count() === 0, "no save button for ordinary users");
    check(await viewer.locator('select[name="gruppe"]').first().isDisabled(), "choices disabled");
    check((await viewer.textContent("body")).includes("Nur Administratoren von Home Assistant"), "viewer note");
    check(!(await viewer.textContent("body")).includes("noch nicht aktiv"), "saved selection is the running one");
    await shot(viewer, "picker-1280-light-viewer.png");
    await viewer.close();

    // Another origin cannot save, with or without a CORS preflight.
    state.user = ADMIN;
    const foreign = await browser.newPage();
    await foreign.goto(`http://localhost:${proxy.address().port}/fremde-seite`);
    await foreign.waitForFunction(() => window.results.length === 2);
    const results = await foreign.evaluate(() => window.results);
    check(results[0] === "json:blocked", `cross-site JSON blocked (${results[0]})`);
    check(fixture.saved.length === 1, "cross-site requests saved nothing");
    await foreign.close();
    check(problems.length === 0, `no page errors or CSP violations: ${problems.join(" | ")}`);
  } finally {
    await browser.close();
    proxy.close();
    fixture.python.stdin.end();
  }
  console.log(`Room page browser checks passed: ${checks}.`);
}

await main();

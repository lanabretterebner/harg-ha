/** Execute the shipped synthetic DOM fixtures; never connect to HA or a controller. */
import assert from "node:assert/strict";
import { readFile, realpath } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { chromium } from "playwright";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ALLOWED = new Set([
  "/tests/test-thermostat-popup.html", "/tests/test-thermostat-popup.js",
  "/tests/test-thermostat-card.js", "/tests/test-device-groups.html",
  "/tests/test-device-groups.js", "/tests/device-group-fixture-data.js",
  "/dist/hargassner-thermostat-popup.js", "/favicon.ico",
]);
const SUITES = [
  { path: "/tests/test-thermostat-popup.html", global: "hargassnerFrontendTests", groups: { popup: 59, card: 113 } },
  { path: "/tests/test-device-groups.html", global: "deviceGroupTestResults", groups: { groups: 22 } },
];

async function fixtureServer(root) {
  const base = await realpath(root);
  const server = createServer(async (request, response) => {
    try {
      const url = new URL(request.url, "http://127.0.0.1");
      if (!["GET", "HEAD"].includes(request.method) || !ALLOWED.has(url.pathname) || url.search) {
        response.writeHead(404).end(); return;
      }
      if (url.pathname === "/favicon.ico") { response.writeHead(204).end(); return; }
      const path = await realpath(resolve(base, `.${url.pathname}`));
      if (!path.startsWith(base + sep)) { response.writeHead(404).end(); return; }
      const body = await readFile(path);
      response.writeHead(200, {
        "Content-Type": path.endsWith(".html") ? "text/html; charset=utf-8" : "text/javascript; charset=utf-8",
        "Cache-Control": "no-store",
        "Content-Length": body.length,
      });
      response.end(request.method === "HEAD" ? undefined : body);
    } catch { response.writeHead(404).end(); }
  });
  await new Promise((accept, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", accept);
  });
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}

function validateGroup(name, group, minimum) {
  assert(group && typeof group === "object", `${name}: missing fixture report`);
  assert(Number.isSafeInteger(group.tests) && group.tests >= minimum,
    `${name}: expected at least ${minimum} cases, received ${group.tests}`);
  assert(Array.isArray(group.results) && group.results.length === group.tests, `${name}: incomplete result list`);
  for (const result of group.results) {
    assert(typeof result.name === "string" && result.name.length > 0, `${name}: unnamed result`);
    assert(result.passed === true, `${name}: ${result.name}: ${result.error || "failed"}`);
  }
  assert.equal(group.passed, true, `${name}: fixture reported failure`);
}

// The optional root is solely for synthetic runner fault-injection checks. CLI
// execution always serves the packaged files above; no remote URL is accepted.
export async function runBrowserFixtures({ root = ROOT, executablePath = process.env.HARG_BROWSER_EXECUTABLE, timeoutMs = 120_000 } = {}) {
  const { server, origin } = await fixtureServer(root);
  let browser;
  const report = { schemaVersion: 1, scope: "Standalone synthetic HA DOM fixtures; no installed-native or physical acceptance", suites: [] };
  try {
    browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
    for (const suite of SUITES) {
      const errors = [];
      const context = await browser.newContext({ serviceWorkers: "block" });
      try {
        await context.route("**/*", route => {
          const request = route.request(), url = new URL(request.url());
          if (url.origin === origin && ALLOWED.has(url.pathname) && !url.search && ["GET", "HEAD"].includes(request.method()))
            return route.continue();
          errors.push(`Blocked unexpected ${request.method()} request`);
          return route.abort("blockedbyclient");
        });
        await context.routeWebSocket("**/*", socket => { errors.push("Blocked unexpected WebSocket"); socket.close(); });
        const page = await context.newPage();
        page.on("pageerror", error => errors.push(`Page error: ${error.message}`));
        page.on("console", message => { if (message.type() === "error") errors.push(`Console error: ${message.text()}`); });
        page.on("requestfailed", request => errors.push(`Request failed: ${new URL(request.url()).pathname}`));
        page.on("response", response => { if (response.status() >= 400) errors.push(`HTTP ${response.status()}: ${new URL(response.url()).pathname}`); });
        await page.goto(origin + suite.path, { waitUntil: "load", timeout: 30_000 });
        await page.waitForFunction(name => window[name] !== undefined, suite.global, { timeout: timeoutMs });
        const result = await page.evaluate(name => window[name], suite.global);
        const groups = suite.global === "deviceGroupTestResults" ? { groups: result } : result;
        if (suite.global === "hargassnerFrontendTests") {
          const packageMetadata = JSON.parse(await readFile(resolve(root, "package.json"), "utf8"));
          assert.equal(result.version, packageMetadata.version, "Fixture/module version mismatch");
        }
        for (const [name, minimum] of Object.entries(suite.groups)) validateGroup(name, groups[name], minimum);
        assert.deepEqual(errors, [], `${suite.path}: browser/request errors`);
        report.suites.push({ path: suite.path, results: Object.fromEntries(Object.keys(suite.groups).map(name => [name, groups[name]])) });
      } finally { await context.close(); }
    }
    return report;
  } finally {
    await browser?.close();
    await new Promise((accept, reject) => server.close(error => error ? reject(error) : accept()));
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const report = await runBrowserFixtures();
    console.log(JSON.stringify({ ...report, suites: report.suites.map(suite => ({ path: suite.path,
      results: Object.fromEntries(Object.entries(suite.results).map(([name, group]) => [name, { passed: group.passed, tests: group.tests }])) })) }, null, 2));
  }
  catch (error) { console.error(error.stack || error); process.exitCode = 1; }
}

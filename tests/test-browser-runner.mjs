/** Fault-injection checks for the CI runner, using only generated loopback fixtures. */
import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { runBrowserFixtures } from "../tools/test_browser.mjs";

const complete = `
  const group = (n) => ({passed:true, tests:n, results:Array.from({length:n},(_,i)=>({name:'case '+i,passed:true}))});
  window.hargassnerFrontendTests={version:'1.4.2',popup:group(59),card:group(113)};
  window.deviceGroupTestResults=group(22);
`;
async function fixture(before = "", after = "", options = {}) {
  const root = await mkdtemp(join(tmpdir(), "harg-browser-runner-"));
  try {
    await mkdir(join(root, "tests"));
    await writeFile(join(root, "package.json"), JSON.stringify({ version: "1.4.2" }));
    const html = `<script>${before}</script><script type="module">${complete}${after}</script>`;
    await writeFile(join(root, "tests/test-thermostat-popup.html"), html);
    await writeFile(join(root, "tests/test-device-groups.html"), html);
    return await runBrowserFixtures({ root, timeoutMs: 5_000, ...options });
  } finally { await rm(root, { recursive: true, force: true }); }
}

test("accepts complete passing fixture reports from both pages", async () => {
  const report = await fixture();
  assert.equal(report.suites.length, 2);
  assert.equal(report.suites[0].results.card.tests, 113);
  assert.equal(report.suites[1].results.groups.tests, 22);
});
test("assertion failures fail even when the fixture summary claims success", async () => {
  await assert.rejects(fixture("", "window.hargassnerFrontendTests.popup.results[0].passed=false;"), /popup: case 0/);
});
test("a truncated or empty suite cannot produce a green result", async () => {
  await assert.rejects(fixture("", "window.hargassnerFrontendTests.card=group(1);"), /expected at least 113/);
});
test("uncaught page exceptions fail the run", async () => {
  await assert.rejects(fixture("throw Error('synthetic page error');"), /Page error: synthetic page error/);
});
test("console errors fail the run", async () => {
  await assert.rejects(fixture("console.error('synthetic console error');"), /Console error: synthetic console error/);
});
test("unexpected HTTP requests are blocked before any connection", async () => {
  await assert.rejects(fixture("", "await fetch('http://127.0.0.1:9/no-network').catch(()=>{});"), /Blocked unexpected GET request/);
});
test("WebSocket requests are blocked", async () => {
  await assert.rejects(fixture("", `await new Promise(resolve=>{const socket=new WebSocket('ws://127.0.0.1:9/no-network');socket.onclose=resolve;socket.onerror=resolve;setTimeout(resolve,300);});`), /Blocked unexpected WebSocket/);
});
test("HTTP errors for allowlisted missing fixture resources fail the run", async () => {
  await assert.rejects(fixture("", "await fetch('/tests/device-group-fixture-data.js');"), /HTTP 404/);
});
test("missing completion signals time out instead of passing", async () => {
  await assert.rejects(fixture("", "delete window.hargassnerFrontendTests;", { timeoutMs: 250 }), /Timeout/);
});
test("a stale fixture/module version fails the run", async () => {
  await assert.rejects(fixture("", "window.hargassnerFrontendTests.version='old';"), /Fixture\/module version mismatch/);
});

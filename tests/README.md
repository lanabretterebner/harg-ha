# Portable thermostat checks

Run `npm test` from this repository. The advertised card/popup entrypoints execute the optimistic card, both-circuit six-mode matrix, authority/atomic-context, and popup suites. The response-time cases execute pinned native button/slider handlers with a deterministic elapsed-time clock: immediate requested-number feedback, one trailing +/- send at 250 ms, and immediate slider release. All source-model tests run without HA, browser, network or controller. The bundled official service/debounce bodies remain hash-checked; preserve their notices/license. See [coverage migration](COVERAGE-MIGRATION.md) for the replacement of1.4.1 expiry/remount assertions.

Serve this repository and open `test-thermostat-popup.html` for browser custom-element/card/menu fixtures, and `test-device-groups.html` for grouping. Those use synthetic native handlers. Actual installed HA Lit rendering/API-context consumption needs separate real-native acceptance; a Node pass does not claim that result.

Reconnect regressions use live fixture timer maps and the bundled native service
and debounce bodies. They never invoke a canceled timer. Both circuits cover HA
and Connection flags plus synchronous `disconnected` → `ready` events before a
render, permanent retirement of captured APIs/handlers, fresh gestures on the
same native nodes, and retention of submitted uncertain values. Popup status
uses the same connectivity qualification as the card. `connection-fixture.mjs`
models the verified public HA websocket event contract and records its primary
source URLs; it creates no WebSocket and is not real-native acceptance.

Run the same HTML fixtures automatically with the development-only browser
runner from this package root:

```sh
npm ci
npx playwright install chromium
npm run test:browser
```

On CI use `npx playwright install --with-deps chromium`. An existing executable
can be selected with `HARG_BROWSER_EXECUTABLE=/usr/bin/chromium`. The runner
starts its own loopback HTTP server, uses synthetic card/popup/grouping fixtures
and fails on assertion, page, console or request errors. Its structured report
separates card113, popup59 and groups22 cases. `npm test` stays Node-only; the
Playwright browser dependency is development tooling and is not a runtime asset.
Actual authenticated installed-native HA checks and user-authorized physical
changes remain separate acceptance obligations.

`installed-native-probe.js` is the maintained copy of the corrected bounded
progressive probe from `integration/ha_optimistic_browser/progressive-native-probe.js`.
Its ownership/context/timer tests are in `test-installed-native-probe.mjs` and
run with `npm test`. The original eight-case fixture had incorrect native context
envelopes and unbounded waits; it is historical evidence, not an execution pass.

After authenticated HA loading, verify the installed resource hash/version and
evaluate the function definition. Invoke `startHargassnerNativeProbeV2` without
`await`, supplying `installedVersion`, `verifiedResourceSha256`, one `kind`
(`native`, `stock`, `card`, `popup`), `exercise:false` and bounded `timeoutMs`.
Poll `window.hargassnerNativeProbeV2` in a separate evaluation. Review each
finished mount before an isolated `exercise:true`; do not start a second probe
while one is running. The report's `stop()` removes only its owned fixture.
No real HA object or authentication is copied; connection writes are denied and
literal synthetic temperature calls remain in memory. Save the sanitized report.
Portable model checks do not establish installed-native or physical acceptance.


The 250 ms send path serializes submitted temperature requests. Timing cases
cover the 350 ms follow-on gesture before a 400 ms backend pending receipt,
synchronous same-session cross-surface admission, captured APIs, old state and
timeout rejection, qualified readback and bound rejection. Earlier late-error
cases now provide real fresh confirmed basis before the later gesture; reconnect
alone retains the submitted lock. This is a deliberate control-contract change.
Native browser `inert` input suppression still needs real-HA acceptance.

Recovery cases keep the unknown requested label while admitting a deliberate
new gesture after a bound backend `sent_unconfirmed` receipt and a new coherent
qualified basis. Old basis, split echoes, pending/unbound receipts and offline
state remain blocked. Local timeout or transport status alone is insufficient.


The1.4.4 popup scope cases additionally deliver connection/user A→B→A and
connected→disconnected→ready synchronously before a Lit render. Existing
writable hass descriptors and native hassConnection/hassConfig providers are
both covered. Repeated connected identities preserve250ms drafts; owner-scoped
subscriptions/descriptors release on stop/reparent, and captured old APIs/timers
stay closed without replacing native controls.

`npm run test:browser` also runs eight new popup scope assertions with real
CustomEvent context propagation/DOM and synthetic native handlers. These are
separate from actual HA Lit acceptance. The maintained installed-native probe
now defaults to lifecycleSource native_context (synthetic provider ancestor, no
full hass property invented on the native popup); legacy_hass remains explicit.
It retains mount/cleanup and all former cases, and adds submitted-inert, qualified
uncertainty, reconnect/ABA, lost-response and external-operation scenarios for
root's separately recorded installed verification.

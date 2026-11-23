# Home Assistant test fixtures

`ha-native-bodies.json` redistributes two verbatim TypeScript function bodies from
Home Assistant frontend, tag `20260930.3`, under the Apache License 2.0.
Copyright Home Assistant contributors. The upstream source paths, primary URLs
and full-file SHA-256 digests are preserved in that JSON. The complete license
is included in `Apache-2.0.txt`.

The extracted bodies are the native climate temperature control `_callService`
method and the shared `debounce` implementation. The test mechanically removes
TypeScript annotations; no service or debounce logic is changed. Its fake DOM,
event and timer host is project test code, not a copy of the full HA component.

Extraction was checked against the pinned complete source files locally. Running
the test uses only bundled fixtures and Node built-ins; it fetches no upstream
files and has no browser, HA or controller connection. Native Lit rendering and
actual integration acceptance remain separate.

`ha-native-gesture-bodies.json` also redistributes the same pinned climate
component's `_handleButton`, `_valueChanging`, and `_valueChanged` bodies under
the same Apache License 2.0. These tests mechanically remove TypeScript casts
and exercise the unchanged native gesture logic with a deterministic clock.

`ha-native-202611-bodies.json` redistributes, under the same Apache License 2.0,
the `willUpdate`, `_syncTargetTemperature`, `_valueChanged`, `_valueChanging`,
`_callService` and `_handleButton` bodies of the same climate component and the
complete `PendingValueSync` class (`src/common/util/pending-value-sync.ts`) from
Home Assistant frontend tag `20261028.6` (shipped with Home Assistant 2026.11.2;
both files are byte-identical in `20261028.8`). Copyright Home Assistant
contributors. Source paths, URLs and full-file SHA-256 digests are in that JSON.
`test-native-202611.mjs` removes TypeScript annotations mechanically and changes
no logic.

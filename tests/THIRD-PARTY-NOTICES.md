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

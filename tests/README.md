# Portable popup continuity source-model tests

Copy this directory's contents into the public package `tests/` directory.
Run `node tests/test-popup-continuity.mjs` from any working directory; the default
module is resolved relative to the test as `../dist/hargassner-thermostat-popup.js`.
An explicit module path can be supplied as the first argument.

The 319 scenarios match the original frozen source-model suite. The only harness
changes are relative module lookup and loading locally bundled source-pinned
native function bodies instead of absolute development source paths. No network,
DOM library, browser, HA instance or controller is needed. This does not test real
Lit rendering. Preserve the fixture notice and complete Apache license.

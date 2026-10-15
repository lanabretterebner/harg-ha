# Third-party notices

The popup's embedded SVG path data comes unchanged from Material Design Icons,
`@mdi/js` version7.4.47, by Austin Andrews and the Pictogrammers contributors.
The extracted symbols are `mdiPower`, `mdiCalendarClock`, `mdiWeatherSunny`,
`mdiWeatherNight` and `mdiSunClock`. The wrapper maps them to the six mode
labels; it does not modify the path strings.

The icon data is licensed under **Apache License2.0**. The project's MIT license
covers its own implementation and does not replace the icon license.

- [Official package source](https://github.com/Templarian/MaterialDesign-JS/tree/v7.4.47)
- [Upstream Pictogrammers license notice](licenses/Pictogrammers-Free-License.txt)
- [Full Apache License2.0](licenses/Apache-2.0.txt)
- [Official license text](https://www.apache.org/licenses/LICENSE-2.0.txt)

Source package: `https://registry.npmjs.org/@mdi/js/-/js-7.4.47.tgz`,
SHA-256 `83c7da0af018050b208e61629bbfb549bfad2eed57482e0923eff2eeaeb12d93`.
The extracted `package/mdi.js` is SHA-256
`0edde73c83c07bc4d8675e96aba3a853a1fc8fa2b251e3f5f6dede4d0332ab3f`.
Only five path strings are redistributed; no runtime package/network dependency
is added. Preserve these notices when redistributing the module.

The development-only popup continuity suite also redistributes two verbatim
Home Assistant frontend function bodies under Apache License2.0. Their pinned
upstream sources and complete notices are in
[the test fixture notice](tests/THIRD-PARTY-NOTICES.md), with the complete
[Apache License](tests/Apache-2.0.txt). They are test fixtures and are not
loaded by the production browser module.

## [0.11.1](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.11.0...v0.11.1) (2026-05-18)

### 🐛 Bug Fixes

* **coolify:** disabled custom network to resolve Traefik 504 timeout ([cbb9385](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/cbb93858617c309989dd9ec37abc5c2879bb7f42)), closes [#1815](https://github.com/bauer-group/IP-Shlink-MCPServer/issues/1815) [#5686](https://github.com/bauer-group/IP-Shlink-MCPServer/issues/5686)

## [0.11.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.10.3...v0.11.0) (2026-05-18)

### 🚀 Features

* **network:** switched default MCP bind to dual-stack "::" ([35faca0](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/35faca0dc1184dc0c3ec2d93362f7a06b497a99b))

## [0.10.3](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.10.2...v0.10.3) (2026-05-18)

### 🐛 Bug Fixes

* **deps:** added py-key-value-aio[disk,redis] for OAuth state store ([a930bce](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/a930bcee499717cc3b2446ac13050e722bce8beb))

## [0.10.2](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.10.1...v0.10.2) (2026-05-18)

### 🐛 Bug Fixes

* **auth:** wired persistent encrypted OAuth state storage across all providers ([b611583](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/b6115836429019251cec74514dd1609dd612ea1f))

## [0.10.1](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.10.0...v0.10.1) (2026-05-18)

### 🐛 Bug Fixes

* **docker:** honored MCP_PORT runtime override end-to-end ([65a63db](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/65a63dbc96edce5bfc1f608cc1f532fb15a44d14))

## [0.10.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.9.0...v0.10.0) (2026-05-18)

### 🚀 Features

* **security:** wired token-bucket rate limiter with proxy-aware client keying ([6f3f767](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/6f3f7674cc169efc810384a9a36551c2d3cf6536))

## [0.9.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.8.0...v0.9.0) (2026-05-18)

### 🚀 Features

* **auth:** implemented tenant rejection policy with audit-only mode ([ae294f9](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/ae294f94c518dc003f4eae8728df1b7307a2081a))

## [0.8.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.7.0...v0.8.0) (2026-05-18)

### 🚀 Features

* **auth:** added tenant allowlist middleware for entra-multi ([1874ab2](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/1874ab22e218eade884e6913d8277501e3c14962))

### 🐛 Bug Fixes

* **security:** percent-encoded path placeholders in resource templates ([b603f99](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/b603f99db4ae829319587f3c08a87e20dc156ddf))

### ♻️ Refactoring

* **static:** switched index page to shared /logo.svg asset ([c4bc33d](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/c4bc33d3e0c54b2ee79192508acfd0d6603ad5af))

## [0.7.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.6.4...v0.7.0) (2026-05-18)

### 🚀 Features

* **auth:** branded OAuth consent screen and corrected callback paths ([c7696fe](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/c7696fec25b3091062e4a05922505e61d521a331))

## [0.6.4](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.6.3...v0.6.4) (2026-05-18)

### ♻️ Refactoring

* **docker:** enhance comments and clean up configuration for Coolify deployment ([2d9dc6d](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/2d9dc6d15bc314a8771aae264cc2261eb5a9c70a))

## [0.6.3](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.6.2...v0.6.3) (2026-05-18)

## [0.6.2](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.6.1...v0.6.2) (2026-05-18)

### ♻️ Refactoring

* **docker:** clean up Coolify and development configurations ([e7c8bfb](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/e7c8bfb033382404515fefd9b66cecb74e3c7fa0))

## [0.6.1](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.6.0...v0.6.1) (2026-05-18)

### 🐛 Bug Fixes

* **extensions:** switched export_short_urls to task=required and dropped unused ttl_seconds ([40ce5d8](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/40ce5d85881b66e31290e13219385213c9f1db17))

## [0.6.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.5.3...v0.6.0) (2026-05-18)

### 🚀 Features

* **extensions:** added config-driven prompts, resource templates, and bulk-export tasks ([11643ff](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/11643ff1476e3be8d6ecb564afcb73e13c1847e1))

## [0.5.3](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.5.2...v0.5.3) (2026-05-17)

### 🐛 Bug Fixes

* **shlink:** stripped /rest/v{version} prefix from spec paths ([27857d6](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/27857d6860325ef86b0a2dfc3c464b9514a19d85))

## [0.5.2](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.5.1...v0.5.2) (2026-05-17)

### ♻️ Refactoring

* **scripts:** collapsed dev-inspector to a single build-run flow ([5d6e932](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/5d6e932f85fd87fba21a42b21c1e9d30fe3dae54))

## [0.5.1](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.5.0...v0.5.1) (2026-05-17)

### ♻️ Refactoring

* **scripts:** made dev-inspector build-from-source by default ([31eecaa](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/31eecaada68653862e36eb1234b11ec81fff8809))

## [0.5.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.4.0...v0.5.0) (2026-05-17)

### 🚀 Features

* **spec:** bundled Shlink modular OpenAPI spec at build + runtime ([a2b4e3e](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/a2b4e3e730cc5960431da0e9632fcc85bdbe82b2))

### 🐛 Bug Fixes

* **scripts:** resolved npx executable on Windows in dev-inspector ([6506364](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/6506364b62069e1eb8d22591c4d22ff5bad8abe3))

### ♻️ Refactoring

* **mcp:** adapted tool mapper to FastMCP 3.x APIs ([e27c5d5](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/e27c5d5a102e7dc13fecb3619b05de0bd4220b3c))

## [0.4.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.3.0...v0.4.0) (2026-05-17)

### 🚀 Features

* **auth:** split Entra scopes for fastmcp 3.x and added provisioning scripts ([ec6d75d](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/ec6d75dd2079a293f814c57613fe8a7b8d3bd69f))

## [0.3.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.2.0...v0.3.0) (2026-05-17)

### 🚀 Features

* **security:** added approval gate via per-method MCP annotations ([076a96f](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/076a96fef7d608738f3e238bdffaf5b42b026114))

## [0.2.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.1.0...v0.2.0) (2026-05-17)

### 🚀 Features

* **dev:** added dev-inspector helper for one-command Inspector flows ([495387b](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/495387ba03a27b8633601a8bb593c1d9389fd096))
* **http:** added human-readable landing page at / ([7b289ed](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/7b289ed8dff446e1eb98f98002fd18bd3fffcb05))
* **image:** baked OpenAPI spec at build time, renamed prod stage ([b958cac](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/b958cacfad58ac1b8c1cf4bccf150494b5b96f7e))

## [0.1.0](https://github.com/bauer-group/IP-Shlink-MCPServer/compare/v0.0.0...v0.1.0) (2026-05-17)

### 🚀 Features

* **docker:** added compose stacks for dev, traefik, and coolify ([8f7643d](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/8f7643d65a78c0c94d151cedcaa1661c0d7f468b))
* **mcp:** added bg-shlink-mcp FastMCP server with OIDC + Shlink bridge ([6f377d1](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/6f377d10fa96e8cbe6fbdfaa8c7b526210303b86))

### 🐛 Bug Fixes

* **ci:** aligned release pipeline with BG monorepo pattern ([1873251](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/187325126ee1ce6c4adb1f4e379faa8018c3c87e))
* **ci:** restored pyproject.toml version bump with robust replace config ([50c5125](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/50c5125a0fcaac20f0c4f1e5b0357faae99c76bd))
* **docker:** unignored README.md in app build context ([04db1a2](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/04db1a21ad131f4024f57767845aa57301b3c166))

### ♻️ Refactoring

* **ci:** migrated pyproject version bump to @semantic-release/exec ([7f8710b](https://github.com/bauer-group/IP-Shlink-MCPServer/commit/7f8710b4cd7adb2a6813e8c7c41b0f4e6e4a81c6))

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

# Changelog

## [0.2.0](https://github.com/wpfleger96/SNORE/compare/v0.1.0...v0.2.0) (2026-08-06)


### Features

* **analysis:** import-time breath persistence and two-phase import job ([#156](https://github.com/wpfleger96/SNORE/issues/156)) ([2402b63](https://github.com/wpfleger96/SNORE/commit/2402b632b09198df737db402221dbf4c172e4ef5))
* **analysis:** run analysis as a background job after import ([#172](https://github.com/wpfleger96/SNORE/issues/172)) ([4ec4b55](https://github.com/wpfleger96/SNORE/commit/4ec4b5587d40ff8008d23f7a3d4d0fc421ded56b))
* **auth:** Phase 3 backend — Google OAuth OIDC login and invite signup ([#159](https://github.com/wpfleger96/SNORE/issues/159)) ([cce6378](https://github.com/wpfleger96/SNORE/commit/cce6378cdebce88f3abfeb6548f74cab7be7d268))
* **demo:** auto-create demo user with bundled fixture data ([#180](https://github.com/wpfleger96/SNORE/issues/180)) ([6c797a3](https://github.com/wpfleger96/SNORE/commit/6c797a340730536cc9e8625193876340ef0c7e1a))
* **demo:** Phase 4 read-only demo account ([#165](https://github.com/wpfleger96/SNORE/issues/165)) ([276e014](https://github.com/wpfleger96/SNORE/commit/276e014359da6c76797d5cb4ce8095fa2a8888f6))
* **deploy:** Dockerfile, /health endpoint, and GHCR publish workflow ([#166](https://github.com/wpfleger96/SNORE/issues/166)) ([96fd4b8](https://github.com/wpfleger96/SNORE/commit/96fd4b84303cc55cf384580b339f5ee916a41f9c))
* **import:** make the import pipeline fully background with a jobs panel ([#179](https://github.com/wpfleger96/SNORE/issues/179)) ([aa72d7a](https://github.com/wpfleger96/SNORE/commit/aa72d7ae1e22c5b60c86c5a8c7f00b4362ef90d5))
* **mcp:** add OAuth to streamable-HTTP transport for Claude iOS (PR-C) ([#164](https://github.com/wpfleger96/SNORE/issues/164)) ([843b913](https://github.com/wpfleger96/SNORE/commit/843b9133c1448b493d1af910ae615ae80cea0fd9))
* **mcp:** MCP server with profile-scoped Phase 1 tools over BreathService ([#155](https://github.com/wpfleger96/SNORE/issues/155)) ([3905b3f](https://github.com/wpfleger96/SNORE/commit/3905b3f24dcb555c6def867765353981e8a8704f))
* **mcp:** Stage 2 tuning tools — breath table, windows, epoch comparison ([#160](https://github.com/wpfleger96/SNORE/issues/160)) ([4f8152b](https://github.com/wpfleger96/SNORE/commit/4f8152b8f6f19ae2ad2d4b6d734df872abc6c8a7))
* **mcp:** Stage 3 vision tools — waveform arrays, PNG rendering, CA analysis ([#161](https://github.com/wpfleger96/SNORE/issues/161)) ([d52140b](https://github.com/wpfleger96/SNORE/commit/d52140b46968eb130ee6b46c03a242e1ba8d663b))
* **multiuser:** Phase 1 schema, core auth infrastructure, and ownership plumbing ([#154](https://github.com/wpfleger96/SNORE/issues/154)) ([22a5b13](https://github.com/wpfleger96/SNORE/commit/22a5b13c100e948e43b4a9ceba993e321e35ea84))
* **multiuser:** Phase 2 backend auth core — sessions, guards, invite flow, resource bounds ([#157](https://github.com/wpfleger96/SNORE/issues/157)) ([56c929c](https://github.com/wpfleger96/SNORE/commit/56c929c71a5486786a44080520a97afb4dc92673))
* **ui:** make SNORE logo link to dashboard ([e3701e6](https://github.com/wpfleger96/SNORE/commit/e3701e6303470f7d1b09652fe5bec47433f76e64))
* **ui:** Phase 3 frontend auth UI shell ([#158](https://github.com/wpfleger96/SNORE/issues/158)) ([8b702be](https://github.com/wpfleger96/SNORE/commit/8b702be4531e989969abf75bf5187cd464d6d3d9))
* **users:** self-service account page, admin users UI, and preferences ([#170](https://github.com/wpfleger96/SNORE/issues/170)) ([84eb03a](https://github.com/wpfleger96/SNORE/commit/84eb03a1b6a91c991f3f9ce9fec85a0fb3731bd5))


### Bug Fixes

* **db:** detect replaced database file and reconnect stale engine ([#176](https://github.com/wpfleger96/SNORE/issues/176)) ([067d0ae](https://github.com/wpfleger96/SNORE/commit/067d0aeb763740bc3f2c842cfea39e1b9f3112fc))
* **dev:** rebuild UI before serving in dev/dev-auth recipes ([754fff9](https://github.com/wpfleger96/SNORE/commit/754fff9744c29be9505c4e7ffb510aa972a699a1))
* **dev:** restrict uvicorn reload watcher to src/ only ([fbabe0e](https://github.com/wpfleger96/SNORE/commit/fbabe0e0a2837ec21cfefe13337be37937c23d4e))
* **import:** raise per-upload byte ceiling from 512 MiB to 2 GiB ([6e59311](https://github.com/wpfleger96/SNORE/commit/6e59311f34fc04f708ea228c9340030e602b4dd4))
* **import:** raise upload file-count limit from 500 to 10 000 and make it configurable ([613bc77](https://github.com/wpfleger96/SNORE/commit/613bc7735c74488ec058293269fac117148604a0))
* **import:** resolve EMFILE crash when uploading thousands of files ([6aa150e](https://github.com/wpfleger96/SNORE/commit/6aa150e202921a0a341c0cc387c9fce95f484e22))
* **mcp:** resolve crash and polish tool responses found in live testing ([#173](https://github.com/wpfleger96/SNORE/issues/173)) ([28bceba](https://github.com/wpfleger96/SNORE/commit/28bceba8132d3d2e07f2c41229d749d6869de275))
* **ui:** add required ProfileResponse fields to router test mocks ([#162](https://github.com/wpfleger96/SNORE/issues/162)) ([13f7a7a](https://github.com/wpfleger96/SNORE/commit/13f7a7aefc859e36d1afd3f02bfeba874ea7597c))
* **ui:** hide server-path import panel in multiuser mode ([#163](https://github.com/wpfleger96/SNORE/issues/163)) ([929f7da](https://github.com/wpfleger96/SNORE/commit/929f7da13b0d4abb5ed2950883fd1082c95bdd7b))
* **ui:** wait for router before mounting app to prevent blank invite page ([75174c8](https://github.com/wpfleger96/SNORE/commit/75174c82d2cfb963473c021615cc81637ae51e70))


### Chores

* **deps:** Lock file maintenance ([#144](https://github.com/wpfleger96/SNORE/issues/144)) ([83d2461](https://github.com/wpfleger96/SNORE/commit/83d24615de896efa0b3fe10c6e78d435b987d026))
* **deps:** Lock file maintenance ([#181](https://github.com/wpfleger96/SNORE/issues/181)) ([5a23b39](https://github.com/wpfleger96/SNORE/commit/5a23b399352d086bc7c85066144f72ecf0011692))
* **deps:** Update dependency vitest to v4 ([#175](https://github.com/wpfleger96/SNORE/issues/175)) ([055df64](https://github.com/wpfleger96/SNORE/commit/055df64597879c5042f92318cb3d6db23fd7af49))
* **deps:** Update github-actions (major) ([#139](https://github.com/wpfleger96/SNORE/issues/139)) ([2e2ead6](https://github.com/wpfleger96/SNORE/commit/2e2ead64ad7fa30a7e7f163461a8b5ec0632295b))
* **deps:** Update github-actions (major) ([#177](https://github.com/wpfleger96/SNORE/issues/177)) ([83181e2](https://github.com/wpfleger96/SNORE/commit/83181e298d35db415311739b979f1788356e55f5))
* **deps:** Update pnpm to v11.17.0 ([#149](https://github.com/wpfleger96/SNORE/issues/149)) ([5e3153a](https://github.com/wpfleger96/SNORE/commit/5e3153adaa2b3735f983a1a8be64efb50214415b))
* **deps:** Update pnpm to v11.18.0 ([#174](https://github.com/wpfleger96/SNORE/issues/174)) ([ad9f5c4](https://github.com/wpfleger96/SNORE/commit/ad9f5c46a34b8f9ed2d2fac66ea9d337055b5e46))


### Continuous Integration

* sync release workflow ([8b283cd](https://github.com/wpfleger96/SNORE/commit/8b283cda43127070c1d20ad655b0b767d9e69d29))


### Refactoring

* **analysis:** use pre-reduced day fields in CA compute, drop redundant DTO flag ([#169](https://github.com/wpfleger96/SNORE/issues/169)) ([3b02373](https://github.com/wpfleger96/SNORE/commit/3b023735d8e7a790ec706ea3eaf81645bc36abb3)), closes [#167](https://github.com/wpfleger96/SNORE/issues/167)
* **db:** flip SQLAlchemy sync Session to AsyncSession throughout ([#152](https://github.com/wpfleger96/SNORE/issues/152)) ([cbe7491](https://github.com/wpfleger96/SNORE/commit/cbe74915c736dae92643ee6c2fb1ecdb400237dd))
* **mcp:** split CA analysis into fetch/compute seam, extract shared helpers ([#167](https://github.com/wpfleger96/SNORE/issues/167)) ([d36a74e](https://github.com/wpfleger96/SNORE/commit/d36a74ec358e5f6a6b1ffa355f4614116bb28c65))
* prepare codebase for async migration ([#150](https://github.com/wpfleger96/SNORE/issues/150)) ([d358805](https://github.com/wpfleger96/SNORE/commit/d358805cbcf1d7c68a0c41aa8ad3b915c0fd148d))


### Testing

* eliminate worker-vs-test race in upload temp-dir assertions ([#153](https://github.com/wpfleger96/SNORE/issues/153)) ([1c2ed94](https://github.com/wpfleger96/SNORE/commit/1c2ed94642bf8903722eb81d74f8045afe154826))

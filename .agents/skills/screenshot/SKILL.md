---
name: screenshot
description: Capture ad-hoc screenshots of the SNORE Vue UI against a route-mocked API (no backend needed) and post them to a PR with immutable image URLs. Use after changing files in ui/src/ to demonstrate UI changes.
---

# Screenshot Skill

Captures screenshots of the SNORE Vue 3 frontend using Playwright with mock API data,
then posts them as a PR comment with immutable image URLs. No running backend server
needed — API calls are intercepted by Playwright and served from fixture data.

## When to use

After making changes to any file in `ui/src/`, to visually demonstrate UI changes on a PR.

## Policy (from AGENTS.md "PR Screenshots")

PR screenshots are **ad hoc**: capture only what demonstrates THIS PR's UI changes —
one focused shot per change, each with a caption. Never post the regression battery
(`ui/screenshot.spec.ts`) to a PR; that suite is for local regression eyeballing only.

## Prerequisites

- A PR must exist for the current branch
- UI pnpm dependencies installed (`just web-install`)
- Playwright browser installed (`cd ui && pnpm exec playwright install chromium`) — one-time setup

## Capture

1. Write a throwaway Playwright spec patterned on `ui/screenshot.spec.ts`: import the
   fixtures from `ui/tests/fixtures/api-fixtures`, spread-and-override them inline so
   every new field/state in the diff is exercised, and use `locator.screenshot()` to
   crop to the relevant section when a full page would bury the change.
2. `ui/playwright.config.ts` pins `testMatch: 'screenshot.spec.ts'`, so pair the spec
   with a throwaway config (same `webServer`/`baseURL`/viewport, `testMatch` pointing
   at your spec).
3. Build and run:

   ```bash
   just web-build
   cd ui && pnpm exec playwright test --config=pr-shots.config.ts
   ```

4. Dark mode: click the sidebar toggle (`page.getByText('Dark Mode').click()`);
   include dark variants only where the change is theme-sensitive.
5. Name files with numeric prefixes to control comment order (`01-devices-overview.png`).
6. Delete the throwaway spec/config before committing.

## Post

```bash
bash scripts/post-screenshots.sh <pr-number> <png-dir> body.md
```

Pushes the PNGs to a per-developer orphan branch (`agent-screenshots/<github-username>`)
as git objects and comments on the PR with immutable commit-SHA
`raw.githubusercontent.com` URLs.

`body.md` uses `{{filename}}` placeholders (without `.png`); images not referenced by a
placeholder are appended at the end. If `body.md` is omitted, the comment is a bare
image list — prefer writing one, with one section per change:

```markdown
## Screenshots

1. **Devices overview** — brief caption of what this shot demonstrates

{{01-devices-overview}}
```

## Local regression battery (never posted to PRs)

```bash
just web-build
cd ui && pnpm run test:screenshot
```

Runs `screenshot.spec.ts` (desktop + mobile projects) against `vite preview` on port
4173; PNGs land in `ui/screenshots/` (gitignored). Use it to eyeball UI regressions
locally.

## Troubleshooting

- "Browser not found" → `cd ui && pnpm exec playwright install chromium`
- Screenshots blank/loading → increase waits in the spec
- `post-screenshots.sh` "force-with-lease rejected" → retry (another push to the
  screenshots branch happened concurrently)

## Output

`post-screenshots.sh` prints the PR comment URL on success.

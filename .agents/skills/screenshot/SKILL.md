# Screenshot Skill

Takes screenshots of the SNORE Vue 3 frontend views using Playwright with mock API data,
then posts them as a PR comment with immutable image URLs. No running backend server needed
— API calls are intercepted by Playwright and served from fixture data.

## When to use

After making changes to any file in `ui/src/`, to visually demonstrate UI changes on a PR.

## Prerequisites

- A PR must exist for the current branch
- UI pnpm dependencies installed (`just ui-install`)
- Playwright browser installed (`just ui-playwright-install`) — one-time setup

## Invocation

1. Get the PR number: `gh pr view --json number -q .number`
2. Full flow (build + screenshot + post): `just screenshot-pr <PR_NUMBER>`

Individual steps if needed:

- Build + capture only: `just screenshot`
- Post existing screenshots: `just screenshot-post <PR_NUMBER>`

## What happens

1. Builds the Vue app via `just ui-build`
2. Launches headless Chromium against vite preview server (port 4173)
3. Intercepts all `/api/v1/*` calls with typed fixture data
4. Screenshots: Dashboard, Sessions, Stats, RX History, Session Detail
5. PNGs saved to `ui/screenshots/`
6. Pushes PNGs to orphan `agent-screenshots/<username>` branch as git objects
7. Posts PR comment with embedded images using immutable raw.githubusercontent.com URLs

## Troubleshooting

- "Browser not found" → run `just ui-playwright-install`
- "No PR found" → must be on a branch with an open PR
- Screenshots blank/loading → increase waitForTimeout in screenshot.spec.ts
- "force-with-lease rejected" → retry (another push happened concurrently)

## Comment format

The script posts a basic comment with image embeds. After posting, edit the comment
(`gh api repos/{owner}/{repo}/issues/comments/{id} --method PATCH --field body="..."`)
to use a numbered list with a brief description for each screenshot:

```
## Screenshots

1. **Dashboard** — stat cards, AHI trend chart, usage calendar heatmap, and recent sessions

![dashboard](url)

2. **Sessions** — paginated table with date filters, device info, and AHI values

![sessions](url)
```

## Output

Prints the number of screenshots posted and the PR comment URL.

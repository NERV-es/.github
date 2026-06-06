# NERV-es fork automation

Everything below runs hands-off. One source of truth (`NERV-es/.github`)
governs all five public forks: **atoll, codeburn, cotabby, meetily, voiceink**.

| Concern | How it's automated | Source of truth |
|---|---|---|
| Repo settings, labels, branch protection | **safe-settings** GitHub App, full-sync every 6h | `.github/settings.yml` |
| Dependency updates | **Renovate**, patch/minor auto-merged on green | `default.json` (shared preset) |
| CI / policy workflows | Shared SHA-pinned **reusables** + byte-identical stub callers | `.github/workflows/*-reusable.yml` |
| Release propagation | `mirror-release.yml` mirrors tags/assets on publish | per-fork stub |
| **Upstream code sync** | `upstream-sync-reusable.yml` — daily GitHub-native `merge-upstream` | this repo |
| **Conflict resolution** | `ai-conflict-resolve-reusable.yml` — GitHub Models resolves conflicts into a **draft PR** | this repo |
| **Code review** | `ai-review-reusable.yml` — free GitHub Models advisory review on PRs | this repo |

## Upstream sync (the fork-specific one)

Each fork has a daily `upstream-sync.yml` caller (06:17 UTC + manual dispatch)
that invokes the shared reusable:

- **Clean** fast-forward/merge → applied automatically; any open tracking
  issue is closed.
- **Conflicts** → a single deduped issue labelled `upstream-sync` is
  opened/refreshed with the upstream compare link and resolution steps.
  Nothing is force-applied.

This is the one thing that can't be 100% hands-off: when a fork's own commits
overlap upstream changes, a human (or an agent) must resolve the merge. The
workflow turns invisible drift into a tracked, actionable issue.

> Requires `required_linear_history: false` on `main` so clean upstream merge
> commits can land. This is set in `settings.yml` and enforced by safe-settings.

### Onboarding a new fork
1. Add the repo name to `deployment-settings.yml` `restrictedRepos.include`.
2. Copy any fork's `.github/workflows/upstream-sync.yml` stub (byte-identical).
3. safe-settings applies settings/labels/protection within 6h.

## AI conflict resolution → draft PR (free, GitHub Models)

When the daily upstream sync hits conflicts and labels an issue `upstream-sync`,
each fork's `ai-conflict-resolve.yml` caller fires (also runnable via manual
dispatch). The shared `ai-conflict-resolve-reusable.yml`:

1. Merges the upstream default branch (leaving conflicts in the tree).
2. Runs `scripts/ai_resolve.py`, which asks **GitHub Models**
   (`openai/gpt-4o-mini`, free) to merge each conflicted *text* file under the
   size cap. Binary / oversized / delete-modify / still-marked files are left
   for a human.
3. Opens a **DRAFT** pull request (`ai/upstream-sync` → fork default) whose body
   lists exactly what was auto-resolved and what still needs review.
4. Comments the PR link on the open `upstream-sync` tracking issue.

**It never merges.** The draft PR waits for your review and approval.

## Free AI code review on every PR

Each fork's `ai-review.yml` runs `ai-review-reusable.yml` on `pull_request`. It
sends the (truncated) diff to GitHub Models and posts one advisory review
comment. It's read-only — never a merge gate, never edits code. Bot-authored PRs
are skipped to avoid loops.

### Tokens & cost
Both use the **free** GitHub Models tier via the built-in `GITHUB_TOKEN`
(`permissions: models: read`) where possible. On this free org the default
Actions token is **denied** Models access (HTTP 403) and also cannot push
workflow-file changes, so a `MODELS_TOKEN` repo secret (a classic PAT with
`workflow` scope + Models access) is used as a fallback for both the model calls
and the `git push`. `gpt-4o-mini` caps **requests at 8000 tokens**, so inputs
are size-capped (resolver: 24 KB/file; review: 14 KB of diff). Larger content is
flagged for human review rather than truncated blindly.

> `actions/checkout` must use `persist-credentials: false` in the resolver so
> the `MODELS_TOKEN` PAT (not the Actions-app token) is used on push — otherwise
> GitHub blocks the push of workflow-file changes.

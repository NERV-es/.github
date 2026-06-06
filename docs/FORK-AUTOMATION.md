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

# Safe Settings — org settings governance

**Status: LIVE.** `github/safe-settings` runs in GitHub Actions full-sync mode
(no server) and enforces `.github/settings.yml` on the five public forks every
6 hours. Drift (someone flips a repo setting or branch-protection rule by hand)
is auto-reverted on the next run. Verified by a drift test on 2026-06-06.

- App: `safesettings-nerv` (App ID `3978127`), installed on all NERV-es repos.
- Workflow: `.github/workflows/safe-settings-sync.yml` (schedule + manual).
- Secrets (in this repo): `SAFE_SETTINGS_APP_ID`, `SAFE_SETTINGS_PRIVATE_KEY`.

## What's managed

- **`.github/settings.yml`** — org-wide defaults: repo settings, labels, and
  `main` branch protection. Change a fork's settings by editing this one file.
  To override for a single repo, add `.github/repos/<repo>.yml`.
- **`deployment-settings.yml`** — `restrictedRepos.include` allowlist scoping
  the sync to the 5 **public** forks only (atoll, codeburn, cotabby, meetily,
  voiceink). The org's private repos (NERV, openclaw-config) are intentionally
  excluded: branch protection on private repos is paywalled on a free org and
  would fail the whole sync. **Onboard a new fork by adding its name here**
  (globs allowed, e.g. `my-fork-*`).

## Run it manually

Actions → **Safe Settings Sync** → **Run workflow**.

## Re-creating the GitHub App (if ever needed)

Org → Settings → Developer settings → **GitHub Apps** → **New GitHub App**:

- **Webhook**: uncheck Active (full-sync needs no webhooks).
- **Repository permissions**: Administration RW, Contents RW, Pull requests RW,
  Issues RW, Metadata RO. Optional: Checks RW (see note below), Workflows RW
  (only to manage workflow files).
- **Organization permissions**: Members RO, Administration RO.
- Install on **All repositories**, generate a private key, note the App ID.
- Add `SAFE_SETTINGS_APP_ID` (numeric) and `SAFE_SETTINGS_PRIVATE_KEY` (full
  `.pem` contents) as Actions secrets in this repo.

## Notes / known quirks

- **pino logger patch**: `full-sync.js` in the pinned release calls
  `createProbot()` without a logger (github/safe-settings#955), crashing on
  `probot.log`. The workflow `sed`-patches it to inject a pino logger until the
  upstream fix (PR #961) ships in a tagged release. Revisit when bumping
  `SAFE_SETTINGS_VERSION`.
- **Cosmetic 403**: each run logs one non-fatal
  `Resource not accessible by integration` when posting its own "Safe-Settings"
  check-run, because the App lacks **Checks: write**. The sync still succeeds.
  To silence it, grant the App **Checks: Read and write**.

This mirrors `scripts/repo-sync.sh` + `config/fork-settings.json` in the NERV
repo, which remains usable for ad-hoc, PAT-driven syncs from your machine.

# Safe Settings — one-time setup

`github/safe-settings` is **not** a Marketplace app; it's a self-hosted Probot
app. To avoid running a server, this org uses its **GitHub Actions full-sync
mode**: `.github/workflows/safe-settings-sync.yml` runs every 6 hours (and on
demand) and enforces `.github/settings.yml` on every NERV-es repo.

You only have to do the GitHub App part once.

## 1. Create the GitHub App (NERV-es org)

Settings → Developer settings → **GitHub Apps** → **New GitHub App**
(or: https://github.com/organizations/NERV-es/settings/apps/new)

- **Name**: `nerv-es-safe-settings` (any unique name)
- **Homepage URL**: this repo's URL
- **Webhook**: **uncheck Active** (full-sync mode doesn't need webhooks)
- **Repository permissions**:
  - Administration: **Read and write**
  - Contents: **Read and write**
  - Metadata: **Read-only** (auto)
  - Pull requests: **Read and write**
  - Issues: **Read and write**   (needed for label management)
  - Workflows: **Read and write** (only if you want it to manage workflow files)
- **Organization permissions**:
  - Members: **Read-only**
  - Administration: **Read-only**
- **Where can this app be installed**: Only on this account

Create it, then:
- **Generate a private key** → downloads a `.pem` file.
- Note the numeric **App ID** (top of the App page).
- **Install App** → install on **All repositories** in NERV-es.

## 2. Add two secrets to this repo (NERV-es/.github)

Settings → Secrets and variables → **Actions** → New repository secret:

- `SAFE_SETTINGS_APP_ID`      = the App's numeric ID
- `SAFE_SETTINGS_PRIVATE_KEY` = the full contents of the downloaded `.pem`

## 3. Run it

Actions → **Safe Settings Sync** → **Run workflow**. It will sync all forks to
`.github/settings.yml`. After that it runs automatically every 6 hours.

## What's managed

- `.github/settings.yml` — org-wide defaults (repo settings, labels, `main`
  branch protection). To override for a single repo, add
  `.github/repos/<repo>.yml`.
- `deployment-settings.yml` — repos safe-settings must NOT touch (the three
  admin repos).

This mirrors `scripts/repo-sync.sh` + `config/fork-settings.json` in the NERV
repo, which remains usable for ad-hoc, PAT-driven syncs from your machine.
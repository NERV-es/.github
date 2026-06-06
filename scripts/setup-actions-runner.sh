#!/bin/bash
# setup-actions-runner — register a self-hosted GitHub Actions runner for the
# NERV-es org, so private-repo workflows (which are billing-blocked on GitHub's
# hosted runners) can run for free on your own Linux VM.
#
# Why this exists:
#   NERV-es/NERV is PRIVATE, so its Actions minutes are metered and a billing
#   block stops every private workflow from starting. A self-hosted runner sits
#   outside that meter: register one Linux box and the reusable workflows (which
#   already target `runs-on: self-hosted` for NERV) execute on it for free.
#   Public repos keep using GitHub-hosted runners — only NERV's caller passes
#   `runs-on: self-hosted`, so this changes nothing for the forks.
#
# Where to run:
#   ON the Linux VM that will host the runner (Ubuntu 22.04+ x86_64 or aarch64,
#   e.g. an Oracle Cloud Always-Free Ampere A1 instance). Not on macOS — the
#   reusable's tooling (typos musl binary, etc.) targets Linux.
#
# Auth (one of):
#   * NERV_PR_TOKEN_FILE  — path to a file holding the bobby-claw PAT
#                           (default ~/.config/gh-personal-token). Needs
#                           `admin:org` so it can mint a runner registration
#                           token. This is the same PAT repo-sync.sh uses.
#   * GH_TOKEN / NERV_PR_TOKEN — the PAT inline via env var.
#   * RUNNER_TOKEN        — a pre-minted registration token (from the GitHub UI:
#                           Org → Settings → Actions → Runners → New runner).
#                           Use this if you don't want to put a PAT on the VM.
#
# Usage:
#   bash setup-actions-runner.sh                 # org runner, labels self-hosted,linux,nerv
#   RUNNER_LABELS=self-hosted,linux,nerv,gpu bash setup-actions-runner.sh
#   RUNNER_NAME=oracle-ampere-1 bash setup-actions-runner.sh
#   RUNNER_TOKEN=XXXX bash setup-actions-runner.sh   # skip PAT, use UI token
#
# Idempotent-ish: re-running reconfigures the runner in ./actions-runner.
set -euo pipefail

ORG="${RUNNER_ORG:-NERV-es}"
RUNNER_VERSION="${RUNNER_VERSION:-2.334.0}"
RUNNER_NAME="${RUNNER_NAME:-$(hostname)-$(date +%s)}"
RUNNER_LABELS="${RUNNER_LABELS:-self-hosted,linux,nerv}"
RUNNER_GROUP="${RUNNER_GROUP:-Default}"
WORKDIR="${RUNNER_WORKDIR:-$HOME/actions-runner}"
TOKEN_FILE="${NERV_PR_TOKEN_FILE:-$HOME/.config/gh-personal-token}"

log() { printf '\033[1;36m[runner]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[runner] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Linux" ] || die "run this on the Linux VM, not $(uname -s)."

# --- resolve arch -----------------------------------------------------------
case "$(uname -m)" in
  x86_64|amd64)  RUNNER_ARCH=x64 ;;
  aarch64|arm64) RUNNER_ARCH=arm64 ;;
  *) die "unsupported arch: $(uname -m)" ;;
esac
log "host: $(uname -m) -> runner arch $RUNNER_ARCH"

# --- swap (low-RAM hosts e.g. GCP e2-micro 1GB) -----------------------------
# The runner agent (.NET) plus a checkout can exceed 1GB and OOM-kill the job.
# Add a 2G swapfile when total RAM < 2GB and no swap is configured yet.
TOTAL_KB="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
if [ "${TOTAL_KB:-0}" -lt 2097152 ] && ! swapon --show 2>/dev/null | grep -q .; then
  log "low RAM (${TOTAL_KB}KB) and no swap — creating /swapfile (2G)..."
  if [ ! -e /swapfile ]; then
    sudo fallocate -l 2G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
    sudo chmod 600 /swapfile
    sudo mkswap /swapfile >/dev/null
  fi
  sudo swapon /swapfile || true
  grep -q '^/swapfile' /etc/fstab 2>/dev/null || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

# --- resolve a registration token ------------------------------------------
if [ -z "${RUNNER_TOKEN:-}" ]; then
  PAT="${GH_TOKEN:-${NERV_PR_TOKEN:-}}"
  if [ -z "$PAT" ] && [ -f "$TOKEN_FILE" ]; then
    PAT="$(tr -d '[:space:]' < "$TOKEN_FILE")"
  fi
  [ -n "$PAT" ] || die "no RUNNER_TOKEN and no PAT (set GH_TOKEN, NERV_PR_TOKEN, or $TOKEN_FILE)."
  log "minting org registration token via API..."
  RUNNER_TOKEN="$(curl -fsSL -X POST \
    -H "Authorization: Bearer ${PAT}" \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "https://api.github.com/orgs/${ORG}/actions/runners/registration-token" \
    | sed -n 's/.*"token": *"\([^"]*\)".*/\1/p')"
  [ -n "$RUNNER_TOKEN" ] || die "failed to mint registration token (PAT needs admin:org)."
fi

# --- download the runner ----------------------------------------------------
mkdir -p "$WORKDIR" && cd "$WORKDIR"
TARBALL="actions-runner-linux-${RUNNER_ARCH}-${RUNNER_VERSION}.tar.gz"
if [ ! -x ./config.sh ]; then
  log "downloading runner ${RUNNER_VERSION} (${RUNNER_ARCH})..."
  curl -fsSL -o "$TARBALL" \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/${TARBALL}"
  tar xzf "$TARBALL" && rm -f "$TARBALL"
fi

# --- install OS dependencies (Debian/Ubuntu minimal lacks libicu etc.) -------
if [ -x ./bin/installdependencies.sh ]; then
  log "installing runner OS dependencies..."
  sudo ./bin/installdependencies.sh || log "installdependencies returned non-zero (may already be satisfied)"
fi

# --- configure --------------------------------------------------------------
log "configuring runner '${RUNNER_NAME}' (labels: ${RUNNER_LABELS})..."
./config.sh \
  --url "https://github.com/${ORG}" \
  --token "${RUNNER_TOKEN}" \
  --name "${RUNNER_NAME}" \
  --labels "${RUNNER_LABELS}" \
  --runnergroup "${RUNNER_GROUP}" \
  --work _work \
  --unattended --replace

# --- install as a service (survives reboots) --------------------------------
if command -v systemctl >/dev/null 2>&1; then
  log "installing as a systemd service..."
  sudo ./svc.sh install || die "svc.sh install failed"
  sudo ./svc.sh start
  sudo ./svc.sh status || true
else
  log "no systemd; start manually with: (cd $WORKDIR && ./run.sh)"
fi

log "done. Verify online status with:"
log "  gh api /orgs/${ORG}/actions/runners --jq '.runners[] | {name, status, labels: [.labels[].name]}'"

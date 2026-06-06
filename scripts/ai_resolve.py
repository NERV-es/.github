#!/usr/bin/env python3
"""Resolve git merge-conflict markers using the free GitHub Models API.

Runs after `git merge` has left conflicts in the working tree. For each
conflicted *text* file under the size cap it asks a model to merge the two
sides, writes the result back, and `git add`s it. Anything it can't safely
handle (binary, too large, delete/modify, or markers left behind) is reported
and left for a human. The caller opens a DRAFT pull request from the result —
nothing here ever merges.

Env:
  GH_MODELS_TOKEN  token with models:read (GITHUB_TOKEN works in Actions)
  MODEL            model id (default: openai/gpt-4o-mini)
  MAX_BYTES        skip files larger than this (default: 24000)
  MAX_FILES        resolve at most this many files (default: 25)
  SUMMARY_FILE     write a markdown summary here (for the PR body)
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://models.github.ai/inference/chat/completions"
TOKEN = os.environ.get("GH_MODELS_TOKEN", "").strip()
MODEL = os.environ.get("MODEL", "openai/gpt-4o-mini").strip()
MAX_BYTES = int(os.environ.get("MAX_BYTES", "24000"))
MAX_FILES = int(os.environ.get("MAX_FILES", "25"))
SUMMARY_FILE = os.environ.get("SUMMARY_FILE", "ai_resolve_summary.md")

SYSTEM = (
    "You resolve git merge conflicts. The user sends one file whose contents "
    "include conflict markers: '<<<<<<< HEAD' (OURS — this fork's version), "
    "'=======', and '>>>>>>> upstream' (THEIRS — the upstream project). "
    "Produce the correct merged file: keep this fork's intentional changes AND "
    "incorporate upstream's improvements where they don't conflict with the "
    "fork's intent. Prefer the fork's version for fork-specific config/workflow "
    "files. Remove EVERY conflict marker. Output ONLY the complete final file "
    "contents — no explanations, no markdown code fences."
)


def sh(*args):
    return subprocess.run(args, capture_output=True, text=True)


def conflicted_files():
    out = sh("git", "diff", "--name-only", "--diff-filter=U").stdout
    return [f for f in out.splitlines() if f.strip()]


def is_binary(path):
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(8000)
    except OSError:
        return True


def strip_fences(text):
    s = text.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def call_model(content):
    body = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": content},
        ],
    }).encode()
    req = urllib.request.Request(
        API, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.load(resp)
            return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "0") or 0) or (8 * (attempt + 1))
                print(f"  rate limited, waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            print(f"  HTTP {e.code}: {e.read()[:200]!r}", flush=True)
            return None
        except Exception as e:  # noqa: BLE001
            print(f"  error: {e}", flush=True)
            time.sleep(5)
    return None


def main():
    if not TOKEN:
        print("No GH_MODELS_TOKEN provided", file=sys.stderr)
        return 1

    files = conflicted_files()
    resolved, skipped = [], []
    print(f"{len(files)} conflicted path(s)")

    for path in files:
        if len(resolved) >= MAX_FILES:
            skipped.append((path, "file budget reached"))
            continue
        if not os.path.isfile(path):
            skipped.append((path, "delete/modify conflict — needs human"))
            continue
        if is_binary(path):
            skipped.append((path, "binary"))
            continue
        size = os.path.getsize(path)
        if size > MAX_BYTES:
            skipped.append((path, f"too large ({size}B > {MAX_BYTES}B)"))
            continue
        with open(path, encoding="utf-8", errors="replace") as fh:
            original = fh.read()
        if "<<<<<<<" not in original:
            skipped.append((path, "no text markers — needs human"))
            continue

        print(f"resolving {path} ({size}B)...", flush=True)
        out = call_model(original)
        if not out:
            skipped.append((path, "model call failed"))
            continue
        merged = strip_fences(out)
        if "<<<<<<<" in merged or ">>>>>>>" in merged:
            skipped.append((path, "markers remained — needs human"))
            continue
        if not merged.endswith("\n"):
            merged += "\n"
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(merged)
        sh("git", "add", "--", path)
        resolved.append(path)

    lines = ["## 🤖 AI conflict resolution", ""]
    lines.append(f"Model: `{MODEL}` · resolved **{len(resolved)}**, "
                 f"left for review **{len(skipped)}**.")
    lines.append("")
    if resolved:
        lines.append("### Resolved automatically")
        lines += [f"- `{p}`" for p in resolved]
        lines.append("")
    if skipped:
        lines.append("### Needs your review (not auto-resolved)")
        lines += [f"- `{p}` — {why}" for p, why in skipped]
        lines.append("")
    lines.append("> ⚠️ AI-generated merge. **Review carefully before merging — "
                 "this PR is a draft and will not merge itself.**")
    summary = "\n".join(lines)
    with open(SUMMARY_FILE, "w", encoding="utf-8") as fh:
        fh.write(summary + "\n")
    print(summary)

    # Signal to the workflow whether any markers remain anywhere.
    remaining = sh("git", "grep", "-l", "<<<<<<< HEAD").stdout.strip()
    with open(os.environ.get("GITHUB_OUTPUT", "/dev/null"), "a") as fh:
        fh.write(f"resolved_count={len(resolved)}\n")
        fh.write(f"skipped_count={len(skipped)}\n")
        fh.write(f"markers_remain={'true' if remaining else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

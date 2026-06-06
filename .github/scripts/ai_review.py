#!/usr/bin/env python3
"""Multi-LLM free-tier PR reviewer for NERV.

Fans a unified diff out to every free-tier LLM whose API key is present in the
environment, then posts ONE consolidated review comment on the PR (idempotent —
it edits its own previous comment instead of stacking new ones).

Stdlib only (urllib) so the CI step needs no `pip install` — fast and hermetic.
Every provider call is isolated: one provider erroring (rate-limit, outage, bad
key) never blocks the others or fails the job.

All providers are reached through their OpenAI-compatible /chat/completions
surface, so adding another is just one row in PROVIDERS below.

Driven entirely by env (set in .github/workflows/ai-review.yml):
  GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER   — to post the comment
  DIFF_FILE                                     — path to the unified diff
  <PROVIDER>_API_KEY                            — presence enables that provider
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

# Marker so we can find + update our own comment instead of spamming a new one
# on every push to the PR branch.
MARKER = "<!-- nerv-ai-review -->"

# Diffs are capped before the model call: free tiers have small context windows
# and we'd rather review the first ~48k chars well than overflow and get junk.
MAX_DIFF_CHARS = 28_000  # ~7k tokens — fits free-tier per-minute token limits (e.g. Groq 12k TPM)
HTTP_TIMEOUT = 90

# Each provider is an OpenAI-compatible chat endpoint. `env` is the secret name;
# a provider is simply skipped when its key is absent (the repo's key-gated
# convention, enforced one layer up by the workflow's guard job).
PROVIDERS = [
    {
        # Zero-config default: GitHub Models is free and uses the built-in
        # Actions token (models:read). Always active in CI — GH_MODELS_TOKEN
        # falls back to GITHUB_TOKEN in the workflow. No external key needed.
        "name": "GitHub Models · gpt-4o-mini",
        "env": "GH_MODELS_TOKEN",
        "url": "https://models.github.ai/inference/chat/completions",
        "model": "openai/gpt-4o-mini",
    },
    {
        "name": "Groq · llama-3.3-70b",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
    },
    {
        "name": "Cerebras · gpt-oss-120b",
        "env": "CEREBRAS_API_KEY",
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "model": "gpt-oss-120b",
    },
    {
        "name": "Gemini · 2.0-flash",
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash",
    },
    {
        "name": "OpenRouter · llama-3.3-70b (free)",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
    },
    {
        "name": "Mistral · codestral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "model": "codestral-latest",
    },
]

SYSTEM_PROMPT = (
    "You are a senior staff engineer doing a high-signal pull-request review. "
    "Report ONLY things that genuinely matter: bugs, security vulnerabilities, "
    "logic errors, race conditions, data loss, broken error handling, and "
    "incorrect assumptions. Do NOT comment on style, formatting, naming, or "
    "anything a linter/formatter already handles. Cite the file and (when "
    "visible) the line for each point. Be terse — bullet points, no preamble. "
    "If you find nothing blocking, reply with exactly: 'No blocking issues.'"
)


def http_post_json(url: str, headers: dict, payload: dict, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (trusted URLs)
        return json.loads(resp.read().decode("utf-8"))


def review_with(provider: dict, diff: str) -> str:
    key = os.environ.get(provider["env"], "").strip()
    if not key:
        return ""
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        # Groq/Cerebras sit behind Cloudflare, which 403s (error 1010) the
        # default python-urllib User-Agent. Present a normal UA instead.
        "User-Agent": "nerv-ai-review/1.0 (+https://github.com/NERV-es/NERV)",
    }
    payload = {
        "model": provider["model"],
        "temperature": 0.1,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "Review this pull-request diff:\n\n```diff\n" + diff + "\n```",
            },
        ],
    }
    try:
        body = http_post_json(provider["url"], headers, payload, HTTP_TIMEOUT)
        choices = body.get("choices") or []
        if not choices:
            return f"_provider error: no choices in response — {str(body)[:200]}_"
        msg = choices[0].get("message", {}) or {}
        content = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""
        # Some providers return content as a list of typed parts.
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        content = (content or "").strip()
        return content or "_(empty response)_"
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        return f"_provider error: HTTP {e.code} — {detail}_"
    except Exception as e:  # noqa: BLE001 — never let one provider kill the review
        return f"_provider error: {e}_"


def gh_api(method: str, url: str, token: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8")
        return json.loads(raw) if raw else {}


def upsert_comment(repo: str, pr: str, token: str, body: str) -> None:
    """Edit our prior review comment if present, else create a new one."""
    list_url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments?per_page=100"
    existing_id = None
    try:
        for c in gh_api("GET", list_url, token):
            if MARKER in (c.get("body") or ""):
                existing_id = c["id"]
                break
    except Exception as e:  # noqa: BLE001
        print(f"warning: could not list comments: {e}", file=sys.stderr)

    if existing_id:
        url = f"https://api.github.com/repos/{repo}/issues/comments/{existing_id}"
        gh_api("PATCH", url, token, {"body": body})
        print(f"Updated existing review comment {existing_id}.")
    else:
        url = f"https://api.github.com/repos/{repo}/issues/{pr}/comments"
        gh_api("POST", url, token, {"body": body})
        print("Posted new review comment.")


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR_NUMBER"]
    diff_file = os.environ["DIFF_FILE"]

    max_diff_chars = MAX_DIFF_CHARS
    if os.environ.get("MAX_DIFF_CHARS", "").strip().isdigit():
        max_diff_chars = int(os.environ["MAX_DIFF_CHARS"])

    with open(diff_file, encoding="utf-8", errors="replace") as fh:
        diff = fh.read()

    if not diff.strip():
        print("Empty diff — nothing to review.")
        return 0

    truncated = len(diff) > max_diff_chars
    if truncated:
        diff = diff[:max_diff_chars]

    sections: list[str] = []
    active = [p for p in PROVIDERS if os.environ.get(p["env"], "").strip()]
    if not active:
        print("No provider API keys present — skipping.")
        return 0

    print(f"Active providers: {', '.join(p['name'] for p in active)}")
    for provider in active:
        out = review_with(provider, diff)
        if out:
            sections.append(f"### {provider['name']}\n\n{out}")

    if not sections:
        print("No provider returned a review.")
        return 0

    note = (
        "\n\n> ⚠️ Diff was truncated to the first "
        f"{max_diff_chars:,} characters for the model context."
        if truncated
        else ""
    )
    body = (
        f"{MARKER}\n"
        "## 🤖 Multi-LLM review\n"
        "_Free-tier models, advisory only. Each model reviews independently; "
        "agreement across models is a strong signal._"
        f"{note}\n\n" + "\n\n---\n\n".join(sections)
    )

    upsert_comment(repo, pr, token, body)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

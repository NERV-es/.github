#!/usr/bin/env python3
"""Free-tier fallback PR reviewer for NERV.

Tries free-tier LLMs in priority order (highest free capacity first) and uses the
FIRST one that returns a usable review — a fallback chain, not a fan-out. If the
top provider is rate-limited (429, with Retry-After backoff) or errors, it falls
through to the next. GitHub Models (zero-key, built-in token) is the final,
always-available safety net, so a repo with no external keys still gets reviewed.

Posts ONE idempotent comment (edits its own previous comment via a marker instead
of stacking new ones).

For oversized PRs (diff larger than the top provider's budget) it switches to
chunked review: the diff is split on file boundaries and each chunk is sent to a
DIFFERENT provider (crossprovider, default) — full coverage of large PRs while
spreading free-tier load. Tunable via CHUNK_STRATEGY / MAX_REVIEW_CHUNKS env.

Stdlib only (urllib) so the CI step needs no `pip install` — fast and hermetic.

All providers are reached through their OpenAI-compatible /chat/completions
surface, so adding another is just one row in PROVIDERS below. Each row may set
`max_chars` (its context budget); the diff is truncated to that budget per
provider before the call.

Driven entirely by env (set in the ai-review reusable workflow):
  GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER   — to post the comment
  DIFF_FILE                                     — path to the unified diff
  MAX_DIFF_CHARS                                — global cap (optional override)
  <PROVIDER>_API_KEY                            — presence enables that provider
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import urllib.error
import urllib.request

# Marker so we can find + update our own comment instead of spamming a new one
# on every push to the PR branch.
MARKER = "<!-- nerv-ai-review -->"

# Diffs are capped before the model call: free tiers have small context windows
# and we'd rather review the first ~48k chars well than overflow and get junk.
MAX_DIFF_CHARS = 28_000  # ~7k tokens — fits free-tier per-minute token limits (e.g. Groq 12k TPM)
HTTP_TIMEOUT = 90

# Big-PR handling (env-tunable in the workflow, no code change needed):
#   CHUNK_STRATEGY  keep | sequential | crossprovider   (default crossprovider)
#     keep          — single provider, truncate oversized diff to its budget.
#     sequential    — split a big diff, review every chunk with the SAME top
#                     provider, merge (full coverage, more calls to one provider).
#     crossprovider — split a big diff, send each chunk to a DIFFERENT provider,
#                     merge (full coverage + spreads free-tier load). DEFAULT.
#   MAX_REVIEW_CHUNKS  cap on pieces a big PR is split into (default 4, 2..10).
#   CHUNK_MAX_CHARS    target size per chunk (default 24000).
CHUNK_STRATEGY_DEFAULT = "crossprovider"
MAX_REVIEW_CHUNKS_DEFAULT = 4
CHUNK_MAX_CHARS_DEFAULT = 24_000

# Each provider is an OpenAI-compatible chat endpoint. `env` is the secret name;
# a provider is simply skipped when its key is absent (the repo's key-gated
# convention, enforced one layer up by the workflow's guard job).
#
# ORDER IS THE FALLBACK CHAIN — highest free-tier capacity / reliability first.
# The first provider that returns a usable review wins; the rest are not called.
# GitHub Models is intentionally LAST: it's the zero-key, always-available net
# (built-in Actions token) so even a repo with no external keys gets a review.
# `max_chars` is each provider's diff budget (context window / token limits).
PROVIDERS = [
    {
        # Generous free throughput + large context; reliable.
        "name": "Cerebras · gpt-oss-120b",
        "env": "CEREBRAS_API_KEY",
        "url": "https://api.cerebras.ai/v1/chat/completions",
        "model": "gpt-oss-120b",
        "max_chars": 40_000,
    },
    {
        # Free NIM credits, 128k context, strong 70b; reliable.
        "name": "NVIDIA NIM · llama-3.3-70b",
        "env": "NVIDIA_API_KEY",
        "url": "https://integrate.api.nvidia.com/v1/chat/completions",
        "model": "meta/llama-3.3-70b-instruct",
        "max_chars": 40_000,
    },
    {
        # Fast but low per-minute token budget (~12k TPM) — keep diff smaller.
        "name": "Groq · llama-3.3-70b",
        "env": "GROQ_API_KEY",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        "model": "llama-3.3-70b-versatile",
        "max_chars": 18_000,
    },
    {
        # Workers AI's OpenAI-compatible endpoint needs the account id in the
        # path. `url_account_env` keeps this provider dormant unless BOTH
        # CLOUDFLARE_API_KEY and CLOUDFLARE_ACCOUNT_ID are set.
        "name": "Cloudflare Workers AI · llama-3.3-70b",
        "env": "CLOUDFLARE_API_KEY",
        "url": "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions",
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "url_account_env": "CLOUDFLARE_ACCOUNT_ID",
        "max_chars": 24_000,
    },
    {
        # Command-R+ trial, 128k context.
        "name": "Cohere · command-r-plus",
        "env": "COHERE_API_KEY",
        "url": "https://api.cohere.ai/compatibility/v1/chat/completions",
        "model": "command-r-plus-08-2024",
        "max_chars": 40_000,
    },
    {
        # Code-focused; solid free tier.
        "name": "Mistral · codestral",
        "env": "MISTRAL_API_KEY",
        "url": "https://api.mistral.ai/v1/chat/completions",
        "model": "codestral-latest",
        "max_chars": 28_000,
    },
    {
        # Huge context but frequently 429s on the free tier — lower priority.
        "name": "Gemini · 2.0-flash",
        "env": "GEMINI_API_KEY",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "model": "gemini-2.0-flash",
        "max_chars": 40_000,
    },
    {
        # Free upstream pool is often saturated (429) — near-last resort.
        "name": "OpenRouter · llama-3.3-70b (free)",
        "env": "OPENROUTER_API_KEY",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "max_chars": 28_000,
    },
    {
        # Zero-config FINAL safety net: GitHub Models is free and uses the
        # built-in Actions token (models:read). Always active in CI —
        # GH_MODELS_TOKEN falls back to GITHUB_TOKEN in the workflow. Low daily
        # quota + small token cap, so it's last — but guarantees every repo a
        # review even with no external keys.
        "name": "GitHub Models · gpt-4o-mini",
        "env": "GH_MODELS_TOKEN",
        "url": "https://models.github.ai/inference/chat/completions",
        "model": "openai/gpt-4o-mini",
        "max_chars": 14_000,
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


def review_with(provider: dict, diff: str, budget: int) -> str | None:
    """Call one provider. Returns the review text, or None if it's unavailable
    (no key, missing account id, rate-limited past retries, or any error) so the
    caller can fall through to the next provider in the chain."""
    key = os.environ.get(provider["env"], "").strip()
    if not key:
        return None
    url = provider["url"]
    # Providers whose endpoint embeds an account id (e.g. Cloudflare Workers AI)
    # stay dormant until that id is supplied.
    acct_env = provider.get("url_account_env")
    if acct_env:
        acct = os.environ.get(acct_env, "").strip()
        if not acct:
            return None
        url = url.format(account_id=acct)
    if len(diff) > budget:
        diff = diff[:budget]
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
    # Retry on 429 with Retry-After backoff (mirrors scripts/ai_resolve.py).
    for attempt in range(3):
        try:
            body = http_post_json(url, headers, payload, HTTP_TIMEOUT)
            choices = body.get("choices") or []
            if not choices:
                print(f"  {provider['name']}: no choices — {str(body)[:160]}", flush=True)
                return None
            msg = choices[0].get("message", {}) or {}
            content = msg.get("content") or msg.get("reasoning_content") or msg.get("reasoning") or ""
            # Some providers return content as a list of typed parts.
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            content = (content or "").strip()
            return content or None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = int(e.headers.get("Retry-After", "0") or 0) or (6 * (attempt + 1))
                wait = min(wait, 30)
                print(f"  {provider['name']}: rate limited, waiting {wait}s", flush=True)
                time.sleep(wait)
                continue
            detail = e.read().decode("utf-8", "replace")[:200]
            print(f"  {provider['name']}: HTTP {e.code} — {detail}", flush=True)
            return None
        except Exception as e:  # noqa: BLE001 — fall through to the next provider
            print(f"  {provider['name']}: error — {e}", flush=True)
            return None
    return None


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


def split_diff_by_file(diff: str) -> list[tuple[str, str]]:
    """Split a unified diff into (filename, text) segments on `diff --git`
    boundaries so chunks stay file-coherent rather than cutting mid-hunk."""
    segments: list[tuple[str, str]] = []
    cur: list[str] = []
    name = "(diff)"
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if cur:
                segments.append((name, "".join(cur)))
            cur = [line]
            parts = line.split()
            # `diff --git a/path b/path` -> prefer the b/ path, strip the prefix.
            name = parts[3][2:] if len(parts) >= 4 and parts[3].startswith("b/") else (
                parts[2][2:] if len(parts) >= 3 and parts[2].startswith("a/") else "(file)"
            )
        else:
            cur.append(line)
    if cur:
        segments.append((name, "".join(cur)))
    return segments or [("(diff)", diff)]


def pack_chunks(segments: list[tuple[str, str]], n_chunks: int, cap: int) -> list[dict]:
    """Greedily pack file segments into at most n_chunks pieces, each ~<= cap
    chars. A single file larger than cap is truncated. Returns dicts with the
    chunk `text` and the list of `files` it covers."""
    chunks: list[dict] = []
    cur_text = ""
    cur_files: list[str] = []
    for fname, text in segments:
        if len(text) > cap:
            text = text[:cap]
        # Start a new chunk only if we still have chunk budget left; otherwise
        # keep appending so we never exceed n_chunks.
        if cur_text and len(cur_text) + len(text) > cap and len(chunks) < n_chunks - 1:
            chunks.append({"text": cur_text, "files": cur_files})
            cur_text, cur_files = text, [fname]
        else:
            cur_text += text
            cur_files.append(fname)
    if cur_text:
        chunks.append({"text": cur_text, "files": cur_files})
    return chunks


def single_review(diff: str, active: list[dict], max_diff_chars: int) -> tuple[dict | None, str | None, int, list[str]]:
    """Fallback chain: try providers top-down, return the first usable review."""
    tried: list[str] = []
    for provider in active:
        budget = min(max_diff_chars, provider.get("max_chars", max_diff_chars))
        print(f"Trying {provider['name']} (budget {budget:,} chars)...")
        out = review_with(provider, diff, budget)
        if out:
            return provider, out, budget, tried
        tried.append(provider["name"])
        print("  -> unavailable, falling through.")
    return None, None, max_diff_chars, tried


def review_chunks(chunks: list[dict], active: list[dict], cross: bool, max_diff_chars: int) -> list[dict]:
    """Review each chunk. `cross` spreads chunks across DIFFERENT providers
    (preferring as-yet-unused ones); otherwise it sticks to one provider,
    falling back only if that provider fails. Each chunk independently falls
    through the chain on failure so one bad provider never drops a chunk."""
    results: list[dict] = []
    used: set[str] = set()
    sticky: dict | None = None
    for idx, ch in enumerate(chunks):
        start = idx % len(active)
        rotation = active[start:] + active[:start]
        if cross:
            order = [p for p in rotation if p["name"] not in used] + \
                    [p for p in rotation if p["name"] in used]
        elif sticky is not None:
            order = [sticky] + [p for p in rotation if p["name"] != sticky["name"]]
        else:
            order = rotation
        review = None
        chosen = None
        for p in order:
            budget = min(max_diff_chars, p.get("max_chars", max_diff_chars))
            out = review_with(p, ch["text"], budget)
            if out:
                review, chosen = out, p
                used.add(p["name"])
                if not cross:
                    sticky = p
                break
        results.append({"idx": idx, "files": ch["files"], "provider": chosen, "review": review})
    return results


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    raw = os.environ.get(name, "").strip()
    if raw.isdigit():
        return max(lo, min(hi, int(raw)))
    return default


def main() -> int:
    token = os.environ["GITHUB_TOKEN"]
    repo = os.environ["GITHUB_REPOSITORY"]
    pr = os.environ["PR_NUMBER"]
    diff_file = os.environ["DIFF_FILE"]

    max_diff_chars = MAX_DIFF_CHARS
    if os.environ.get("MAX_DIFF_CHARS", "").strip().isdigit():
        max_diff_chars = int(os.environ["MAX_DIFF_CHARS"])

    strategy = (os.environ.get("CHUNK_STRATEGY", "").strip().lower() or CHUNK_STRATEGY_DEFAULT)
    if strategy not in ("keep", "sequential", "crossprovider"):
        strategy = CHUNK_STRATEGY_DEFAULT
    max_chunks = _env_int("MAX_REVIEW_CHUNKS", MAX_REVIEW_CHUNKS_DEFAULT, 2, 10)
    chunk_cap = _env_int("CHUNK_MAX_CHARS", CHUNK_MAX_CHARS_DEFAULT, 4_000, 120_000)

    with open(diff_file, encoding="utf-8", errors="replace") as fh:
        diff = fh.read()

    if not diff.strip():
        print("Empty diff — nothing to review.")
        return 0

    def _enabled(p: dict) -> bool:
        if not os.environ.get(p["env"], "").strip():
            return False
        acct_env = p.get("url_account_env")
        if acct_env and not os.environ.get(acct_env, "").strip():
            return False
        return True

    active = [p for p in PROVIDERS if _enabled(p)]
    if not active:
        print("No provider API keys present — skipping.")
        return 0

    print(f"Fallback chain: {' -> '.join(p['name'] for p in active)}")

    # Big-PR mode only kicks in when the diff overflows the TOP provider's budget
    # AND chunking is enabled AND we can actually split usefully. Otherwise normal
    # PRs stay a single cheap call.
    top_budget = min(max_diff_chars, active[0].get("max_chars", max_diff_chars))
    needed = math.ceil(len(diff) / chunk_cap)
    do_chunk = (
        strategy != "keep"
        and len(diff) > top_budget
        and needed >= 2
    )
    cross = strategy == "crossprovider"
    if cross and len(active) < 2:
        do_chunk = False  # nothing to spread across — fall back to single

    if do_chunk:
        n_chunks = min(max_chunks, max(2, needed))
        if cross:
            n_chunks = min(n_chunks, len(active))
        chunks = pack_chunks(split_diff_by_file(diff), n_chunks, chunk_cap)
        print(f"Big PR ({len(diff):,} chars) -> {strategy} chunking into {len(chunks)} pieces.")
        results = review_chunks(chunks, active, cross, max_diff_chars)
        sections = []
        ok = 0
        for r in results:
            files = ", ".join(f"`{f}`" for f in r["files"][:6])
            if len(r["files"]) > 6:
                files += f" +{len(r['files']) - 6} more"
            if r["review"] and r["provider"]:
                ok += 1
                sections.append(f"### Part {r['idx'] + 1} — {files} · {r['provider']['name']}\n\n{r['review']}")
            else:
                sections.append(f"### Part {r['idx'] + 1} — {files}\n\n_(no provider available for this chunk)_")
        if ok == 0:
            print("All chunk reviews failed — no review posted.")
            return 0
        body = (
            f"{MARKER}\n"
            "## 🤖 AI review (chunked)\n"
            f"_Large PR ({len(diff):,} chars) split into {len(chunks)} parts "
            f"{'across providers' if cross else 'via ' + (results[0]['provider']['name'] if results[0]['provider'] else 'provider')} "
            "for full coverage — free-tier, advisory only._\n\n"
            + "\n\n---\n\n".join(sections)
        )
    else:
        chosen, review, chosen_budget, tried = single_review(diff, active, max_diff_chars)
        if not review or chosen is None:
            print("All providers in the chain were unavailable — no review posted.")
            return 0
        truncated = len(diff) > chosen_budget
        notes = []
        if tried:
            notes.append("fell back past " + ", ".join(tried))
        if truncated:
            notes.append(f"diff truncated to the first {chosen_budget:,} chars for context")
        footer = (
            f"\n\n---\n_Reviewed by **{chosen['name']}** · free-tier fallback chain, "
            "advisory only"
            + (f" — {'; '.join(notes)}" if notes else "")
            + "._"
        )
        body = f"{MARKER}\n## 🤖 AI review\n\n" + review + footer

    upsert_comment(repo, pr, token, body)

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Org AI PR reviewer (free-tier, fallback chain)

`ai_review.py` + `.github/workflows/ai-review-reusable.yml` give an opted-in repo a
high-signal AI code review on each PR **without paying for Copilot**. One
definition lives here in the public `NERV-es/.github` repo; each repo opts in with
an 8-line stub:

```yaml
# .github/workflows/ai-review.yml in any repo
name: ai-review
on:
  pull_request:
permissions:
  contents: read
  pull-requests: write
  models: read
jobs:
  ai-review:
    uses: NERV-es/.github/.github/workflows/ai-review-reusable.yml@ai-review-v1
    secrets: inherit
```

> **Scope:** the reusable currently allowlists only `NERV-es/NERV` and
> `NERV-es/.github` (defensive `if:` guard on the `review` job). Add a repo to that
> guard *and* drop in the stub to enable it elsewhere.

The reusable checks out the caller (for the diff) **and** this repo at the pinned
tag (for `ai_review.py`), then posts one self-updating comment via the
`<!-- nerv-ai-review -->` marker (PATCH if present, else POST).

## Providers (fallback chain)

`ai_review.py` tries providers **in priority order — highest free-tier capacity
first — and uses the first one that returns a usable review.** It is a fallback
chain, not a fan-out: if the top provider is rate-limited (429, retried with
`Retry-After` backoff) or errors, it falls through to the next. The diff is
truncated to each provider's own `max_chars` budget before the call. All are
OpenAI-compatible `/chat/completions` (Bearer auth).

| # | Provider | Secret name | Model | Diff budget |
| --- | --- | --- | --- | --- |
| 1 | Cerebras | `CEREBRAS_API_KEY` | gpt-oss-120b | 40k |
| 2 | NVIDIA NIM | `NVIDIA_API_KEY` | meta/llama-3.3-70b-instruct | 40k |
| 3 | Groq | `GROQ_API_KEY` | llama-3.3-70b-versatile | 18k |
| 4 | Cloudflare Workers AI | `CLOUDFLARE_API_KEY` **+** `CLOUDFLARE_ACCOUNT_ID` | @cf/meta/llama-3.3-70b-instruct-fp8-fast | 24k |
| 5 | Cohere | `COHERE_API_KEY` | command-r-plus-08-2024 | 40k |
| 6 | Mistral | `MISTRAL_API_KEY` | codestral-latest | 28k |
| 7 | Google Gemini | `GEMINI_API_KEY` | gemini-2.0-flash | 40k |
| 8 | OpenRouter | `OPENROUTER_API_KEY` | llama-3.3-70b-instruct (`:free`) | 28k |
| 9 | **GitHub Models** | *(built-in token)* | openai/gpt-4o-mini | 14k |

**GitHub Models is the always-on final safety net** — the reusable sets
`GH_MODELS_TOKEN` to the built-in `GITHUB_TOKEN` (with `models: read`), so a repo
needs **no secrets at all** to get reviews. It is intentionally **last** in the
chain (low daily quota / small token cap); adding any external free-tier key (set
per-repo via `gh secret set <NAME> --repo NERV-es/<repo>`; org-level secrets don't
reach private repos on the free plan, hence `secrets: inherit`) puts a
higher-capacity model ahead of it.

**Cloudflare Workers AI** is special: its OpenAI-compatible endpoint embeds the
account id in the URL, so it needs **both** `CLOUDFLARE_API_KEY` **and**
`CLOUDFLARE_ACCOUNT_ID` (the 32-hex id from your Cloudflare dashboard URL /
right sidebar). The provider auto-skips until both are present.

Free-tier gotchas baked into the script: Groq/Cerebras 403 (Cloudflare err 1010)
the default urllib User-Agent → a normal UA header is sent; Cerebras free models
are `gpt-oss-120b`/`zai-glm-4.7` (no llama); Groq free tier is ~12k TPM, so its
diff budget is the smallest; Gemini/OpenRouter `:free` often 429 (retried, then
skipped) so they sit near the end. NVIDIA NIM, Cohere (`/compatibility/v1`), and
Cloudflare Workers AI are all OpenAI-compatible and validated live.

## Optional: webhook out to the homelab

When `AGENTGATEWAY_WEBHOOK_URL` is set the reusable's `webhook-out` job also POSTs
an HMAC-SHA256-signed (`X-Hub-Signature-256`) PR payload so the NERV agent fleet
can pick up a deep review. Advisory — never fails the PR. Dormant until the
homelab is reachable from GitHub-hosted runners (cloudflared / tailscale funnel).

```bash
gh secret set AGENTGATEWAY_WEBHOOK_URL    --repo NERV-es/<repo>  # https tunnel URL
gh secret set AGENTGATEWAY_WEBHOOK_SECRET --repo NERV-es/<repo>  # shared HMAC secret
```

Receiver side lives in the NERV repo: `scripts/github-webhook-receiver.py`
(stdlib-only) verifies the HMAC with `NERV_WEBHOOK_SECRET`
(== `AGENTGATEWAY_WEBHOOK_SECRET`) and enqueues a `task.discover` on the
agent-bus (spools to disk if the bus is down). Manifest:
`services/github-webhook-receiver.json`.

## Cutting a new version

`ai-review-reusable.yml` checks out this repo at `reviewer_ref` (default
`ai-review-v1`) to fetch the script, so the tag and the file stay in lockstep.
After changing the script or workflow, move/recreate the tag (or bump to
`ai-review-v2` and update the stubs).

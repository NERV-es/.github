# NERV-es

Governance, agentic infrastructure, and tooling.

This is the organization-level `.github` repository. It provides org-wide defaults
that cascade to every repo in **NERV-es** that doesn't define its own:

- **Community health** — `SECURITY.md`, `CONTRIBUTING.md`, `CODEOWNERS`, issue/PR templates
- **Dependabot** — shared `dependabot.yml` baseline
- **Reusable CI** — the canonical quality gate lives in
  [`NERV-es/NERV/.github/workflows/lint-reusable.yml`](https://github.com/NERV-es/NERV/blob/main/.github/workflows/lint-reusable.yml);
  each repo calls it from an 8-line stub.

> One place to tweak universal things. Edit here, inherit everywhere.

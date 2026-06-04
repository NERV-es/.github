# Contributing to NERV-es

## Branch & PR flow

- Work on a feature branch — **never push directly to `main`**.
- Open a PR; CI (the shared quality gate) runs automatically.
- Conventional-commit style is preferred (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).

## Quality gate

Every repo inherits one reusable workflow,
[`lint-reusable.yml`](https://github.com/NERV-es/NERV/blob/main/.github/workflows/lint-reusable.yml).
It auto-detects languages and runs the right linters:

| Language | Linter |
| --- | --- |
| Python | ruff |
| JS/TS | Biome |
| Shell | ShellCheck |
| YAML | yamllint |
| C/C++ | cppcheck |
| Swift | SwiftLint |
| **all** | **gitleaks (always hard-fails)** |

Style findings are **advisory** (they annotate, they don't block). Secret
detection is **never** advisory.

To run the same checks locally before pushing, use the repo's
`ops/scripts/quality/` hooks where present.

## Adding CI to a new repo

Drop this 8-line stub at `.github/workflows/quality.yml`:

```yaml
name: Quality
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  quality:
    uses: NERV-es/NERV/.github/workflows/lint-reusable.yml@main
```

That's the whole integration. No per-repo linter config to maintain.

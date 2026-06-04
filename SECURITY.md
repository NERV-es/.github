# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in any **NERV-es** repository, please
report it privately:

1. Use GitHub's **[Report a vulnerability](https://github.com/NERV-es/NERV/security/advisories/new)**
   (Security → Advisories) on the affected repo, **or**
2. Open a private security advisory on this org.

Please **do not** open a public issue for security-sensitive reports.

### What to expect

- **Acknowledgement** within 72 hours.
- An assessment and, if accepted, a remediation timeline.
- Credit in the advisory once a fix ships (unless you prefer to remain anonymous).

## Supported Versions

These repositories track `main`. Security fixes land on `main` and are not
backported to historical tags unless explicitly noted in a repo's own
`SECURITY.md`.

## Scope

Secret scanning (gitleaks) is enforced in CI and **always hard-fails** — never
commit credentials. If you believe a secret was committed, treat it as
compromised, rotate it, and report per the process above.

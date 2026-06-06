#!/usr/bin/env python3
"""Stub/SHA drift enforcer for NERV-es.

Self-discovering: for every `*-reusable.yml` in NERV-es/.github it computes the
canonical SHA (the latest commit that touched that file), then scans every repo
in the org for thin-stub callers that `uses:` a reusable, and re-pins any whose
pinned SHA has drifted from canonical. Idempotent — a clean org is a no-op.

Auth: reads GH_TOKEN from the env (a Bobby-Claws app installation token scoped
org-wide). No external deps — stdlib urllib only.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
ORG = "NERV-es"
ADMIN_REPO = ".github"
TOKEN = os.environ["GH_TOKEN"]
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"

# Matches: NERV-es/.github/.github/workflows/<name>.yml@<40-hex>
REF_RE = re.compile(
    r"NERV-es/\.github/\.github/workflows/([A-Za-z0-9._-]+\.ya?ml)@([0-9a-f]{40})"
)


def req(method, path, body=None):
    url = path if path.startswith("http") else f"{API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Authorization", f"Bearer {TOKEN}")
    r.add_header("Accept", "application/vnd.github+json")
    r.add_header("X-GitHub-Api-Version", "2022-11-28")
    r.add_header("User-Agent", "bobby-claws-drift-enforcer")
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, json.loads(resp.read() or "null"), resp.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null"), e.headers


def paginate(path):
    out, url = [], f"{API}{path}"
    while url:
        status, data, headers = req("GET", url)
        if status != 200:
            print(f"::warning::GET {url} -> {status}")
            break
        out.extend(data)
        url = None
        for part in (headers.get("Link") or "").split(","):
            if 'rel="next"' in part:
                url = part[part.find("<") + 1 : part.find(">")]
    return out


def canonical_shas():
    """name -> latest commit SHA touching that reusable file."""
    status, items, _ = req(
        "GET", f"/repos/{ORG}/{ADMIN_REPO}/contents/.github/workflows"
    )
    if status != 200:
        sys.exit(f"cannot list reusables: {status} {items}")
    canon = {}
    for it in items:
        name = it["name"]
        if not name.endswith("-reusable.yml"):
            continue
        path = f".github/workflows/{name}"
        st, commits, _ = req(
            "GET", f"/repos/{ORG}/{ADMIN_REPO}/commits?path={path}&per_page=1"
        )
        if st == 200 and commits:
            canon[name] = commits[0]["sha"]
    return canon


def list_workflow_files(repo):
    status, items, _ = req("GET", f"/repos/{ORG}/{repo}/contents/.github/workflows")
    if status != 200:
        return []
    return [
        it["name"]
        for it in items
        if it["type"] == "file" and it["name"].endswith((".yml", ".yaml"))
    ]


def repin_file(repo, branch, wf, canon):
    path = f".github/workflows/{wf}"
    status, meta, _ = req("GET", f"/repos/{ORG}/{repo}/contents/{path}?ref={branch}")
    if status != 200:
        return []
    blob_sha = meta["sha"]
    content = base64.b64decode(meta["content"]).decode("utf-8", "replace")

    changes, new_content = [], content
    for name, pinned in set(REF_RE.findall(content)):
        target = canon.get(name)
        if target and pinned != target:
            new_content = new_content.replace(
                f"workflows/{name}@{pinned}", f"workflows/{name}@{target}"
            )
            changes.append((name, pinned[:8], target[:8]))

    if not changes:
        return []
    if DRY_RUN:
        return changes

    short = ", ".join(f"{n}->{t}" for n, _, t in changes)
    msg = (
        f"ci: re-pin {wf} to canonical reusable SHA(s) [{short}]\n\n"
        "Automated by stub-drift-enforcer (Bobby-Claws app).\n\n"
        "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
    )
    st, resp, _ = req(
        "PUT",
        f"/repos/{ORG}/{repo}/contents/{path}",
        {
            "message": msg,
            "content": base64.b64encode(new_content.encode()).decode(),
            "sha": blob_sha,
            "branch": branch,
        },
    )
    if st not in (200, 201):
        print(f"::warning::PUT {repo}/{path} -> {st} {resp}")
        return []
    return changes


def main():
    canon = canonical_shas()
    print(f"Canonical reusables ({len(canon)}):")
    for n, s in sorted(canon.items()):
        print(f"  {n} @ {s[:8]}")

    repos = paginate(f"/orgs/{ORG}/repos?per_page=100&type=all")
    total_fixed, summary_rows = 0, []
    for r in repos:
        repo, branch = r["name"], r["default_branch"]
        if r.get("archived"):
            continue
        for wf in list_workflow_files(repo):
            for name, old, new in repin_file(repo, branch, wf, canon):
                total_fixed += 1
                tag = "WOULD FIX" if DRY_RUN else "FIXED"
                line = f"{tag}: {repo}/{wf}  {name}  {old} -> {new}"
                print(line)
                summary_rows.append(line)

    sm = os.environ.get("GITHUB_STEP_SUMMARY")
    if sm:
        with open(sm, "a") as f:
            f.write("## Stub drift enforcer\n\n")
            f.write(f"- Repos scanned: **{len(repos)}**\n")
            f.write(f"- Canonical reusables: **{len(canon)}**\n")
            f.write(
                f"- Stubs {'that would be ' if DRY_RUN else ''}re-pinned: **{total_fixed}**\n\n"
            )
            if summary_rows:
                f.write("```\n" + "\n".join(summary_rows) + "\n```\n")
            else:
                f.write("All stubs already pinned to canonical SHAs. ✅\n")

    print(
        f"\nDone. {total_fixed} stub(s) "
        f"{'would be ' if DRY_RUN else ''}re-pinned across {len(repos)} repos."
    )


if __name__ == "__main__":
    main()

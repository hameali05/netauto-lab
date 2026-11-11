#!/usr/bin/env python3
"""Deploy configs/<router>/frr.conf to the routers in the lab.

Pipeline:  git push -> CI green (Batfish) -> THIS SCRIPT -> verify.py

For each router (list taken from NetBox, role "router"):
  1. copy configs/<router>/frr.conf into the container (docker cp)
  2. --dry-run : show what would change (frr-reload.py --test), stop there
     normal    : apply only the differences (frr-reload.py --reload),
                 then check again: there must be no difference left.

Before a real deploy, a CI gate asks GitHub if the "Batfish checks" run
for the current commit is green. The FRR image has no SSH server, so we
talk to the routers with "docker", not SSH.

Usage:  .venv/bin/python deploy.py --dry-run
        .venv/bin/python deploy.py
        .venv/bin/python deploy.py --skip-ci-check
Needs:  NetBox running, .env with NETBOX_URL and NETBOX_TOKEN (never printed),
        the lab running (containers clab-two-sites-<router>).
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

import requests
from nornir.core import Nornir
from nornir.core.task import Result, Task
from nornir.plugins.runners import ThreadedRunner
from nornir_netbox.plugins.inventory import NetBoxInventory2

from netbox.populate import load_env  # same .env reader as Phase 2

ROOT = Path(__file__).resolve().parent
CONFIG_DIR = ROOT / "configs"
LAB_NAME = "two-sites"                      # from two-sites.clab.yml
WORKFLOW_FILE = "ci.yml"                    # .github/workflows/ci.yml
RELOAD = "/usr/lib/frr/frr-reload.py"       # FRR's official reload tool
TMP_CONF = "/tmp/frr.conf.new"              # where we copy the file first
LIVE_CONF = "/etc/frr/frr.conf"             # the file FRR loads at start


# ------------------------------------------------------------------
# 1. Small helpers to run commands
# ------------------------------------------------------------------
def run(cmd):
    """Run a command on the laptop. Return its output, or raise on error."""
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed: "
                           f"{(p.stderr or p.stdout).strip()}")
    return p.stdout


def git(*args):
    return run(["git", "-C", str(ROOT), *args]).strip()


# ------------------------------------------------------------------
# 2. CI gate: is the current commit green on GitHub?
# ------------------------------------------------------------------
def github_repo():
    """'owner/repo' from the git remote (git@github.com:o/r.git or https)."""
    url = git("remote", "get-url", "origin")
    m = re.search(r"github\.com[:/]([^/]+)/(.+?)(?:\.git)?$", url)
    if not m:
        sys.exit(f"ERROR: origin is not a GitHub repo: {url}")
    return f"{m.group(1)}/{m.group(2)}"


def ci_gate():
    """Stop the script unless CI passed for exactly what we deploy."""
    # a) configs/ must be committed: CI only tested committed files
    if git("status", "--porcelain", "--", "configs/"):
        sys.exit("CI GATE: configs/ has uncommitted changes. CI never "
                 "tested them. Commit + push first.")

    # b) ask the GitHub public API (no token needed: public repo)
    sha = git("rev-parse", "HEAD")
    repo = github_repo()
    url = (f"https://api.github.com/repos/{repo}/actions/workflows/"
           f"{WORKFLOW_FILE}/runs")
    try:
        r = requests.get(
            url,
            params={"head_sha": sha, "branch": "main", "event": "push"},
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "netauto-lab-deploy"},
            timeout=10,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        sys.exit(f"CI GATE: cannot reach the GitHub API ({e}).\n"
                 f"  Offline? Use --skip-ci-check (at your own risk).")

    runs = r.json().get("workflow_runs", [])
    short = sha[:7]
    if not runs:
        sys.exit(f"CI GATE: no CI run found for commit {short} on main. "
                 f"Did you git push?")

    latest = runs[0]                     # GitHub returns the newest first
    if latest["status"] != "completed":
        sys.exit(f"CI GATE: CI is still running for {short} "
                 f"({latest['status']}). Wait and try again.\n"
                 f"  {latest['html_url']}")
    if latest["conclusion"] != "success":
        sys.exit(f"CI GATE: CI FAILED for {short} "
                 f"({latest['conclusion']}). Not deploying.\n"
                 f"  {latest['html_url']}")
    print(f"CI GATE: OK, commit {short} is green on GitHub.")


# ------------------------------------------------------------------
# 3. Inventory: the routers, from NetBox
# ------------------------------------------------------------------
def build_nornir():
    env = load_env(ROOT / ".env")
    inventory = NetBoxInventory2(
        nb_url=env["NETBOX_URL"],
        nb_token=env["NETBOX_TOKEN"],
        filter_parameters={"role": "router"},
    )
    # nornir_netbox 0.3.0 always sends "Token <token>". New NetBox tokens
    # (nbt_...) must be sent as "Bearer <token>" (pynetbox does this too).
    if env["NETBOX_TOKEN"].startswith("nbt_"):
        inventory.session.headers["Authorization"] = \
            f"Bearer {env['NETBOX_TOKEN']}"
    try:
        inv = inventory.load()
    except ValueError as e:
        sys.exit(f"ERROR: {e} (is NetBox running? is the token OK?)")
    if not inv.hosts:
        sys.exit("ERROR: no devices with role 'router' found in NetBox")
    return Nornir(inventory=inv, runner=ThreadedRunner(num_workers=4))


# ------------------------------------------------------------------
# 4. The task: runs once per router, in parallel
# ------------------------------------------------------------------
def to_changes(lines):
    """Turn frr-reload blocks into one line per real change.

    frr-reload prints each change as a full block, e.g.
        interface eth1
         ip address 10.1.12.1/30
        exit
    We keep the path of each line ("interface eth1 > ip address ..."),
    drop the "exit" lines, and drop paths that only open a section
    (a path that is the start of another path).
    """
    paths, stack = [], []                # stack = [(indent, text), ...]
    for line in lines:
        text = line.strip()
        if text == "exit":
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()                  # back to the right parent
        stack.append((indent, text))
        path = " > ".join(t for _, t in stack)
        if path not in paths:
            paths.append(path)
    return [p for p in paths
            if not any(o.startswith(p + " > ") for o in paths)]


def parse_test(output):
    """frr-reload.py --test output -> (changes to delete, changes to add)."""
    delete, add, section = [], [], None
    for line in output.splitlines():
        if line.strip() == "Lines To Delete":
            section = delete
        elif line.strip() == "Lines To Add":
            section = add
        elif section is not None and line.strip() \
                and not set(line.strip()) == {"="}:
            section.append(line)
    return to_changes(delete), to_changes(add)


def deploy_router(task: Task, dry_run: bool) -> Result:
    name = task.host.name                        # e.g. "r1"
    container = f"clab-{LAB_NAME}-{name}"        # e.g. "clab-two-sites-r1"
    local_conf = CONFIG_DIR / name / "frr.conf"
    if not local_conf.exists():
        raise RuntimeError(f"{local_conf.relative_to(ROOT)} not found")

    def in_router(*cmd):
        return run(["docker", "exec", container, *cmd])

    # a) copy the file into the router + see the differences
    run(["docker", "cp", str(local_conf), f"{container}:{TMP_CONF}"])
    delete, add = parse_test(in_router("python3", RELOAD, "--test", TMP_CONF))
    changed = bool(delete or add)

    # b) real deploy: install the file, apply, check again
    if not dry_run and changed:
        in_router("cp", TMP_CONF, LIVE_CONF)     # survives a restart
        in_router("chown", "frr:frr", LIVE_CONF)
        in_router("python3", RELOAD, "--reload", LIVE_CONF)
        left_del, left_add = parse_test(
            in_router("python3", RELOAD, "--test", LIVE_CONF))
        if left_del or left_add:
            raise RuntimeError("after reload the router still differs from "
                               "the file:\n" + "\n".join(
                                   [f"- {l}" for l in left_del]
                                   + [f"+ {l}" for l in left_add]))

    return Result(host=task.host, changed=changed,
                  result={"delete": delete, "add": add})


# ------------------------------------------------------------------
# 5. Print one line per router (+ the differences)
# ------------------------------------------------------------------
def report(results, dry_run):
    failed = False
    print()
    for name in sorted(results):
        multi = results[name]
        if multi.failed:
            failed = True
            print(f"{name:5} FAILED     {multi.exception}")
            continue
        diff = multi[0].result
        if not multi[0].changed:
            print(f"{name:5} NO CHANGE")
            continue
        verb = "WOULD CHANGE" if dry_run else "CHANGED"
        print(f"{name:5} {verb:12} (+{len(diff['add'])} / "
              f"-{len(diff['delete'])} changes)")
        for line in diff["delete"]:
            print(f"        - {line}")
        for line in diff["add"]:
            print(f"        + {line}")
    print()
    return failed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="show the changes, change nothing")
    parser.add_argument("--skip-ci-check", action="store_true",
                        help="deploy even if CI is not green (risky)")
    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN: nothing will be changed on the routers.")
    elif args.skip_ci_check:
        print("WARNING: --skip-ci-check: deploying WITHOUT the CI gate.")
    else:
        ci_gate()

    nr = build_nornir()
    print(f"Routers from NetBox: {', '.join(sorted(nr.inventory.hosts))}")
    results = nr.run(task=deploy_router, dry_run=args.dry_run)

    failed = report(results, args.dry_run)
    if failed:
        sys.exit("Done, with ERRORS (see FAILED above).")
    print("Done: dry run finished." if args.dry_run
          else "Done: all routers deployed and checked.")


if __name__ == "__main__":
    main()

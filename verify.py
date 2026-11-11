#!/usr/bin/env python3
"""Check that the lab network really works, and print a PASS/FAIL table.

Pipeline:  ... -> deploy.py -> THIS SCRIPT

EXPECTED state = from NetBox (devices, IPs, OSPF/BGP config contexts).
REAL state     = from the routers ("show ... json") and the PCs (ping).
Nothing is hard-coded: if NetBox changes, the checks change with it.
Read-only: only "show" commands and pings, nothing is changed.

Checks:
  1. OSPF neighbours are Full       4. BGP receives the peer's prefixes
  2. OSPF passive where expected    5. every router has all loopbacks + LANs
  3. BGP sessions Established       6. every PC pings every other PC

Usage:  .venv/bin/python verify.py
        .venv/bin/python verify.py --wait 60   (retry failed checks, max 60 s)
Needs:  NetBox running, .env with NETBOX_URL and NETBOX_TOKEN (never printed),
        the lab running.
"""
import argparse
import ipaddress
import json
import re
import sys
import time
from pathlib import Path

import pynetbox

from deploy import LAB_NAME, build_nornir, run   # same inventory + helpers
from netbox.populate import load_env

ROOT = Path(__file__).resolve().parent
RETRY_EVERY = 5                       # seconds between two tries (--wait)


def container(name):
    return f"clab-{LAB_NAME}-{name}"   # e.g. clab-two-sites-r1


# ------------------------------------------------------------------
# 1. EXPECTED state, built from NetBox
# ------------------------------------------------------------------
def device_ips(nb, device):
    """{interface name: ip_interface} for one device, e.g. {'lo': 1.1.1.1/32}."""
    return {ip.assigned_object.name: ipaddress.ip_interface(ip.address)
            for ip in nb.ipam.ip_addresses.filter(device_id=device.id)}


def build_expected(nb):
    routers = {d.name: d for d in nb.dcim.devices.filter(role="router")}
    pcs = {d.name: d for d in nb.dcim.devices.filter(role="pc")}
    ips = {name: device_ips(nb, d) for name, d in {**routers, **pcs}.items()}
    ctx = {name: (d.config_context or {}) for name, d in routers.items()}

    # Every router must reach all loopbacks and all PC LANs
    loopbacks = {ips[r]["lo"].network for r in routers if "lo" in ips[r]}
    lans = {ip.network for pc in pcs for ip in ips[pc].values()}
    must_have_routes = sorted(str(p) for p in loopbacks | lans)

    expected = {}
    for r in routers:
        ospf = ctx[r].get("ospf") or {}
        bgp = ctx[r].get("bgp") or {}
        exp = {"ospf_neighbors": [], "passive": {}, "bgp": [],
               "routes": must_have_routes}

        for ifname, cfg in (ospf.get("interfaces") or {}).items():
            if ifname == "lo":
                continue                     # loopback: no neighbour, no passive
            passive = bool(cfg.get("passive", False))
            exp["passive"][ifname] = passive
            if passive or ifname not in ips[r]:
                continue                     # passive = no neighbour expected
            # neighbour = the other OSPF router with an IP in the same subnet
            subnet = ips[r][ifname].network
            for other in routers:
                other_ospf = (ctx[other].get("ospf") or {})
                for oif, oip in ips[other].items():
                    if other != r and oip.ip in subnet \
                            and oif in (other_ospf.get("interfaces") or {}):
                        exp["ospf_neighbors"].append(
                            {"rid": other_ospf["router_id"], "iface": ifname})

        for nbr in bgp.get("neighbors") or []:
            # the peer = the router that owns the neighbour IP
            peer = next((o for o in routers for oip in ips[o].values()
                         if str(oip.ip) == nbr["ip"]), None)
            peer_nets = ((ctx[peer].get("bgp") or {}).get("networks")
                         if peer else None)
            exp["bgp"].append({"ip": nbr["ip"], "remote_as": nbr["remote_as"],
                               "prefixes": len(peer_nets) if peer_nets else None})
        expected[r] = exp

    pings = [{"src": src, "dst": dst, "ip": str(ip.ip)}
             for src in pcs for dst in pcs if src != dst
             for ip in ips[dst].values()]
    return expected, pings


# ------------------------------------------------------------------
# 2. REAL state: the router checks (Nornir task, runs on each router)
# ------------------------------------------------------------------
def vtysh_json(name, command):
    return json.loads(run(["docker", "exec", container(name),
                           "vtysh", "-c", f"{command} json"]))


def check_router(task, expected):
    name = task.host.name
    exp = expected[name]
    rows = []                                # (check, ok, detail)

    def add(check, ok, detail):
        rows.append((check, ok, detail))

    # 1. OSPF neighbours
    if exp["ospf_neighbors"]:
        nbrs = vtysh_json(name, "show ip ospf neighbor").get("neighbors", {})
        for e in exp["ospf_neighbors"]:
            check = f"OSPF neighbor {e['rid']} ({e['iface']})"
            entries = nbrs.get(e["rid"], [])
            on_iface = [n for n in entries
                        if n.get("ifaceName", "").split(":")[0] == e["iface"]]
            if not on_iface:
                add(check, False, "not seen")
            else:
                state = on_iface[0].get("converged", "?")
                add(check, state == "Full", state)

    # 2. OSPF passive
    if exp["passive"]:
        ifaces = vtysh_json(name, "show ip ospf interface").get("interfaces", {})
        for ifname, want in exp["passive"].items():
            check = f"OSPF passive {ifname}" if want else f"OSPF active {ifname}"
            if ifname not in ifaces:
                add(check, False, "OSPF not running on it")
                continue
            real = ifaces[ifname].get("timerPassiveIface", False)
            add(check, real == want, "passive" if real else "sends hellos")

    # 3 + 4. BGP session and prefixes received
    if exp["bgp"]:
        peers = (vtysh_json(name, "show bgp summary")
                 .get("ipv4Unicast", {}).get("peers", {}))
        for e in exp["bgp"]:
            p = peers.get(e["ip"])
            check = f"BGP {e['ip']} AS{e['remote_as']}"
            if p is None:
                add(check, False, "neighbor not configured")
                add(f"BGP prefixes from {e['ip']}", False, "no session")
                continue
            ok = p.get("state") == "Established" \
                and p.get("remoteAs") == e["remote_as"]
            add(check, ok, f"{p.get('state')}, AS{p.get('remoteAs')}")
            got = p.get("pfxRcd", 0)
            want = e["prefixes"]
            ok = got == want if want is not None else got > 0
            add(f"BGP prefixes from {e['ip']}", ok,
                f"{got} received (expected {want if want is not None else '>0'})")

    # 5. routes to all loopbacks + LANs
    table = vtysh_json(name, "show ip route")
    missing = [p for p in exp["routes"]
               if not any(r.get("selected") for r in table.get(p, []))]
    have = len(exp["routes"]) - len(missing)
    add(f"routes {have}/{len(exp['routes'])}", not missing,
        "all present" if not missing else "missing: " + ", ".join(missing))
    return rows


# ------------------------------------------------------------------
# 3. REAL state: pings between the PCs
# ------------------------------------------------------------------
def check_ping(p):
    check = f"ping {p['dst']} {p['ip']}"
    try:
        out = run(["docker", "exec", container(p["src"]),
                   "ping", "-c", "3", "-W", "2", p["ip"]])
    except RuntimeError as e:                # ping exits 1 when 100% loss
        out = str(e)
    m = re.search(r"(\d+)% packet loss", out)
    loss = int(m.group(1)) if m else 100
    return (p["src"], check, loss == 0, f"{loss}% loss")


# ------------------------------------------------------------------
# 4. Run everything, print the table
# ------------------------------------------------------------------
def run_all_checks(nr, expected, pings):
    rows = []                                # (device, check, ok, detail)
    results = nr.run(task=check_router, expected=expected)
    for name in sorted(results):
        multi = results[name]
        if multi.failed:                     # e.g. container not running
            rows.append((name, "router checks", False, str(multi.exception)))
        else:
            rows += [(name, *row) for row in multi[0].result]
    rows += [check_ping(p) for p in pings]
    return rows


def print_table(rows):
    width = max(len(r[1]) for r in rows) + 2
    print(f"\n{'DEVICE':7} {'CHECK':{width}} {'RESULT':7} DETAIL")
    for device, check, ok, detail in rows:
        print(f"{device:7} {check:{width}} {'PASS' if ok else 'FAIL':7} {detail}")
    passed = sum(1 for r in rows if r[2])
    print(f"\nRESULT: {passed} PASS / {len(rows) - passed} FAIL")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--wait", type=int, default=0, metavar="SECONDS",
                        help="retry failed checks for up to SECONDS "
                             "(useful right after a deploy)")
    args = parser.parse_args()

    env = load_env(ROOT / ".env")
    nb = pynetbox.api(env["NETBOX_URL"], token=env["NETBOX_TOKEN"])
    expected, pings = build_expected(nb)
    nr = build_nornir()
    print(f"Expected state from NetBox: {len(expected)} routers, "
          f"{len(pings)} pings")

    deadline = time.monotonic() + args.wait
    while True:
        rows = run_all_checks(nr, expected, pings)
        failed = [r for r in rows if not r[2]]
        left = int(deadline - time.monotonic())
        if not failed or left <= 0:
            break
        print(f"  {len(failed)} check(s) failing, retrying in "
              f"{RETRY_EVERY} s ({left} s left)...")
        time.sleep(RETRY_EVERY)

    print_table(rows)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

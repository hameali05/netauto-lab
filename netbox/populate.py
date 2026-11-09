#!/usr/bin/env python3
"""Push the lab data from netbox/seed.yml into NetBox.

Idempotent: you can run it many times. For each object it either
CREATES it, UPDATES only the fields that differ, or does nothing.
It never deletes anything.

Usage:  .venv/bin/python netbox/populate.py
Needs:  .env with NETBOX_URL and NETBOX_TOKEN (never printed).
"""
import sys
from collections import Counter
from pathlib import Path

import pynetbox
import yaml

HERE = Path(__file__).resolve().parent
SEED_FILE = HERE / "seed.yml"
ENV_FILE = HERE.parent / ".env"

stats = Counter()  # how many objects were created / updated / unchanged


# ------------------------------------------------------------------
# 1. Read settings from .env (simple KEY=value lines)
# ------------------------------------------------------------------
def load_env(path):
    env = {}
    if not path.exists():
        sys.exit(f"ERROR: {path} not found")
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("NETBOX_URL", "NETBOX_TOKEN"):
        if not env.get(key):
            sys.exit(f"ERROR: {key} is missing in .env")
    return env


# ------------------------------------------------------------------
# 2. The heart of the script: create OR update OR leave alone
# ------------------------------------------------------------------
def same(current, wanted):
    """Compare a NetBox value with the wanted value (lists in any order)."""
    if isinstance(current, list) and isinstance(wanted, list):
        return sorted(current) == sorted(wanted)
    return current == wanted


def ensure(label, endpoint, lookup, data):
    """Make sure one object exists in NetBox with exactly `data`.

    label:    text shown on screen, e.g. "device r1"
    endpoint: pynetbox endpoint, e.g. nb.dcim.devices
    lookup:   filters that find the object, e.g. {"name": "r1"}
    data:     all fields the object must have (used to create or update)
    """
    record = endpoint.get(**lookup)

    if record is None:
        record = endpoint.create(data)
        print(f"  created    {label}")
        stats["created"] += 1
        return record

    current = record.serialize()  # nested objects become their id
    changes = {k: v for k, v in data.items() if not same(current.get(k), v)}

    if changes:
        endpoint.update([{"id": record.id, **changes}])  # PATCH only what changed
        print(f"  updated    {label}  ({', '.join(changes)})")
        stats["updated"] += 1
        return endpoint.get(record.id)

    print(f"  unchanged  {label}")
    stats["unchanged"] += 1
    return record


# ------------------------------------------------------------------
# 3. One function per kind of object, in dependency order
# ------------------------------------------------------------------
def build_basics(nb, seed):
    """RIR, manufacturers, device types, roles, platforms."""
    print("Basics:")
    r = seed["rir"]
    ids = {"rir": ensure(f"rir {r['slug']}", nb.ipam.rirs, {"slug": r["slug"]}, r).id}

    manufacturers = {}
    for m in seed["manufacturers"]:
        manufacturers[m["slug"]] = ensure(
            f"manufacturer {m['slug']}", nb.dcim.manufacturers, {"slug": m["slug"]}, m
        ).id

    device_types = {}
    for dt in seed["device_types"]:
        data = {**dt, "manufacturer": manufacturers[dt["manufacturer"]]}
        device_types[dt["slug"]] = ensure(
            f"device type {dt['slug']}", nb.dcim.device_types, {"slug": dt["slug"]}, data
        ).id

    roles = {}
    for role in seed["roles"]:
        roles[role["slug"]] = ensure(
            f"role {role['slug']}", nb.dcim.device_roles, {"slug": role["slug"]}, role
        ).id

    platforms = {}
    for p in seed["platforms"]:
        data = {**p, "manufacturer": manufacturers[p["manufacturer"]]}
        platforms[p["slug"]] = ensure(
            f"platform {p['slug']}", nb.dcim.platforms, {"slug": p["slug"]}, data
        ).id

    ids.update(device_types=device_types, roles=roles, platforms=platforms)
    return ids


def build_sites_and_asns(nb, seed, rir_id):
    """Sites, then one ASN object per ASN, linked to its site(s)."""
    print("Sites and ASNs:")
    sites = {}
    asn_to_sites = {}
    for s in seed["sites"]:
        data = {"name": s["name"], "slug": s["slug"], "status": "active"}
        sites[s["slug"]] = ensure(f"site {s['slug']}", nb.dcim.sites, {"slug": s["slug"]}, data).id
        asn_to_sites.setdefault(s["asn"], []).append(sites[s["slug"]])

    for asn, site_ids in asn_to_sites.items():
        data = {"asn": asn, "rir": rir_id, "sites": site_ids}
        ensure(f"asn {asn}", nb.ipam.asns, {"asn": asn}, data)
    return sites


def build_devices(nb, seed, ids, sites):
    """Devices (+ config context), their interfaces, and IP addresses."""
    for dev in seed["devices"]:
        print(f"Device {dev['name']}:")
        data = {
            "name": dev["name"],
            "site": sites[dev["site"]],
            "role": ids["roles"][dev["role"]],
            "device_type": ids["device_types"][dev["device_type"]],
            "platform": ids["platforms"][dev["platform"]] if dev.get("platform") else None,
            "status": "active",
            "local_context_data": dev.get("config_context"),  # OSPF/BGP data
        }
        device = ensure(f"device {dev['name']}", nb.dcim.devices, {"name": dev["name"]}, data)

        for intf in dev["interfaces"]:
            label = f"{dev['name']} {intf['name']}"
            data = {
                "device": device.id,
                "name": intf["name"],
                "type": intf["type"],
                "description": intf.get("description", ""),
            }
            interface = ensure(
                f"interface {label}",
                nb.dcim.interfaces,
                {"device_id": device.id, "name": intf["name"]},
                data,
            )

            data = {
                "address": intf["ip"],
                "status": "active",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": interface.id,
            }
            ensure(f"ip {intf['ip']} on {label}", nb.ipam.ip_addresses, {"address": intf["ip"]}, data)


# ------------------------------------------------------------------
# 4. Main: connect, build everything, print a summary
# ------------------------------------------------------------------
def main():
    env = load_env(ENV_FILE)
    seed = yaml.safe_load(SEED_FILE.read_text())

    nb = pynetbox.api(env["NETBOX_URL"], token=env["NETBOX_TOKEN"])
    print(f"Connected to NetBox {nb.version} at {env['NETBOX_URL']}\n")

    try:
        ids = build_basics(nb, seed)
        sites = build_sites_and_asns(nb, seed, ids["rir"])
        build_devices(nb, seed, ids, sites)
    except pynetbox.RequestError as err:
        sys.exit(f"\nNetBox refused a request: {err.error}")

    print(
        f"\nDone: {stats['created']} created, {stats['updated']} updated, "
        f"{stats['unchanged']} unchanged."
    )


if __name__ == "__main__":
    main()

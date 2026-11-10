#!/usr/bin/env python3
"""Generate configs/<router>/frr.conf for each router, from NetBox.

NetBox (source of truth) -> this script + templates/frr.j2 -> config files.
Read-only: it never changes anything in NetBox.

Usage:  .venv/bin/python generate.py
Needs:  NetBox running, .env with NETBOX_URL and NETBOX_TOKEN (never printed).
"""
import sys
from pathlib import Path

import pynetbox
from jinja2 import Environment, FileSystemLoader, StrictUndefined

from netbox.populate import load_env  # same .env reader as Phase 2

ROOT = Path(__file__).resolve().parent
TEMPLATE_DIR = ROOT / "templates"
OUTPUT_DIR = ROOT / "configs"


# ------------------------------------------------------------------
# 1. Collect the data for one router from NetBox
# ------------------------------------------------------------------
def site_asn(nb, device):
    """The local BGP ASN = the ASN object linked to the device's site."""
    asns = list(nb.ipam.asns.filter(site_id=device.site.id))
    if len(asns) != 1:
        sys.exit(f"ERROR: site {device.site.name} must have exactly 1 ASN, "
                 f"found {len(asns)}")
    return asns[0].asn


def interfaces_with_ips(nb, device):
    """[{name, ip}] for every interface that has an IP, sorted by name."""
    result = []
    for ip in nb.ipam.ip_addresses.filter(device_id=device.id):
        result.append({"name": ip.assigned_object.name, "ip": ip.address})
    return sorted(result, key=lambda i: i["name"])


def router_data(nb, device):
    """Everything the template needs, as plain Python dicts and lists."""
    context = device.config_context or {}   # OSPF/BGP data (config context)
    data = {
        "hostname": device.name,
        "interfaces": interfaces_with_ips(nb, device),
        "ospf": context.get("ospf"),
        "bgp": context.get("bgp"),
        "asn": None,
    }
    if data["bgp"]:                          # only BGP routers need the ASN
        data["asn"] = site_asn(nb, device)
    return data


# ------------------------------------------------------------------
# 2. Render the template and write the file
# ------------------------------------------------------------------
def main():
    env_vars = load_env(ROOT / ".env")
    nb = pynetbox.api(env_vars["NETBOX_URL"], token=env_vars["NETBOX_TOKEN"])

    jinja = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        trim_blocks=True,            # no empty line after {% ... %}
        lstrip_blocks=True,          # ignore spaces before {% ... %}
        keep_trailing_newline=True,  # file ends with a newline
        undefined=StrictUndefined,   # missing variable = error, not blank
    )
    template = jinja.get_template("frr.j2")

    routers = sorted(nb.dcim.devices.filter(role="router"), key=lambda d: d.name)
    if not routers:
        sys.exit("ERROR: no devices with role 'router' found in NetBox")

    for device in routers:
        config = template.render(**router_data(nb, device))
        out_file = OUTPUT_DIR / device.name / "frr.conf"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(config)
        print(f"  wrote {out_file.relative_to(ROOT)}")

    print(f"Done: {len(routers)} router configs generated.")


if __name__ == "__main__":
    main()

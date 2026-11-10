"""Batfish checks on the generated configs (configs/<router>/frr.conf).

Batfish reads the configs and builds a model of the network, WITHOUT
running any router. We check 3 things (see docs/PLAN.md):
  1. every config is read without errors
  2. the eBGP session r1 <-> r3 is compatible on both sides
  3. LAN A (PC-A) can reach LAN B (PC-B), in both directions

Usage:  .venv/bin/pytest -v tests/
Needs:  a Batfish container on BATFISH_HOST (default: localhost).
"""
import os
import re
from pathlib import Path

import pytest
from pybatfish.client.session import Session
from pybatfish.datamodel.flow import HeaderConstraints

ROOT = Path(__file__).resolve().parent.parent
CONFIGS_DIR = ROOT / "configs"
ROUTERS = ["r1", "r2", "r3", "r4"]


# ------------------------------------------------------------------
# 1. Packaging: wrap each frr.conf in the Cumulus "concatenated" format
#    Batfish has no plain-FRR format. It reads FRR inside the Cumulus
#    format: hostname, /etc/network/interfaces, ports.conf, frr.conf.
#    configs/ itself is NOT changed: only Batfish sees the wrapped copy.
# ------------------------------------------------------------------
def interfaces_section(frr_text):
    """Build a small /etc/network/interfaces from the IPs in frr.conf."""
    lines = ["# This file describes the network interfaces"]
    for name, ip in re.findall(r"^interface (\S+)\n ip address (\S+)$",
                               frr_text, re.MULTILINE):
        lines.append(f"auto {name}")
        lines.append(f"iface {name}" + (" inet loopback" if name == "lo" else ""))
        lines.append(f"    address {ip}")
        lines.append("")
    return "\n".join(lines)


def cumulus_file(hostname, frr_text):
    """One Batfish file for one router (order and markers from the docs)."""
    return "\n".join([
        hostname,
        interfaces_section(frr_text),
        "# ports.conf --",          # empty ports.conf: we have no switch ports
        frr_text,                   # starts with "frr version" = FRR marker
    ])


@pytest.fixture(scope="session")
def bf(tmp_path_factory):
    """Package the snapshot, upload it to Batfish, return the session."""
    snapshot = tmp_path_factory.mktemp("snapshot")
    (snapshot / "configs").mkdir()
    for router in ROUTERS:
        frr_text = (CONFIGS_DIR / router / "frr.conf").read_text()
        (snapshot / "configs" / f"{router}.cfg").write_text(
            cumulus_file(router, frr_text))

    session = Session(host=os.environ.get("BATFISH_HOST", "localhost"))
    session.set_network("netauto-lab")
    session.init_snapshot(str(snapshot), name="generated", overwrite=True)
    return session


# ------------------------------------------------------------------
# 2. Test 1: every config is read without errors
# ------------------------------------------------------------------
# Normal FRR lines that Batfish's Cumulus parser does not know. They are
# accepted on purpose; ANY other unknown line makes the test fail.
KNOWN_UNSUPPORTED = {
    "exit",                         # FRR 10 block end; old Cumulus FRR had none
    "domainname localdomain",       # DNS only, not routing
    "no bgp ebgp-requires-policy",  # Batfish never applies this rule (gap)
    "ip ospf passive",              # Batfish can't check passive (gap)
}


def test_all_configs_parsed(bf):
    status = bf.q.fileParseStatus().answer().frame()
    bad = status[~status["Status"].isin(["PASSED", "PARTIALLY_UNRECOGNIZED"])]
    assert bad.empty, f"Files not parsed:\n{bad}"

    warnings = bf.q.parseWarning().answer().frame()
    unknown = warnings[~warnings["Text"].str.strip().isin(KNOWN_UNSUPPORTED)]
    assert unknown.empty, (
        f"New unrecognized lines:\n{unknown[['Filename', 'Line', 'Text']]}")


def test_all_routers_present(bf):
    nodes = set(bf.q.nodeProperties().answer().frame()["Node"])
    assert nodes == set(ROUTERS)


# ------------------------------------------------------------------
# 3. Test 2: eBGP r1 <-> r3 is compatible (IPs, ASNs match)
# ------------------------------------------------------------------
def test_bgp_sessions_compatible(bf):
    sessions = bf.q.bgpSessionCompatibility().answer().frame()
    assert len(sessions) == 2, f"Expected 2 sides (r1, r3):\n{sessions}"
    bad = sessions[sessions["Configured_Status"] != "UNIQUE_MATCH"]
    assert bad.empty, f"Incompatible BGP sessions:\n{bad}"


def test_bgp_sessions_established(bf):
    sessions = bf.q.bgpSessionStatus().answer().frame()
    bad = sessions[sessions["Established_Status"] != "ESTABLISHED"]
    assert not sessions.empty and bad.empty, f"BGP not established:\n{sessions}"


# ------------------------------------------------------------------
# 4. Test 3: PC-A LAN reaches PC-B LAN (and back)
#    The flow "enters" the router LAN port, like a packet from the PC.
# ------------------------------------------------------------------
@pytest.mark.parametrize("start, src, dst", [
    ("r2[eth2]", "192.168.1.10", "192.168.2.10"),   # PC-A -> PC-B
    ("r4[eth2]", "192.168.2.10", "192.168.1.10"),   # PC-B -> PC-A
])
def test_lan_reachability(bf, start, src, dst):
    answer = bf.q.traceroute(
        startLocation=f"@enter({start})",
        headers=HeaderConstraints(srcIps=src, dstIps=dst),
    ).answer().frame()
    traces = answer.iloc[0]["Traces"]
    dispositions = {t.disposition for t in traces}
    assert dispositions == {"DELIVERED_TO_SUBNET"}, (
        f"{src} -> {dst}: {dispositions}\n{traces[0]}")

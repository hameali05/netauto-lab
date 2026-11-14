# reference-configs/ — Phase 1 baseline (do not update)

These are the **golden configs from Phase 1**: the `frr.conf` of R1–R4
after I configured the lab **by hand** with vtysh (IPs, OSPF area 0 in
each site, eBGP R1–R3), checked it (OSPF Full, BGP Established, PC-A
pings PC-B) and saved it with `write memory`.

## How they were used

In Phase 3, `generate.py` (NetBox + Jinja2 template) rebuilt the 4
configs, and they were **identical** to these files. That proved the
automation reproduces the working hand-made network exactly.

## These files are frozen

They are kept as a snapshot of Phase 1 and are **not updated** when
the network changes.

The **current** configs, the ones CI tests and `deploy.py` pushes, are
in [`configs/`](../configs/). They are generated from NetBox and now
differ from this baseline on purpose. The full pipeline test
(2026-11-14) added the transit link networks to BGP:

| Router | Extra line in `configs/` |
|--------|--------------------------|
| r1 | `network 10.1.12.0/30` (under `router bgp 65001`) |
| r3 | `network 10.2.34.0/30` (under `router bgp 65002`) |

To see the difference: `diff -r reference-configs configs`

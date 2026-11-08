# netauto-lab — Project Plan

## Address plan

| Link / item          | Network          | Device A     | Device B        | Purpose            |
|----------------------|------------------|--------------|-----------------|--------------------|
| R1 eth1 – R2 eth1    | 10.1.12.0/30     | R1 .1        | R2 .2           | Site A, OSPF       |
| R3 eth1 – R4 eth1    | 10.2.34.0/30     | R3 .1        | R4 .2           | Site B, OSPF       |
| R1 eth2 – R3 eth2    | 172.16.0.0/30    | R1 .1        | R3 .2           | eBGP between sites |
| R2 eth2 – PC-A eth1  | 192.168.1.0/24   | R2 .1        | PC-A .10        | LAN A              |
| R4 eth2 – PC-B eth1  | 192.168.2.0/24   | R4 .1        | PC-B .10        | LAN B              |

| Router | Loopback (/32) | Site | ASN   |
|--------|----------------|------|-------|
| R1     | 1.1.1.1        | A    | 65001 |
| R2     | 2.2.2.2        | A    | 65001 |
| R3     | 3.3.3.3        | B    | 65002 |
| R4     | 4.4.4.4        | B    | 65002 |

- OSPF area 0 inside each site only. eBGP only between R1 and R3.
- eth0 = management interface, never used for routing.

## Decisions (do not change without asking)
- FRR image: quay.io/frrouting/frr:10.2.1, Containerlab kind: linux
- One shared file frr/daemons for the 4 routers (zebra, bgpd, ospfd = yes,
  others = no, vtysh_enable=yes)
- PCs: alpine:latest; IP and default route set with "exec" in the topology file
- Topology file: two-sites.clab.yml, lab name: two-sites
- Links: r1:eth1-r2:eth1, r2:eth2-pc-a:eth1, r1:eth2-r3:eth2,
  r3:eth1-r4:eth1, r4:eth2-pc-b:eth1
- .gitignore must contain: .venv/, .env, __pycache__/, clab-*/
- NetBox data is written by netbox/populate.py (pynetbox) from netbox/seed.yml
- OSPF/BGP data in NetBox: config contexts (no plugin)
- generate.py runs on the laptop; generated configs are committed to Git
- GitHub Actions only CHECKS with Batfish. Deploy is run by me, on the laptop.
- Deploy method: Nornir copies frr.conf into each router and reloads FRR
  (the FRR image has no SSH server)
- Batfish tests: configs readable, BGP sessions compatible,
  PC-A LAN reaches PC-B LAN

## Phases

### Day A

#### Phase 1 — Lab built and configured by hand
- [x] frr/daemons + two-sites.clab.yml + add clab-*/ to .gitignore
- [ ] [YOU] Deploy the lab with containerlab (sudo)
- [ ] [YOU] IPs and loopbacks on R1–R4 (vtysh)
- [ ] [YOU] OSPF area 0 in Site A (R1, R2) and Site B (R3, R4)
- [ ] [YOU] eBGP R1–R3, site networks announced, routes shared with OSPF
- [ ] [YOU] Verify: OSPF Full, BGP Established, PC-A pings PC-B
- [ ] Save golden configs in reference-configs/
- [ ] git commit + push

#### Phase 2 — NetBox as source of truth
- [ ] [YOU] Start NetBox (docker compose up -d in ~/netbox-docker)
- [ ] [YOU] Create the API token in NetBox and put it in .env
- [ ] netbox/seed.yml (all lab data)
- [ ] netbox/populate.py: sites, roles, platform, devices, interfaces,
      IPs, ASNs, config contexts
- [ ] Run populate.py twice — no duplicates
- [ ] git commit + push

### Day B

#### Phase 3 — Templates and generation
- [ ] templates/frr.j2
- [ ] generate.py -> configs/<router>/frr.conf
- [ ] Compare generated configs with reference-configs/

#### Phase 4 — CI with Batfish
- [ ] tests/test_batfish.py
- [ ] .github/workflows/ci.yml
- [ ] Demo: push a wrong ASN -> CI fails -> fix -> CI green

#### Phase 5 — Deploy and verify
- [ ] deploy.py (Nornir) with --dry-run
- [ ] verify.py (PASS/FAIL table: OSPF, BGP, routes, ping)
- [ ] Test from zero: destroy lab, redeploy, deploy.py, verify.py

#### Phase 6 — Finish
- [ ] README
- [ ] [YOU] Topology screenshot
- [ ] Check no secrets in Git
- [ ] CV line

## End of each session
1. git commit + push
2. Stop NetBox (keeps data): `cd ~/netbox-docker && docker compose stop`
3. [YOU - Windows] `wsl --shutdown` (in PowerShell, frees RAM)

## Notes
- 2026-11-08: Swapped Phase 4 and Phase 5. CI with Batfish is now Phase 4,
  Deploy + verify is now Phase 5, to follow the pipeline order
  (check configs before deploying them).

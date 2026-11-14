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
- [x] [YOU] Deploy the lab with containerlab (sudo)
- [x] [YOU] IPs and loopbacks on R1–R4 (vtysh)
- [x] [YOU] OSPF area 0 in Site A (R1, R2) and Site B (R3, R4)
- [x] [YOU] eBGP R1–R3, site networks announced, routes shared with OSPF
- [x] [YOU] Verify: OSPF Full, BGP Established, PC-A pings PC-B
- [x] Save golden configs in reference-configs/
- [x] git commit + push
- [x] Learning doc (docs/learning/phase1.md)

#### Phase 2 — NetBox as source of truth
- [x] [YOU] Start NetBox (docker compose up -d in ~/netbox-docker)
- [x] [YOU] Create the API token in NetBox and put it in .env
- [x] netbox/seed.yml (all lab data)
- [x] netbox/populate.py: sites, roles, platform, devices, interfaces,
      IPs, ASNs, config contexts
- [x] Run populate.py twice — no duplicates
- [x] git commit + push
- [x] Learning doc (docs/learning/phase2.md)

### Day B

#### Phase 3 — Templates and generation
- [x] templates/frr.j2
- [x] generate.py -> configs/<router>/frr.conf
- [x] Compare generated configs with reference-configs/
- [x] Learning doc (docs/learning/phase3.md)

#### Phase 4 — CI with Batfish
- [x] tests/test_batfish.py
- [x] .github/workflows/ci.yml
- [x] Demo: push a wrong ASN -> CI fails -> fix -> CI green
- [x] Learning doc (docs/learning/phase4.md)

#### Phase 5 — Deploy and verify
- [x] deploy.py (Nornir) with --dry-run
- [x] verify.py (PASS/FAIL table: OSPF, BGP, routes, ping)
- [x] Negative test: break R3 eth2 by hand -> verify.py FAIL ->
      deploy.py repairs -> verify.py all PASS
- [x] Full pipeline test: a new change in NetBox -> generate.py ->
      git push -> CI green -> deploy.py -> verify.py
- [x] Test from zero: destroy lab, redeploy, deploy.py, verify.py --wait 60
- [x] Learning doc (docs/learning/phase5.md)

#### Phase 6 — Finish
- [ ] README   <- NEXT
- [ ] [YOU] Topology screenshot
- [ ] Check no secrets in Git
- [ ] CV line
- [ ] Learning doc (docs/learning/phase6.md)

## End of each session
1. git commit + push
2. Stop NetBox (keeps data): `cd ~/netbox-docker && docker compose stop`
3. [YOU - Windows] `wsl --shutdown` (in PowerShell, frees RAM)

## Notes
- 2026-11-08: Swapped Phase 4 and Phase 5. CI with Batfish is now Phase 4,
  Deploy + verify is now Phase 5, to follow the pipeline order
  (check configs before deploying them).
- 2026-11-09: Added shared frr/vtysh.conf ("service integrated-vtysh-config"),
  bound into R1–R4 like daemons. Without it, `write memory` saves 5 separate
  files (zebra.conf, ospfd.conf...) instead of one frr.conf, which the golden
  configs and the deploy method need.
- 2026-11-09: Learning doc at the end of each phase in docs/learning/phaseN.md
  (private: docs/learning/ is in .gitignore). Recap, decisions, Mermaid
  diagrams, interview Q&A.
- 2026-11-09: seed.yml — the local BGP ASN is NOT stored in the config
  context: it comes from the site's ASN object (one source for each fact).
  OSPF/BGP data is stored per device as "local config context data".
- 2026-11-09: PC-A and PC-B are in NetBox too (role "pc", with their IPs),
  so NetBox holds the full lab and verify.py can find ping targets there.
  eth0 (management) is not modelled.
- 2026-11-11: Batfish has no plain-FRR format. tests/test_batfish.py wraps
  each configs/<router>/frr.conf in the Cumulus "concatenated" format
  (hostname + small interfaces section + empty ports.conf + frr.conf) in a
  temporary snapshot. configs/ is unchanged (still = golden configs, still
  what deploy.py sends).
- 2026-11-11: Batfish does not understand 4 normal FRR lines: exit,
  domainname, no bgp ebgp-requires-policy, ip ospf passive. The parse test
  accepts exactly these (KNOWN_UNSUPPORTED) and fails on any other.
  Known gaps: Batfish can't check OSPF passive or ebgp-requires-policy.
  verify.py (Phase 5) checks them on the real routers.
- 2026-11-11: Everything in CI is pinned: requirements.txt (exact versions),
  Batfish image 2026.08.27.3685 (= laptop), runner ubuntu-24.04,
  actions/checkout@v7 + setup-python@v7. Upgrade on purpose only.
- 2026-11-11: CI demo done on branch demo-wrong-asn (kept, PR closed
  without merging): remote-as 65003 in seed.yml -> 4 failed / 2 passed ->
  fixed in the same PR -> green.
- 2026-11-11: deploy.py has a CI gate: before deploying, it asks the GitHub
  public API (no token, no gh) if the "Batfish checks" run for the current
  commit on main is green. It also refuses if configs/ has uncommitted
  changes. --skip-ci-check bypasses it (with a warning). --dry-run is
  never blocked. Deploy is still started by hand.
- 2026-11-11: Nornir inventory comes from NetBox (nornir_netbox, devices
  with role "router"). Container name = clab-two-sites-<device name>.
  Config applied with frr-reload.py (only the differences, no restart),
  then checked again with --test (must show no difference).
- 2026-11-11: After a PC restart (or wsl --shutdown), Docker restarts the
  lab containers but containerlab does NOT recreate the links: routers
  have only lo + eth0 ("pseudo interface" in vtysh). Fix at the start of
  each session: [YOU] sudo containerlab destroy + deploy, then
  deploy.py and verify.py --wait 60.
- 2026-11-11: verify.py takes the EXPECTED state from NetBox (pynetbox)
  and the REAL state from `show ... json` (Nornir, same inventory as
  deploy.py) + pings between PCs. Covers the Batfish gaps: OSPF passive,
  and ebgp-requires-policy (checked by its effect: prefixes received).
  Done so far: dry run, from-zero deploy, drift test, first verify.py
  run (20/20 PASS). Journal in docs/learning/phase5.md.
- 2026-11-14: Full pipeline test done (commit 8c2d32c): R1 announces
  10.1.12.0/30 and R3 announces 10.2.34.0/30 in BGP (seed.yml -> NetBox
  -> generate.py -> CI green -> deploy.py +1 line on r1/r3 -> verify.py
  20/20, "4 received"). Side effect: fixes the Phase 1 "no route back"
  problem (R2 can ping 4.4.4.4 without -I; before, R4 replied via eth0).
- 2026-11-14: "Test from zero" not repeated as a separate test: done on
  2026-11-11 (journal Test 2) and in each restart routine (again today:
  20/20 PASS).
- 2026-11-14: Don't poll the GitHub API: 60 calls/hour without a token.
  Check the CI tick on GitHub; deploy.py's CI gate uses only 1 call.
- 2026-11-14: reference-configs/ (Phase 1 golden configs) now differ from
  configs/ for r1 and r3 (the 2 new network lines). Decision: keep them
  frozen as the Phase 1 baseline, never update them. Explained in
  reference-configs/README.md. Current configs = configs/.
- 2026-11-09: Possible bonus at the end: add cables (links) in NetBox
  (with "other" interface types) to get a topology view. Not done for now.

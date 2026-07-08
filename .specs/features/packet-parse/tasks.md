# Packet Parse & Fail-Fast — Tasks

**Design:** `.specs/features/packet-parse/design.md`
**Spec:** `.specs/features/packet-parse/spec.md` (PKT-01..24 — **finalized below**)
**Status:** **Approved** (2026-07-08) — Execute deferred (user go-ahead pending)
**Execute tooling (chosen):** Skill `coding-guidelines` on the C/XDP tasks (T1–T7); MCPs: none configured.
Execution mode (sub-agents vs inline) to be decided when Execute is triggered.

> **Requirement IDs finalized here** (spec noted "finalized in Design/Tasks"): Scaffold/loader/harness
> **PKT-01..06**, fail-fast + enum/counter **PKT-07..12**, `pkt_meta`/single-parse **PKT-13..18**,
> VLAN/QinQ **PKT-19..22**, ARP **PKT-23..24**. Canonical list in the Traceability table.

> **Test conventions** come from `design.md` → *Data-plane test conventions* (this feature has no prior
> C/XDP entry in `.specs/codebase/TESTING.md`; T8 adds it). Gates (run from `data-plane/`):
> **build** = `make bpf skel loader` · **quick** = `make test` (`BPF_PROG_TEST_RUN`, `-DPKT_TEST_HOOKS`) ·
> **full** = `make test` + optional privileged live-veth smoke. Test type **dp-unit** = synthetic-packet
> `BPF_PROG_TEST_RUN`, parallel-safe as infra but parse tasks share `parse.h`/`xdp_gateway.bpf.c`/
> `test_parse.c` → the T4→T7 chain runs **sequentially**.

---

## Execution Plan

### Phase 1: Bootstrap (Sequential)

```
T1  (scaffold + contracts + trivial prog)
```

### Phase 2: Off-chain components (Parallel with Phase 3)

```
T1 ──┬──→ T2 [P]  (native-mode loader — separate file loader/loader.c)
     └──→ T3       (test harness walking skeleton — starts the parse chain)
```

### Phase 3: Parse behavior (Sequential chain — shared files)

```
T3 ──→ T4 ──→ T5 ──→ T6 ──→ T7
       (eth   (ipv4  (l4 +   (vlan/
        +fail  +frag  pkt_    qinq)
        +arp)  )      meta)
```

### Phase 4: Docs (Parallel — after T3)

```
T3 ──→ T8 [P]  (TESTING.md data-plane section — separate file)
```

**Parallel summary:** `T2` and `T8` are the only `[P]` tasks — each edits a file no other task touches
(`loader/loader.c`, `.specs/codebase/TESTING.md`) and has no automated tests, so they run alongside the
sequential `T3→T4→T5→T6→T7` chain. The chain itself is **not** parallel: every step edits the shared
`parse.h` / `xdp_gateway.bpf.c` / `test_parse.c`.

---

## Task Breakdown

### T1: Data-plane scaffold + contract headers + trivial XDP program

**Status:** Verified (2026-07-08)
**What:** Create `data-plane/` with a working `clang -target bpf` + `bpftool` skeleton build, the two
contract headers, and a trivial XDP program that compiles and returns `XDP_PASS`.
**Where:** `data-plane/Makefile`, `data-plane/README.md`, `data-plane/.gitignore`,
`data-plane/src/pkt_meta.h`, `data-plane/src/drop_reason.h`, `data-plane/src/xdp_gateway.bpf.c`
**Depends on:** None
**Reuses:** PRD §10.2 reason names; `design.md` layout + `pkt_meta`/`drop_reason` definitions
**Requirement:** PKT-01, PKT-12, PKT-13, PKT-18

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `data-plane/` tree matches `design.md` layout; `build/` is git-ignored.
- [x] `pkt_meta.h` defines `struct pkt_meta` (fields per design, explicit padding, zero-init convention).
- [x] `drop_reason.h` defines `enum drop_reason` (4 fail-fast + `DR_MAP_ERROR`, `DROP_REASON_CAP=32`),
      the `counter_map` (`PERCPU_ARRAY`, `DROP_REASON_CAP`), and `static __always_inline record_drop()`.
- [x] `xdp_gateway.bpf.c` = `SEC("xdp") int xdp_gateway(struct xdp_md *ctx) { return XDP_PASS; }`,
      declares `counter_map` + (`#ifdef PKT_TEST_HOOKS`) `test_meta_map`; uses plain uapi headers, no
      `vmlinux.h`.
- [x] Gate check passes: `make bpf skel` builds `build/xdp_gateway.bpf.o` + `build/xdp_gateway.skel.h`
      with no clang/verifier-invalidating errors.
**Tests:** none · **Gate:** build

**Verify:** `cd data-plane && make bpf skel && ls build/xdp_gateway.skel.h` → skeleton present, exit 0.
**Commit:** `feat(dataplane): scaffold build + pkt_meta/drop_reason contracts + trivial XDP prog`

---

### T2: Native-mode XDP loader [P]

**Status:** Verified (2026-07-08)
**What:** Userspace loader that loads the skeleton and attaches to `IN` in native/DRV mode, fails loud
on non-native, reports the actual mode, and detaches cleanly on exit.
**Where:** `data-plane/loader/loader.c` (+ Makefile `loader`/`run` targets)
**Depends on:** T1
**Reuses:** generated `xdp_gateway.skel.h`; verified libbpf `bpf_xdp_attach`/`bpf_xdp_query`/
`bpf_xdp_detach` (design Research notes)
**Requirement:** PKT-02, PKT-03, PKT-04, PKT-05

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] Loads via `xdp_gateway_bpf__open_and_load()`; resolves ifindex via `if_nametoindex` (`argv[1]` or
      `IN_IFACE`, A-PKT-6).
- [x] `bpf_xdp_attach(ifindex, prog_fd, XDP_FLAGS_DRV_MODE, NULL)`; on `<0` prints a clear
      "native XDP unsupported" error and `exit(1)` — **no** SKB fallback (PKT-03).
- [x] Logs the actual mode from `bpf_xdp_query` (PKT-04); `SIGINT`/`SIGTERM` → `bpf_xdp_detach` + skeleton
      destroy (PKT-05).
- [x] Gate check passes: `make loader` builds `build/xdp_gateway_loader`.
- [x] Manual smoke recorded in README: `sudo ./build/xdp_gateway_loader <veth>` attaches in DRV or errors
      clearly (no automated test — no privileged-NIC test type in v1).
**Tests:** none · **Gate:** build

**Verify:** `make loader` exit 0; `sudo ./build/xdp_gateway_loader veth0` → prints attach mode or a clean
native-unsupported error; Ctrl-C leaves the iface with no XDP prog (`ip link show veth0`).
**Commit:** `feat(dataplane): native/DRV-mode XDP loader with fail-loud attach + signal detach`

---

### T3: Test harness walking skeleton (BPF_PROG_TEST_RUN)

**Status:** Verified (2026-07-08)
**What:** Synthetic-frame builders + a `BPF_PROG_TEST_RUN` runner with one trivial assertion, proving the
compile→load→test loop end-to-end against the trivial program.
**Where:** `data-plane/tests/pkt_build.h`, `data-plane/tests/test_parse.c` (+ Makefile `test` target
compiling the BPF obj with `-DPKT_TEST_HOOKS`)
**Depends on:** T1
**Reuses:** generated skeleton (test variant); verified `bpf_prog_test_run_opts` (design Research notes)
**Requirement:** PKT-06

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `pkt_build.h` provides composable frame builders: `build_eth(ethertype)`, `build_vlan`, `build_qinq`,
      `build_ipv4(proto,frag,ihl)`, `build_tcp/udp/icmp`, `build_arp`, `build_ipv6` (byte buffers).
- [x] `test_parse.c` loads the `-DPKT_TEST_HOOKS` object and runs `bpf_prog_test_run_opts` with a frame as
      `data_in`, asserting `opts.retval`; helper to read `counter_map[reason]` and `test_meta_map[0]`.
- [x] One trivial test passes (any well-formed frame → `XDP_PASS` on the trivial prog), proving the loop.
- [x] Gate check passes: `make test`.
- [x] Test count: **1** dp-unit test passes (no silent deletions).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `cd data-plane && make test` → `1 passed`, exit 0.
**Commit:** `test(dataplane): BPF_PROG_TEST_RUN harness + synthetic frame builders (walking skeleton)`

---

### T4: EtherType resolution + fail-fast drops (IPv6 / unsupported) + ARP pass

**Status:** Verified (2026-07-08)
**What:** Parse L2, branch on EtherType — IPv6 → drop, ARP → `XDP_PASS` (marked seam), other → drop —
each drop recorded, all reads bounds-checked; the IPv4 case passes through (parsed in T5).
**Where:** `data-plane/src/parse.h` (new: `hdr_cursor`, `parse_eth`), `data-plane/src/xdp_gateway.bpf.c`
(EtherType switch + `record_drop`/seam), `data-plane/tests/test_parse.c` (add cases)
**Depends on:** T3
**Reuses:** `pkt_meta.h`, `drop_reason.h`, `pkt_build.h`; xdp-tutorial cursor/`data_end` idiom
**Requirement:** PKT-07, PKT-08, PKT-11 (L2 bounds), PKT-17 (stateless path), PKT-23, PKT-24

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `parse_eth` advances a `data_end`-checked cursor, sets `pkt_meta.eth_proto`; truncated L2 →
      `unsupported_ethertype` (fail-closed, PKT-11).
- [x] Entry: `ETH_P_IPV6` → `record_drop(DR_IPV6_UNSUPPORTED)`; non-IPv4/non-ARP → `DR_UNSUPPORTED_ETHERTYPE`;
      `ETH_P_ARP` → `XDP_PASS` with a `/* SEAM: redirect */` comment, **no** counter (PKT-23/24);
      `ETH_P_IP` → `XDP_PASS` placeholder `/* SEAM: IPv4 parse (T5) */`.
- [x] No per-source-IP state on the path (PKT-17).
- [x] Tests added: IPv6 → `XDP_DROP` + `counter[ipv6]==1`; non-IP (e.g. `0x0000`) → drop + `unsupported`
      counter; truncated VLAN-tag frame → `unsupported`; ARP → `XDP_PASS` + no drop counter. `parse_eth`
      retains the short-L2 bounds check; `BPF_PROG_TEST_RUN` rejects sub-Ethernet `data_in` before the
      program runs on this kernel.
- [x] Gate check passes: `make test`. Test count: **5** dp-unit tests pass (1 trivial + 4 new).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `make test` → all pass; ARP case asserts `retval==XDP_PASS` and every drop counter `==0`.
**Commit:** `feat(dataplane): EtherType resolution + IPv6/unsupported fail-fast + ARP pass seam`

---

### T5: IPv4 parse + malformed + fragment drops

**Status:** Verified (2026-07-08)
**What:** Parse the IPv4 header once into `pkt_meta`, dropping malformed headers and all fragments; valid
IPv4 continues to the L4 seam (filled in T6).
**Where:** `data-plane/src/parse.h` (add `parse_ipv4`), `data-plane/src/xdp_gateway.bpf.c` (IPv4 branch),
`data-plane/tests/test_parse.c` (add cases)
**Depends on:** T4
**Reuses:** `parse_eth`/cursor from T4; `pkt_meta.h`; `<linux/ip.h>`
**Requirement:** PKT-09, PKT-10, PKT-11 (L3 bounds), PKT-14

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `parse_ipv4` checks `version==4`, `ihl>=5`, header/`tot_len`/option bounds vs `data_end`; sets
      `src_ip/dst_ip/ip_proto/l3_off/l4_off`. Bad → `malformed_ipv4` (PKT-09); truncation → `malformed_ipv4`
      (PKT-11).
- [x] Fragment (`MF` set or `frag_off!=0`, A-PKT-7) → `record_drop(DR_FRAGMENT_UNSUPPORTED)`, sets
      `is_fragment` (PKT-10).
- [x] Valid IPv4 → `XDP_PASS` placeholder `/* SEAM: L4 parse (T6) */` with IPv4 fields populated (PKT-14).
- [x] Tests added: `version!=4`, `ihl<5`, truncated header, `tot_len`>frame → `malformed_ipv4`; first
      (`off=0,MF=1`) and later fragment → `fragment_unsupported`; well-formed IPv4 → `XDP_PASS`.
- [x] Gate check passes: `make test`. Test count: **11** dp-unit tests pass (6 new plus valid IPv4 update).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `make test` → all pass; both fragment variants assert `fragment_unsupported` counter increments.
**Commit:** `feat(dataplane): IPv4 header parse with malformed + fragment fail-fast`

---

### T6: L4 parse + pkt_meta population + service-lookup seam

**Status:** Verified (2026-07-08)
**What:** Parse L4 (TCP/UDP ports, ICMP type/code, other = ports 0), finalize `pkt_meta`, and return
`XDP_PASS` at the marked service-lookup seam; truncated L4 → malformed.
**Where:** `data-plane/src/parse.h` (add `parse_l4`), `data-plane/src/xdp_gateway.bpf.c` (fill `pkt_meta`,
`#ifdef PKT_TEST_HOOKS` write `test_meta_map`, final seam), `data-plane/tests/test_parse.c` (add cases)
**Depends on:** T5
**Reuses:** `parse_ipv4` output (`ip_proto`,`l4_off`); `<linux/tcp.h>`/`udp.h`/`icmp.h`; `test_meta_map`
**Requirement:** PKT-15, PKT-16, PKT-17, PKT-18

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [x] `parse_l4`: TCP/UDP → `sport/dport`; ICMP → `icmp_type/code`; other IPv4 proto → ports 0, returns OK
      and **continues** (A-PKT-5); truncated L4 header → `malformed_ipv4` (A-PKT-4, PKT-11).
- [x] `pkt_meta` fully populated by a **single** parse (PKT-17); zero-init + padding respected (PKT-18);
      `#ifdef PKT_TEST_HOOKS` writes `test_meta_map[0]=meta` before the seam.
- [x] Valid IPv4 returns `XDP_PASS` at the single `/* SEAM: service lookup (next feature) */` (PKT-16).
- [x] Tests added: TCP/UDP assert `sport/dport` in `test_meta_map`; ICMP asserts `type/code`; GRE/ESP →
      `XDP_PASS` with ports 0; truncated UDP/TCP header → `malformed_ipv4`.
- [x] Gate check passes: `make test`. Test count: **17** dp-unit tests pass (6 new).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `make test` → all pass; a UDP frame asserts `test_meta_map[0].src_ip/dport` equal the built
values and `retval==XDP_PASS`.
**Commit:** `feat(dataplane): L4 parse + single-parse pkt_meta + XDP_PASS service-lookup seam`

---

### T7: VLAN / QinQ EtherType resolution

**What:** Unwrap up to two 802.1Q/802.1ad tags to the inner EtherType (tags preserved), dropping deeper
stacks/truncated tags as unsupported; all EtherType branching then applies to tagged frames.
**Where:** `data-plane/src/parse.h` (add `parse_vlan`, call from `parse_eth`),
`data-plane/src/xdp_gateway.bpf.c` (set `vlan_depth`), `data-plane/tests/test_parse.c` (add cases)
**Depends on:** T6
**Reuses:** `parse_eth` from T4; bounded-loop verifier idiom
**Requirement:** PKT-19, PKT-20, PKT-21, PKT-22

**Tools:** MCP: NONE · Skill: `coding-guidelines`

**Done when:**
- [ ] `parse_vlan` unwraps ≤2 tags (`ETH_P_8021Q`/`ETH_P_8021AD`) via a **bounded** `for(i<2)` loop to the
      inner `eth_proto`, sets `vlan_depth` (PKT-19/20); tags preserved (not stripped, A-PKT-1).
- [ ] `>2` tags or a truncated tag → `record_drop(DR_UNSUPPORTED_ETHERTYPE)` (PKT-21).
- [ ] After unwrap, all P1/P2 EtherType branches apply identically to the tagged frame (PKT-22).
- [ ] Tests added: single-VLAN IPv4 → `XDP_PASS` + correct `pkt_meta` (`vlan_depth==1`); QinQ IPv4 →
      `XDP_PASS` (`vlan_depth==2`); triple-tag → `unsupported_ethertype`; VLAN-wrapped IPv6 → `ipv6_unsupported`.
- [ ] Gate check passes: `make test`. Test count: **≥20** dp-unit tests pass (≥4 new).
**Tests:** dp-unit · **Gate:** quick

**Verify:** `make test` → all pass; QinQ IPv4 asserts inner fields resolved and `retval==XDP_PASS`.
**Commit:** `feat(dataplane): VLAN/QinQ EtherType resolution (bounded ≤2 tags)`

---

### T8: Data-plane section in TESTING.md [P]

**Status:** Verified (2026-07-08)
**What:** Add the data-plane test conventions (gates, `dp-unit` type, parallel-safety, corpus) to the
codebase testing doc.
**Where:** `.specs/codebase/TESTING.md` (append a **Data-plane (C/XDP)** section)
**Depends on:** T3
**Reuses:** `design.md` → *Data-plane test conventions*; mirrors the AD-008 control-plane pattern
**Requirement:** — (supports PKT-06; A-PKT-2)

**Tools:** MCP: NONE · Skill: NONE

**Done when:**
- [x] New section documents: gates (build/quick/full `make` commands), `dp-unit` = `BPF_PROG_TEST_RUN`
      synthetic packets (parallel-safe as infra; parse tasks serialize on shared files), loader = build +
      manual veth smoke (no automated test in v1), and the adversarial-frame corpus.
- [x] States a future `dp-integration` (live veth/NIC) would need `CAP_NET_ADMIN` and is not parallel-safe.
- [x] Existing control-plane content unchanged.
**Tests:** none · **Gate:** none

**Verify:** `.specs/codebase/TESTING.md` renders with a Data-plane section; control-plane section intact.
**Commit:** `docs(testing): add data-plane (C/XDP) test conventions`

---

## Requirement Traceability (24 total → all mapped)

| Req | Description | Task |
| --- | --- | --- |
| PKT-01 | Build compiles XDP obj + skeleton | T1 |
| PKT-02 | Loader attaches native/DRV mode | T2 |
| PKT-03 | Fail-loud on non-native (no SKB fallback) | T2 |
| PKT-04 | Loader reports actual mode | T2 |
| PKT-05 | Loader detach/cleanup on exit | T2 |
| PKT-06 | `BPF_PROG_TEST_RUN` synthetic-packet harness | T3 |
| PKT-07 | IPv6 → `ipv6_unsupported` | T4 |
| PKT-08 | Unknown EtherType → `unsupported_ethertype` | T4 |
| PKT-09 | Malformed IPv4 → `malformed_ipv4` | T5 |
| PKT-10 | Fragment → `fragment_unsupported` | T5 |
| PKT-11 | Bounds-checked / fail-closed on truncation | T4, T5, T6 |
| PKT-12 | `enum drop_reason` + minimal per-CPU counter | T1 |
| PKT-13 | `pkt_meta` single-parse contract struct | T1 |
| PKT-14 | IPv4 fields parsed once into `pkt_meta` | T5 |
| PKT-15 | L4 parse (TCP/UDP/ICMP/other) | T6 |
| PKT-16 | Valid IPv4 → `XDP_PASS` service-lookup seam | T6 |
| PKT-17 | Single-parse invariant; no per-source state | T4, T6 |
| PKT-18 | Zero-init + consistent padding | T1, T6 |
| PKT-19 | Single 802.1Q unwrap (tags preserved) | T7 |
| PKT-20 | QinQ double-tag unwrap | T7 |
| PKT-21 | >2 tags / truncated → `unsupported_ethertype` | T7 |
| PKT-22 | EtherType branching applies to tagged frames | T7 |
| PKT-23 | ARP → `XDP_PASS`, not a drop | T4 |
| PKT-24 | ARP branch = marked redirect seam | T4 |

**Coverage:** 24 total, 24 mapped to tasks, 0 unmapped ✅

# Data-plane BPF Test-Run Gotchas

## Context

Packet parse execute added the `data-plane/` XDP parser and `BPF_PROG_TEST_RUN`
suite. Relevant code:

- `data-plane/tests/test_parse.c` — synthetic frame runner and verdict/meta assertions.
- `data-plane/src/parse.h` — verifier-friendly packet cursor and parse helpers.
- `data-plane/src/xdp_gateway.bpf.c` — verdict policy and `PKT_TEST_HOOKS` meta map.

## Gotchas

- This kernel rejects sub-Ethernet `data_in` for XDP `BPF_PROG_TEST_RUN` before
  the program runs. A true short-L2 runtime test using `data_size_in < sizeof(struct ethhdr)`
  returns `EINVAL`, so the suite uses a truncated VLAN-tag frame for a runner-feasible
  L2 truncation case while `parse_eth` still keeps the direct `data_end` bounds check.
- A test-only map cannot safely replace `ctx->data_end` for verifier bounds proof. The
  verifier rejected dynamic synthetic packet-end pointer arithmetic before packet reads.
  Keep parser tests on real `data_in` lengths the kernel accepts.
- Variable IPv4 IHL handling must be shaped for the verifier: compute a bounded `20..60`
  header length, check `iph + ihl_len <= data_end`, then set the cursor to that checked
  pointer. Avoid adding unbounded `total_len` directly to packet pointers; compare it
  against a scalar available length instead.

## Current Gate

From `data-plane/`:

- Build gate: `make bpf skel loader`
- Quick gate: `make test`

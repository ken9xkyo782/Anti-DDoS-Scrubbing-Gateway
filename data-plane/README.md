# Data Plane

Native-XDP packet parsing and fail-fast verdict code lives here.

## Build

```bash
make bpf skel
```

This compiles `src/xdp_gateway.bpf.c` with `clang -target bpf` and generates a libbpf skeleton at
`build/xdp_gateway.skel.h`.

## Requirements

- `clang`
- `llvm-strip`
- `bpftool`
- `libbpf` headers
- kernel UAPI headers

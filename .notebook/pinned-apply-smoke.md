# Pinned Apply Smoke
> Separate-process config swap against the native-XDP loader

Entry: `data-plane/tools/xdpgw-apply.c:open_apply_pins()`

Pins: exactly the 14 config maps from `data-plane/loader/loader.c:set_config_pin_paths()`.
- No runtime maps, static inners, or `tx_devmap` in the helper fd bundle.

Flow: `data-plane/tests/smoke_apply.sh` → loader with pins → helper subprocess → `dpstat active_config`.
- Sink veth needs the XDP_PASS setup used by `smoke_redirect.sh` for live redirect observation.

Feed proof: `data-plane/tests/apply_bulk.sh:map_inner_id()` compares slot-0 and slot-1
`inner_map_id` values from `bpftool map lookup pinned`; map-in-map output does not use `value:`.

Updated: 2026-07-13

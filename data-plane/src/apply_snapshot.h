#ifndef XDP_GATEWAY_APPLY_SNAPSHOT_H
#define XDP_GATEWAY_APPLY_SNAPSHOT_H

/*
 * Control-plane to data-plane apply snapshot wire contract.
 *
 * This is a byte stream, not a C structure: the writer and reader encode each
 * field explicitly so compiler padding and host byte order cannot change it.
 * All integer fields marked le16/le32/le64 are little-endian. IPv4 fields
 * marked be32 keep network byte order.
 */
#define APPLY_SNAPSHOT_MAGIC "XDPGWAP1"
#define APPLY_SNAPSHOT_MAGIC_SIZE 8U
#define APPLY_SNAPSHOT_SCHEMA_VERSION 1U

/* magic[8], schema_version: le32, service_count: le32 */
#define APPLY_SNAPSHOT_HEADER_SIZE 16U

/*
 * Repeated service record, in order:
 *
 *   dst_prefixlen: le32
 *   dst_addr: be32
 *   dp_id: le32          -- the u32 data-plane surrogate (ProtectedService.dp_id,
 *                           >=1, 0 = sentinel); written into service_val.service_id.
 *                           This is NOT the control-plane service UUID.
 *   enabled: u8
 *   wl_flags: u8
 *   bl_flags: u8
 *   committed_bps: le64
 *   ceiling_bps: le64
 *   vip_pps: le64        -- service-level VIP ceiling (0 when VIP_F_PPS_SET unset)
 *   vip_bps: le64        -- service-level VIP ceiling (0 when VIP_F_BPS_SET unset)
 *   vip_flags: u8        -- VIP_F_PPS_SET | VIP_F_BPS_SET; the helper stamps
 *                           vip_config.version itself (not carried on the wire).
 *   rule_count: le16
 *   rules[rule_count]:
 *     src_lo: le16, src_hi: le16, dst_lo: le16, dst_hi: le16,
 *     proto: u8, flags: u8
 *   wl_count: le32
 *   wl[wl_count]:
 *     prefixlen: le32, src_addr: be32
 *   sbl_count: le32
 *   sbl[sbl_count]: prefixlen: le32, src_addr: be32
 *
 * VIP limits are per service (one vip_config row keyed by dp_id), not per
 * whitelist entry -- matching struct vip_config in whitelist.h and the
 * control-plane ServiceConfig.vip_pps/vip_bps. Whitelist entries carry only the
 * source CIDR (the LPM value is a presence marker).
 *
 * schema_version must increase for every layout change. Readers reject an
 * unknown version before touching data-plane maps.
 */
#define APPLY_SNAPSHOT_SERVICE_FIXED_SIZE 50U
#define APPLY_SNAPSHOT_RULE_SIZE 10U
#define APPLY_SNAPSHOT_WHITELIST_ENTRY_SIZE 8U
#define APPLY_SNAPSHOT_SERVICE_BLACKLIST_ENTRY_SIZE 8U

#endif

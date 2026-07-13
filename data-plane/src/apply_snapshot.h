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
 *   service_id: le32
 *   enabled: u8
 *   wl_flags: u8
 *   bl_flags: u8
 *   committed_bps: le64
 *   ceiling_bps: le64
 *   rule_count: le16
 *   rules[rule_count]:
 *     src_lo: le16, src_hi: le16, dst_lo: le16, dst_hi: le16,
 *     proto: u8, flags: u8
 *   wl_count: le32
 *   wl[wl_count]:
 *     prefixlen: le32, src_addr: be32, vip_pps: le64, vip_bps: le64,
 *     vip_flags: u8
 *   sbl_count: le32
 *   sbl[sbl_count]: prefixlen: le32, src_addr: be32
 *
 * schema_version must increase for every layout change. Readers reject an
 * unknown version before touching data-plane maps.
 */
#define APPLY_SNAPSHOT_SERVICE_FIXED_SIZE 33U
#define APPLY_SNAPSHOT_RULE_SIZE 10U
#define APPLY_SNAPSHOT_WHITELIST_ENTRY_SIZE 25U
#define APPLY_SNAPSHOT_SERVICE_BLACKLIST_ENTRY_SIZE 8U

#endif

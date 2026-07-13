// SPDX-License-Identifier: GPL-2.0
/*
 * Parse-only self-test for the apply_snapshot wire format (DBS-10).
 *
 * Binds the committed golden fixture to the C parser: decodes
 * tests/fixtures/apply_snapshot_golden.bin with xdpgw-apply's parse_snapshot
 * and asserts every field. The Python serializer (M4 #2 T6
 * serialize_node_snapshot) must emit byte-identical output for the same node,
 * so this fixture is the single artifact both sides round-trip through. Exits
 * non-zero on any mismatch. Run by `make apply`.
 */
#define XDPGW_APPLY_NO_MAIN
#include "../tools/xdpgw-apply.c"

#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>

static int check_golden(const char *path)
{
	struct node_cfg node;
	int err = -1;

	if (parse_snapshot(path, &node) != 0) {
		fprintf(stderr, "test_snapshot: parse failed for %s\n", path);
		return -1;
	}

#define CHECK(cond, msg)                                            \
	do {                                                        \
		if (!(cond)) {                                      \
			fprintf(stderr, "test_snapshot: %s\n", msg); \
			goto out;                                  \
		}                                                  \
	} while (0)

	CHECK(node.schema_version == APPLY_SNAPSHOT_SCHEMA_VERSION,
	      "schema_version");
	CHECK(node.service_count == 2, "service_count");

	const struct cfg_service *a = &node.services[0];

	CHECK(a->dst_prefixlen == 32, "svc0 dst_prefixlen");
	CHECK(a->dst_addr == inet_addr("10.0.0.2"), "svc0 dst_addr");
	CHECK(a->dp_id == 42, "svc0 dp_id");
	CHECK(a->enabled == 1, "svc0 enabled");
	CHECK(a->wl_flags == WL_F_ACTIVE, "svc0 wl_flags");
	CHECK(a->bl_flags == 0, "svc0 bl_flags");
	CHECK(a->committed_bps == 1000000000ULL, "svc0 committed_bps");
	CHECK(a->ceiling_bps == 2000000000ULL, "svc0 ceiling_bps");
	CHECK(a->vip_pps == 1000, "svc0 vip_pps");
	CHECK(a->vip_bps == 8000000ULL, "svc0 vip_bps");
	CHECK(a->vip_flags == (VIP_F_PPS_SET | VIP_F_BPS_SET), "svc0 vip_flags");
	CHECK(a->rule_count == 1, "svc0 rule_count");
	CHECK(a->rules[0].src_lo == 0 && a->rules[0].src_hi == 65535,
	      "svc0 rule src range");
	CHECK(a->rules[0].dst_lo == 0 && a->rules[0].dst_hi == 65535,
	      "svc0 rule dst range");
	CHECK(a->rules[0].proto == 0, "svc0 rule proto");
	CHECK(a->rules[0].flags == RULE_F_ENABLED, "svc0 rule flags");
	CHECK(a->wl_count == 1, "svc0 wl_count");
	CHECK(a->wl[0].prefixlen == 24, "svc0 wl prefixlen");
	CHECK(a->wl[0].addr == inet_addr("192.51.100.0"), "svc0 wl addr");
	CHECK(a->sbl_count == 1, "svc0 sbl_count");
	CHECK(a->sbl[0].prefixlen == 32, "svc0 sbl prefixlen");
	CHECK(a->sbl[0].addr == inet_addr("203.0.113.5"), "svc0 sbl addr");

	const struct cfg_service *b = &node.services[1];

	CHECK(b->dst_prefixlen == 32, "svc1 dst_prefixlen");
	CHECK(b->dst_addr == inet_addr("10.0.0.3"), "svc1 dst_addr");
	CHECK(b->dp_id == 43, "svc1 dp_id");
	CHECK(b->enabled == 1, "svc1 enabled");
	CHECK(b->wl_flags == 0, "svc1 wl_flags");
	CHECK(b->committed_bps == 0, "svc1 committed_bps");
	CHECK(b->ceiling_bps == 500000000ULL, "svc1 ceiling_bps");
	CHECK(b->vip_pps == 0 && b->vip_bps == 0, "svc1 vip zero");
	CHECK(b->vip_flags == 0, "svc1 vip_flags");
	CHECK(b->rule_count == 0, "svc1 rule_count");
	CHECK(b->wl_count == 0, "svc1 wl_count");
	CHECK(b->sbl_count == 0, "svc1 sbl_count");

	err = 0;
#undef CHECK
out:
	free_node_cfg(&node);
	return err;
}

int main(int argc, char **argv)
{
	const char *path = argc > 1 ? argv[1]
				    : "tests/fixtures/apply_snapshot_golden.bin";

	if (check_golden(path) != 0)
		return 1;

	printf("test_snapshot: %s parsed OK\n", path);
	return 0;
}

// SPDX-License-Identifier: GPL-2.0
/*
 * Parse-only self-test for the apply_snapshot wire format (DBS-10).
 *
 * Binds the committed golden fixtures to the C parser: decodes the service and
 * global-deny contracts with xdpgw-apply's parse_snapshot and asserts every
 * field. Exits non-zero on any mismatch. Run by `make apply`.
 */
#define XDPGW_APPLY_NO_MAIN
#include "../tools/xdpgw-apply.c"

#include <arpa/inet.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int check_service_golden(const char *path)
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
	CHECK(node.snapshot_kind == APPLY_SNAPSHOT_KIND_SERVICE_FULL,
	      "service snapshot_kind");
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

static int write_all(int fd, const uint8_t *data, size_t len)
{
	while (len > 0) {
		ssize_t written = write(fd, data, len);

		if (written <= 0)
			return -1;
		data += written;
		len -= (size_t)written;
	}
	return 0;
}

static int read_file(const char *path, uint8_t **data_out, size_t *len_out)
{
	FILE *f;
	long size;
	uint8_t *data;

	*data_out = NULL;
	*len_out = 0;
	f = fopen(path, "rb");
	if (!f)
		return -1;
	if (fseek(f, 0, SEEK_END) != 0 || (size = ftell(f)) < 0 ||
	    fseek(f, 0, SEEK_SET) != 0) {
		fclose(f);
		return -1;
	}
	data = malloc((size_t)size);
	if (!data) {
		fclose(f);
		return -1;
	}
	if (fread(data, 1, (size_t)size, f) != (size_t)size) {
		free(data);
		fclose(f);
		return -1;
	}
	fclose(f);
	*data_out = data;
	*len_out = (size_t)size;
	return 0;
}

static int check_rejected(const char *name, const uint8_t *data, size_t len)
{
	char path[] = "/tmp/xdpgw-snapshot-XXXXXX";
	struct node_cfg node;
	int fd;
	int err = -1;

	fd = mkstemp(path);
	if (fd < 0)
		return -1;
	if (write_all(fd, data, len) != 0) {
		close(fd);
		unlink(path);
		return -1;
	}
	if (close(fd) != 0) {
		unlink(path);
		return -1;
	}
	if (parse_snapshot(path, &node) == 0) {
		fprintf(stderr, "test_snapshot: accepted %s\n", name);
		free_node_cfg(&node);
		goto out;
	}
	err = 0;
out:
	unlink(path);
	return err;
}

static int check_global_golden(const char *path)
{
	struct node_cfg node;
	uint8_t *data = NULL;
	size_t len = 0;
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
	      "global schema_version");
	CHECK(node.snapshot_kind == APPLY_SNAPSHOT_KIND_GLOBAL_DENY,
	      "global snapshot_kind");
	CHECK(node.global_revision == 42, "global revision");
	CHECK(node.global_count == 3, "global entry count");
	CHECK(node.global_entries[0].prefixlen == 16,
	      "global entry 0 prefixlen");
	CHECK(node.global_entries[0].addr == inet_addr("45.45.0.0"),
	      "global entry 0 addr");
	CHECK(node.global_entries[1].prefixlen == 24,
	      "global entry 1 prefixlen");
	CHECK(node.global_entries[1].addr == inet_addr("192.0.2.0"),
	      "global entry 1 addr");
	CHECK(node.global_entries[2].prefixlen == 32,
	      "global entry 2 prefixlen");
	CHECK(node.global_entries[2].addr == inet_addr("203.0.113.5"),
	      "global entry 2 addr");

	CHECK(read_file(path, &data, &len) == 0, "read global fixture");
	CHECK(len == APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE +
		  3 * APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE,
	      "global fixture size");

	data[12] = 0xff;
	CHECK(check_rejected("unknown kind", data, len) == 0,
	      "reject unknown kind");
	data[12] = APPLY_SNAPSHOT_KIND_GLOBAL_DENY;

	data[8] = APPLY_SNAPSHOT_SCHEMA_VERSION + 1;
	CHECK(check_rejected("unknown version", data, len) == 0,
	      "reject unknown version");
	data[8] = APPLY_SNAPSHOT_SCHEMA_VERSION;

	CHECK(check_rejected("truncation", data, len - 1) == 0,
	      "reject truncation");

	data[APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE] = 33;
	CHECK(check_rejected("invalid prefix", data, len) == 0,
	      "reject invalid prefix");
	data[APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE] = 16;

	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE +
		       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE,
	       data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE,
	       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE);
	CHECK(check_rejected("duplicate entries", data, len) == 0,
	      "reject duplicate entries");
	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE +
		       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE,
	       "\x18\x00\x00\x00\xc0\x00\x02\x00",
	       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE);

	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE,
	       data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE +
		       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE,
	       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE);
	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE +
		       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE,
	       "\x10\x00\x00\x00\x2d\x2d\x00\x00",
	       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE);
	CHECK(check_rejected("unsorted entries", data, len) == 0,
	      "reject unsorted entries");
	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE,
	       "\x10\x00\x00\x00\x2d\x2d\x00\x00",
	       APPLY_SNAPSHOT_GLOBAL_ENTRY_SIZE);

	memcpy(data + APPLY_SNAPSHOT_GLOBAL_HEADER_SIZE - 4,
	       "\x01\x00\x10\x00", 4);
	CHECK(check_rejected("count above limit", data, len) == 0,
	      "reject count above limit");

	err = 0;
#undef CHECK
out:
	free(data);
	free_node_cfg(&node);
	return err;
}

int main(int argc, char **argv)
{
	const char *service_path = argc > 1 ? argv[1]
					    : "tests/fixtures/apply_snapshot_golden.bin";
	const char *global_path = argc > 2 ? argv[2]
					   : "tests/fixtures/global_deny_snapshot_golden.bin";

	if (check_service_golden(service_path) != 0 ||
	    check_global_golden(global_path) != 0)
		return 1;

	printf("test_snapshot: %s and %s parsed OK\n", service_path,
	       global_path);
	return 0;
}

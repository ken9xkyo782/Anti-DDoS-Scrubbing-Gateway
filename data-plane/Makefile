BPF_CLANG ?= clang
BPFTOOL ?= bpftool
CC ?= gcc
LLVM_STRIP ?= llvm-strip

BUILD_DIR := build
SRC_DIR := src
LOADER_DIR := loader
TEST_DIR := tests

BPF_SRC := $(SRC_DIR)/xdp_gateway.bpf.c
BPF_OBJ := $(BUILD_DIR)/xdp_gateway.bpf.o
TEST_BPF_OBJ := $(BUILD_DIR)/xdp_gateway.test.bpf.o
SKEL := $(BUILD_DIR)/xdp_gateway.skel.h
TEST_SKEL := $(BUILD_DIR)/xdp_gateway.test.skel.h
LOADER_SRC := $(LOADER_DIR)/loader.c
LOADER := $(BUILD_DIR)/xdp_gateway_loader
TEST_SRC := $(TEST_DIR)/test_parse.c
TEST_BIN := $(BUILD_DIR)/test_parse

ARCH := $(shell uname -m | sed 's/x86_64/x86/' | sed 's/aarch64/arm64/')
UAPI_INCLUDE := /usr/include/$(shell uname -m)-linux-gnu

BPF_CFLAGS := -g -O2 -Wall -Wextra -target bpf -D__TARGET_ARCH_$(ARCH) -I$(SRC_DIR) -I$(UAPI_INCLUDE)
LIBBPF_CFLAGS := $(shell pkg-config --cflags libbpf 2>/dev/null)
LIBBPF_LIBS := $(shell pkg-config --libs libbpf 2>/dev/null || echo -lbpf)
USER_CFLAGS := -g -O2 -Wall -Wextra -I$(BUILD_DIR) -I$(SRC_DIR) $(LIBBPF_CFLAGS)

.PHONY: all bpf skel loader test clean run

all: bpf skel loader

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

bpf: $(BPF_OBJ)

$(BPF_OBJ): $(BPF_SRC) $(SRC_DIR)/pkt_meta.h $(SRC_DIR)/drop_reason.h $(SRC_DIR)/parse.h $(SRC_DIR)/service.h | $(BUILD_DIR)
	$(BPF_CLANG) $(BPF_CFLAGS) -c $< -o $@
	$(LLVM_STRIP) -g $@

$(TEST_BPF_OBJ): $(BPF_SRC) $(SRC_DIR)/pkt_meta.h $(SRC_DIR)/drop_reason.h $(SRC_DIR)/parse.h $(SRC_DIR)/service.h | $(BUILD_DIR)
	$(BPF_CLANG) $(BPF_CFLAGS) -DPKT_TEST_HOOKS -c $< -o $@
	$(LLVM_STRIP) -g $@

skel: $(SKEL)

$(SKEL): $(BPF_OBJ)
	$(BPFTOOL) gen skeleton $< > $@

$(TEST_SKEL): $(TEST_BPF_OBJ)
	$(BPFTOOL) gen skeleton $< > $@

loader: $(LOADER)

$(LOADER): $(LOADER_SRC) $(SKEL) | $(BUILD_DIR)
	$(CC) $(USER_CFLAGS) $< -o $@ $(LIBBPF_LIBS) -lelf -lz

test: $(TEST_BIN)
	$(TEST_BIN)

$(TEST_BIN): $(TEST_SRC) $(TEST_DIR)/pkt_build.h $(TEST_SKEL) | $(BUILD_DIR)
	$(CC) $(USER_CFLAGS) -I$(SRC_DIR) -I$(TEST_DIR) $< -o $@ $(LIBBPF_LIBS) -lelf -lz

run: loader
	@if [ -z "$(IFACE)" ] || [ -z "$(OUT)" ]; then echo "usage: make run IFACE=<in-ifname> OUT=<out-ifname> [SERVICE_DEST=<ipv4-or-cidr>]"; exit 2; fi
	sudo SERVICE_DEST="$(SERVICE_DEST)" $(LOADER) $(IFACE) $(OUT)

clean:
	rm -rf $(BUILD_DIR)

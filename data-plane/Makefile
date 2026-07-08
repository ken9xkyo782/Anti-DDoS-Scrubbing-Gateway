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
SKEL := $(BUILD_DIR)/xdp_gateway.skel.h
LOADER := $(BUILD_DIR)/xdp_gateway_loader

ARCH := $(shell uname -m | sed 's/x86_64/x86/' | sed 's/aarch64/arm64/')
UAPI_INCLUDE := /usr/include/$(shell uname -m)-linux-gnu

BPF_CFLAGS := -g -O2 -Wall -Wextra -target bpf -D__TARGET_ARCH_$(ARCH) -I$(SRC_DIR) -I$(UAPI_INCLUDE)

.PHONY: all bpf skel loader test clean run

all: bpf skel loader

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

bpf: $(BPF_OBJ)

$(BPF_OBJ): $(BPF_SRC) $(SRC_DIR)/pkt_meta.h $(SRC_DIR)/drop_reason.h | $(BUILD_DIR)
	$(BPF_CLANG) $(BPF_CFLAGS) -c $< -o $@
	$(LLVM_STRIP) -g $@

skel: $(SKEL)

$(SKEL): $(BPF_OBJ)
	$(BPFTOOL) gen skeleton $< > $@

loader:
	@echo "loader target is added in T2"

test:
	@echo "test target is added in T3"

run: loader
	@echo "run target is added in T2"

clean:
	rm -rf $(BUILD_DIR)

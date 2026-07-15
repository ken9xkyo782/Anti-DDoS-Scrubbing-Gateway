# Makefile điều phối triển khai đơn-node cho Anti-DDoS Scrubbing Gateway.
#
#   make deploy   → dựng TẤT CẢ trong 1 lệnh (hạ tầng + build + cài đặt + chạy)
#   make help     → xem toàn bộ target và biến override
#
# Các tiến trình nền (loader, worker, api) chạy dưới nền, PID và log lưu ở .run/.
# Data-plane loader cần quyền root; nếu không phải root, bước đó được bỏ qua có
# cảnh báo và phần còn lại của hệ thống vẫn chạy (gateway ở trạng thái offline).

SHELL := /bin/bash

# ---- Đường dẫn ----
ROOT          := $(CURDIR)
DATA_PLANE    := $(ROOT)/data-plane
CONTROL_PLANE := $(ROOT)/control-plane
FRONTEND      := $(CONTROL_PLANE)/frontend
COMPOSE       := $(CONTROL_PLANE)/compose.test.yml
FE_STATIC     := $(FRONTEND)/dist

# ---- Python / venv ----
VENV := $(CONTROL_PLANE)/.venv
PY   := $(VENV)/bin/python

# ---- Trạng thái runtime (PID + log các tiến trình nền) ----
RUN_DIR := $(ROOT)/.run
LOG_DIR := $(RUN_DIR)/logs

# ---- Biến có thể override: make deploy API_PORT=9000 IN_IFACE=eth0 ... ----
API_HOST       ?= 0.0.0.0
API_PORT       ?= 8000
ADMIN_USER     ?= admin
ADMIN_PASSWORD ?= change-me-please
COOKIE_SECURE  ?= false
IN_IFACE       ?= xdpgwin0
OUT_IFACE      ?= xdpgwout0
SERVICE_DEST   ?= 10.0.0.2
# Cặp veth demo chỉ được tự tạo khi dùng đúng tên interface mặc định dưới đây.
DEMO_IN  := xdpgwin0
DEMO_OUT := xdpgwout0

.DEFAULT_GOAL := help

.PHONY: help deploy down teardown restart start stop status logs health \
        infra infra-down \
        build install cp-install fe-install migrate bootstrap setup \
        dp-build dp-veth dp-start dp-stop \
        fe-build \
        api-start api-stop worker-start worker-stop \
        clean

# ============================================================================
# Lệnh tổng hợp
# ============================================================================

deploy: ## ★ Deploy toàn bộ hệ thống trong 1 lệnh
	$(MAKE) infra
	$(MAKE) dp-build
	$(MAKE) cp-install
	$(MAKE) fe-install
	$(MAKE) migrate
	$(MAKE) bootstrap
	$(MAKE) fe-build
	$(MAKE) dp-start
	$(MAKE) worker-start
	$(MAKE) api-start
	@echo ""
	@echo "════════════════════════════════════════════════════════════"
	@echo " Deploy hoàn tất."
	@echo "   API + SPA : http://$(API_HOST):$(API_PORT)  (SPA tại /tenant, /admin)"
	@echo "   Admin     : $(ADMIN_USER) / $(ADMIN_PASSWORD)"
	@echo "   Quản lý   : make status | make logs | make health | make down"
	@echo "════════════════════════════════════════════════════════════"

build: dp-build fe-build ## Build data-plane + bản production của SPA

install: cp-install fe-install ## Cài phụ thuộc Python (venv) + frontend

setup: migrate bootstrap ## Áp migration + tạo tài khoản admin

start: dp-start worker-start api-start ## Khởi động các tiến trình nền (loader, worker, api)

stop: api-stop worker-stop dp-stop ## Dừng các tiến trình nền

restart: ## Khởi động lại các tiến trình nền
	$(MAKE) stop
	$(MAKE) start

down: ## Dừng tiến trình nền và hạ Postgres/Redis
	-$(MAKE) stop
	$(MAKE) infra-down

teardown: down ## Bí danh của `down`

# ============================================================================
# Hạ tầng (Postgres + Redis qua Docker Compose)
# ============================================================================

infra: ## Dựng Postgres + Redis và chờ healthy
	docker compose -f $(COMPOSE) up -d
	@echo "→ chờ Postgres/Redis healthy..."
	@for i in $$(seq 1 30); do \
		unhealthy=$$(docker compose -f $(COMPOSE) ps --format '{{.Service}} {{.Health}}' 2>/dev/null | grep -v ' healthy' || true); \
		if [ -z "$$unhealthy" ]; then echo "  hạ tầng healthy"; break; fi; \
		sleep 2; \
	done
	@docker compose -f $(COMPOSE) ps

infra-down: ## Hạ Postgres + Redis
	docker compose -f $(COMPOSE) down

# ============================================================================
# Data-plane (XDP/eBPF)
# ============================================================================

dp-build: ## Build data-plane (bpf, skel, loader, apply, dpstat)
	$(MAKE) -C $(DATA_PLANE) bpf skel loader apply dpstat

dp-veth: ## Tạo cặp veth demo (chỉ khi dùng interface mặc định)
	@if [ "$(IN_IFACE)" = "$(DEMO_IN)" ] && [ "$(OUT_IFACE)" = "$(DEMO_OUT)" ]; then \
		if ! ip link show $(DEMO_IN) >/dev/null 2>&1; then \
			echo "→ tạo cặp veth demo $(DEMO_IN)/$(DEMO_OUT)"; \
			ip link add $(DEMO_IN) type veth peer name $(DEMO_IN)p; \
			ip link add $(DEMO_OUT) type veth peer name $(DEMO_OUT)p; \
			ip link set $(DEMO_IN) up;  ip link set $(DEMO_IN)p up; \
			ip link set $(DEMO_OUT) up; ip link set $(DEMO_OUT)p up; \
		fi; \
	fi

dp-start: dp-build ## Nạp data-plane loader dưới nền (cần root)
	@mkdir -p $(RUN_DIR) $(LOG_DIR)
	@if [ "$$(id -u)" -ne 0 ]; then \
		echo "⚠  bỏ qua data-plane loader: cần root (chạy 'sudo make dp-start')"; \
	elif [ -f $(RUN_DIR)/loader.pid ] && kill -0 "$$(cat $(RUN_DIR)/loader.pid)" 2>/dev/null; then \
		echo "loader đã chạy (pid $$(cat $(RUN_DIR)/loader.pid))"; \
	else \
		$(MAKE) --no-print-directory dp-veth; \
		rm -rf /sys/fs/bpf/xdp_gateway; \
		cd $(DATA_PLANE); \
		SERVICE_DEST="$(SERVICE_DEST)" ./build/xdp_gateway_loader $(IN_IFACE) $(OUT_IFACE) >"$(LOG_DIR)/loader.log" 2>&1 & \
		echo $$! > $(RUN_DIR)/loader.pid; \
		disown 2>/dev/null || true; \
		sleep 1; \
		if kill -0 "$$(cat $(RUN_DIR)/loader.pid)" 2>/dev/null; then \
			echo "loader chạy (pid $$(cat $(RUN_DIR)/loader.pid)) trên $(IN_IFACE)→$(OUT_IFACE) [SERVICE_DEST=$(SERVICE_DEST)]"; \
		else \
			echo "✗ loader thoát sớm — xem $(LOG_DIR)/loader.log:"; tail -n 15 "$(LOG_DIR)/loader.log"; exit 1; \
		fi; \
	fi

dp-stop: ## Dừng loader và gỡ các map đã ghim
	@if [ -f $(RUN_DIR)/loader.pid ]; then \
		kill "$$(cat $(RUN_DIR)/loader.pid)" 2>/dev/null || true; \
		rm -f $(RUN_DIR)/loader.pid; echo "loader dừng"; \
	else echo "loader không chạy"; fi
	@rm -rf /sys/fs/bpf/xdp_gateway 2>/dev/null || true

# ============================================================================
# Control-plane (API + worker)
# ============================================================================

cp-install: ## Tạo venv control-plane và cài phụ thuộc Python
	@if [ ! -x "$(PY)" ]; then echo "→ tạo venv $(VENV)"; python3 -m venv $(VENV); fi
	cd $(CONTROL_PLANE) && $(PY) -m pip install --quiet --upgrade pip
	cd $(CONTROL_PLANE) && $(PY) -m pip install -e '.[dev]'

migrate: ## Áp migration cơ sở dữ liệu (alembic upgrade head)
	cd $(CONTROL_PLANE) && $(VENV)/bin/alembic upgrade head

bootstrap: ## Tạo tài khoản admin đầu tiên (idempotent)
	cd $(CONTROL_PLANE) && \
		CONTROL_PLANE_BOOTSTRAP_ADMIN_USERNAME="$(ADMIN_USER)" \
		CONTROL_PLANE_BOOTSTRAP_ADMIN_PASSWORD="$(ADMIN_PASSWORD)" \
		$(PY) -m app.cli bootstrap-admin

api-start: ## Khởi động API (Uvicorn) dưới nền, phục vụ cả SPA nếu đã build
	@mkdir -p $(RUN_DIR) $(LOG_DIR)
	@if [ -f $(RUN_DIR)/api.pid ] && kill -0 "$$(cat $(RUN_DIR)/api.pid)" 2>/dev/null; then \
		echo "api đã chạy (pid $$(cat $(RUN_DIR)/api.pid))"; \
	else \
		cd $(CONTROL_PLANE); \
		export CONTROL_PLANE_COOKIE_SECURE="$(COOKIE_SECURE)"; \
		if [ -f "$(FE_STATIC)/index.html" ]; then export CONTROL_PLANE_FRONTEND_STATIC_DIR="$(FE_STATIC)"; fi; \
		$(VENV)/bin/uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) >"$(LOG_DIR)/api.log" 2>&1 & \
		echo $$! > $(RUN_DIR)/api.pid; \
		disown 2>/dev/null || true; \
		sleep 1; \
		echo "api khởi động (pid $$(cat $(RUN_DIR)/api.pid)) → http://$(API_HOST):$(API_PORT)"; \
	fi

api-stop: ## Dừng API
	@if [ -f $(RUN_DIR)/api.pid ]; then \
		kill "$$(cat $(RUN_DIR)/api.pid)" 2>/dev/null || true; \
		rm -f $(RUN_DIR)/api.pid; echo "api dừng"; \
	else echo "api không chạy"; fi

worker-start: ## Khởi động worker dưới nền
	@mkdir -p $(RUN_DIR) $(LOG_DIR)
	@if [ -f $(RUN_DIR)/worker.pid ] && kill -0 "$$(cat $(RUN_DIR)/worker.pid)" 2>/dev/null; then \
		echo "worker đã chạy (pid $$(cat $(RUN_DIR)/worker.pid))"; \
	else \
		cd $(CONTROL_PLANE); \
		$(PY) -m app.worker >"$(LOG_DIR)/worker.log" 2>&1 & \
		echo $$! > $(RUN_DIR)/worker.pid; \
		disown 2>/dev/null || true; \
		sleep 1; \
		echo "worker khởi động (pid $$(cat $(RUN_DIR)/worker.pid))"; \
	fi

worker-stop: ## Dừng worker
	@if [ -f $(RUN_DIR)/worker.pid ]; then \
		kill "$$(cat $(RUN_DIR)/worker.pid)" 2>/dev/null || true; \
		rm -f $(RUN_DIR)/worker.pid; echo "worker dừng"; \
	else echo "worker không chạy"; fi

# ============================================================================
# Frontend (SPA)
# ============================================================================

fe-install: ## Cài phụ thuộc frontend (npm ci)
	cd $(FRONTEND) && npm ci

fe-build: ## Build bản production của SPA
	cd $(FRONTEND) && npm run build

# ============================================================================
# Vận hành
# ============================================================================

status: ## Trạng thái các tiến trình nền và hạ tầng
	@for svc in loader worker api; do \
		f=$(RUN_DIR)/$$svc.pid; \
		if [ -f $$f ] && kill -0 "$$(cat $$f)" 2>/dev/null; then \
			echo "  $$svc  : đang chạy (pid $$(cat $$f))"; \
		else echo "  $$svc  : dừng"; fi; \
	done
	@echo "  infra :"; docker compose -f $(COMPOSE) ps 2>/dev/null || true

logs: ## Xem 40 dòng log gần nhất của mỗi tiến trình nền
	@tail -n 40 -v $(LOG_DIR)/*.log 2>/dev/null || echo "chưa có log ở $(LOG_DIR)"

health: ## Kiểm tra API còn sống
	@curl -fsS http://127.0.0.1:$(API_PORT)/health && echo "  ← API OK" || echo "API chưa phản hồi"

clean: ## Dọn artifact build (data-plane, dist, .run/)
	-$(MAKE) -C $(DATA_PLANE) clean
	rm -rf $(FE_STATIC) $(RUN_DIR)

help: ## Hiện danh sách target
	@echo "Anti-DDoS Scrubbing Gateway — triển khai đơn-node"
	@echo ""
	@echo "Target:"
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Biến override (make <target> VAR=giá_trị):"
	@echo "  API_HOST=$(API_HOST)  API_PORT=$(API_PORT)  COOKIE_SECURE=$(COOKIE_SECURE)"
	@echo "  ADMIN_USER=$(ADMIN_USER)  ADMIN_PASSWORD=$(ADMIN_PASSWORD)"
	@echo "  IN_IFACE=$(IN_IFACE)  OUT_IFACE=$(OUT_IFACE)  SERVICE_DEST=$(SERVICE_DEST)"

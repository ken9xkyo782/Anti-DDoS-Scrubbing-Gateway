# Hướng dẫn cài đặt thủ công và kiểm thử thủ công từng thành phần

Tài liệu này hướng dẫn cách **cài đặt bằng tay** và **kiểm thử bằng tay từng
thành phần** của Anti-DDoS Scrubbing Gateway trên một máy Linux duy nhất. Mục
tiêu là để bạn dựng được toàn bộ hệ thống từ mã nguồn, hiểu điểm ghép nối giữa
các thành phần, và tự xác minh mỗi phần hoạt động đúng trước khi ghép chúng lại.

Các lệnh trong tài liệu chạy trực tiếp trên máy chủ, không dùng trình điều phối
container cho ứng dụng. Postgres và Redis có thể chạy qua Docker Compose (nhanh,
dùng đúng cổng mà cấu hình mặc định trỏ tới) hoặc cài trực tiếp lên máy.

> Quy ước: dấu nhắc `$` là lệnh chạy dưới người dùng thường, `#` là lệnh cần
> quyền `root`/`CAP_NET_ADMIN`. Thay các giá trị trong `<...>` bằng giá trị thật.

---

## Mục lục

1. [Kiến trúc và luồng dữ liệu](#1-kiến-trúc-và-luồng-dữ-liệu)
2. [Yêu cầu hệ thống và phụ thuộc](#2-yêu-cầu-hệ-thống-và-phụ-thuộc)
3. [Data-plane (XDP/eBPF)](#3-data-plane-xdpebpf)
4. [Control-plane API (FastAPI)](#4-control-plane-api-fastapi)
5. [Worker (tiến trình nền)](#5-worker-tiến-trình-nền)
6. [Frontend SPA (React/Vite)](#6-frontend-spa-reactvite)
7. [Kiểm thử tích hợp đầu-cuối](#7-kiểm-thử-tích-hợp-đầu-cuối)
8. [Tra cứu nhanh biến môi trường](#8-tra-cứu-nhanh-biến-môi-trường)
9. [Xử lý sự cố thường gặp](#9-xử-lý-sự-cố-thường-gặp)

---

## 1. Kiến trúc và luồng dữ liệu

Hệ thống có ba thành phần triển khai độc lập:

| Thành phần | Ngôn ngữ | Vai trò |
| --- | --- | --- |
| **Data-plane** | C, XDP/eBPF | Chạy trong nhân, phân loại từng gói tin ở tốc độ cao, redirect gói sạch, drop gói tấn công, đếm số liệu. |
| **Control-plane API** | Python 3.12, FastAPI | API quản trị: tenant, dịch vụ, whitelist/blacklist, feed, cấu hình, đọc số liệu. |
| **Worker** | Python 3.12 | Tiến trình nền cùng codebase với API: áp cấu hình xuống data-plane, thu số liệu, đo cước, đánh giá cảnh báo, đồng bộ feed. |
| **Frontend SPA** | React 19, Vite | Bảng điều khiển cho tenant và quản trị viên, gọi API bằng cookie phiên. |

Luồng đi của một thay đổi cấu hình và của số liệu quan sát:

```
       (trình duyệt)                 (HTTP + cookie)
  Frontend SPA  ───────────────────▶  Control-plane API  ──┐
                                             │             │ ghi
                                             │ đọc         ▼
                                             │        Postgres + Redis
                                             │             │
                                             │        đọc/hàng đợi
                                             ▼             │
   dpstat/xdpgw-apply  ◀───────────────  Worker  ◀─────────┘
  (đọc map / lật slot)   subprocess        (các lane nền)
        │  ▲
   ghi  │  │ đọc counter
        ▼  │
   Data-plane XDP (map ghim dưới /sys/fs/bpf/xdp_gateway/)
        ▲
        │ gói tin vào/ra NIC (hoặc veth khi thử nghiệm)
```

Điểm ghép nối cần nhớ:

- Worker gọi hai chương trình C của data-plane qua `subprocess`: **`dpstat`**
  để đọc số liệu và trạng thái, **`xdpgw-apply`** để dựng và lật cấu hình BPF.
- Data-plane **loader** phải đang chạy và **sở hữu các map đã ghim** dưới
  `/sys/fs/bpf/xdp_gateway/` thì worker mới đọc/ghi được. Không có loader, worker
  vẫn chạy nhưng ghi nhận gateway ở trạng thái `offline`.
- API và worker **dùng chung** một Postgres và một Redis.

---

## 2. Yêu cầu hệ thống và phụ thuộc

### 2.1 Nền tảng

- Linux nhân hỗ trợ XDP native (DRV). Bản này đã kiểm chứng trên nhân
  `6.8.x`.
- Quyền `root`/`CAP_NET_ADMIN` để nạp chương trình XDP và tạo veth.

### 2.2 Gói phụ thuộc theo thành phần

| Thành phần | Phụ thuộc |
| --- | --- |
| Data-plane | `clang`, `llvm-strip`, `bpftool`, thư viện `libbpf` (kèm header), header UAPI của nhân, `gcc`, `make`, `iproute2` (`ip`), `python3`. |
| Control-plane / Worker | Python `>=3.12`, `pip`/`venv`, Postgres 16, Redis 7. (Tùy chọn: Docker để chạy Postgres + Redis qua `compose.test.yml`.) |
| Frontend | Node.js 18+ và `npm`. |

Cài phụ thuộc data-plane trên Ubuntu/Debian:

```bash
# apt-get update
# apt-get install -y clang llvm bpftool libbpf-dev gcc make iproute2 \
      linux-headers-$(uname -r) python3
```

### 2.3 Kiểm tra công cụ đã sẵn sàng

```bash
$ clang --version | head -1
$ bpftool version | head -1
$ gcc --version | head -1
$ python3 --version      # cần >= 3.12
$ node --version         # cần >= 18
$ docker --version       # tùy chọn
```

---

## 3. Data-plane (XDP/eBPF)

Thư mục làm việc: `data-plane/`. Toàn bộ bước build và test đơn vị đều **không
cần** Postgres, Redis hay Python control-plane.

### 3.1 Build

Build chương trình BPF, skeleton libbpf, loader, công cụ vận hành và trình áp
cấu hình:

```bash
$ cd data-plane
$ make bpf skel loader apply dpstat
```

Kết quả trong `build/`:

| Tệp | Vai trò |
| --- | --- |
| `xdp_gateway.bpf.o` | Object BPF đã biên dịch. |
| `xdp_gateway.skel.h` | Skeleton libbpf sinh bằng `bpftool`. |
| `xdp_gateway_loader` | Loader nạp và ghim map. |
| `dpstat` | CLI vận hành: đọc counter, snapshot, bật/tắt bypass. |
| `xdpgw-apply` | Trình dựng/verify/lật slot cấu hình BPF. |

`make apply` đồng thời build `test_snapshot` và chạy self-test phân tích snapshot
với hai golden fixture, nên hoàn tất mà không báo lỗi nghĩa là định dạng wire
`apply_snapshot.h` khớp giữa parser C và bộ serialize Python.

### 3.2 Kiểm thử đơn vị (dp-unit)

Chạy bộ test dựa trên `BPF_PROG_TEST_RUN` với các khung tin tổng hợp:

```bash
$ make test
```

- Kỳ vọng: **130** test đơn vị **pass**, cộng self-test golden của
  `test_snapshot` cho cả snapshot dịch vụ và snapshot global-deny.
- Bộ này kiểm chứng verdict cho IPv6, EtherType lạ, IPv4 dị dạng, phân mảnh,
  bogon, tra cứu dịch vụ, allow-rule, rate-limit, whitelist/VIP, blacklist,
  bloom→LPM, lấy mẫu drop, và toàn bộ đường dựng/verify/lật của `xdpgw-apply`.

### 3.3 Chạy loader thủ công trên cặp veth

Bước này nạp chương trình XDP thật để bạn quan sát được map và counter bằng
`dpstat`. Loader gắn vào giao diện **IN** ở chế độ native (DRV) và điền ifindex
của **OUT** vào `tx_devmap[0]`; nó **không** rơi về chế độ generic/SKB.

1. Tạo hai cặp veth và bật chúng lên:

   ```bash
   # ip link add vethA type veth peer name vethIN
   # ip link add vethOUT type veth peer name vethSINK
   # ip link set vethA up && ip link set vethIN up
   # ip link set vethOUT up && ip link set vethSINK up
   ```

2. Xóa pin cũ nếu còn (loader từ chối khởi động khi thư mục pin đã tồn tại):

   ```bash
   # rm -rf /sys/fs/bpf/xdp_gateway
   ```

3. Nạp loader kèm một dịch vụ demo (địa chỉ không có prefix được coi là `/32`):

   ```bash
   # cd data-plane
   # SERVICE_DEST=10.0.0.2 ./build/xdp_gateway_loader vethIN vethOUT
   ```

   Loader ghim các map quan sát dưới `/sys/fs/bpf/xdp_gateway/` rồi chờ. Nhấn
   `Ctrl-C` để gỡ; sau đó `ip link show vethIN` không còn hiển thị chương trình
   XDP và loader tự xóa thư mục pin.

4. Xác nhận đã gắn:

   ```bash
   # ip link show vethIN        # phải thấy prog XDP kèm id
   # ls /sys/fs/bpf/xdp_gateway  # phải thấy các map đã ghim
   ```

Các biến seed tùy chọn để demo hành vi trước khi worker áp cấu hình thật:

| Biến | Ý nghĩa |
| --- | --- |
| `SERVICE_DEST` | Đích dịch vụ demo (IPv4 hoặc CIDR canonical). Kèm khối allow-rule match-all không quota. |
| `XDPGW_SEED_WL_CIDR` | Seed whitelist theo CIDR nguồn (cần một trần VIP). |
| `XDPGW_SEED_VIP_PPS` / `XDPGW_SEED_VIP_BPS` | Trần VIP tổng theo pps/bps. |
| `XDPGW_SEED_GBL_CIDR` / `XDPGW_SEED_SBL_CIDR` | Seed một CIDR blacklist global / theo dịch vụ vào slot 0. |
| `XDPGW_SEED_BLOCKED_PORT` | Bật một bit cổng UDP bị chặn trong bitmap slot 0. |

### 3.4 Kiểm thử thủ công bằng `dpstat`

Chạy song song trong khi loader đang chạy (mở terminal thứ hai):

```bash
# cd data-plane
# ./build/dpstat counters          # tổng drop chính xác theo lý do
# ./build/dpstat counters -w 2      # lặp mỗi 2 giây
# ./build/dpstat tail               # bám dòng các sự kiện drop được lấy mẫu
# ./build/dpstat rate 256 64        # đặt ngân sách mẫu: 256 pps mỗi CPU, burst 64
# ./build/dpstat active_config      # slot và version đang hoạt động
# ./build/dpstat snapshot --json    # toàn bộ đầu vào telemetry ở dạng JSON
```

Điểm cần kiểm khi đọc kết quả:

- `counters` in thêm các dòng `bloom_hit_lpm_miss` cho whitelist, blacklist
  global, blacklist theo dịch vụ và tổng.
- Counter **reset** mỗi khi nạp lại chương trình XDP. Người tiêu thụ phải tính
  **delta** giữa các lần đọc, không coi giá trị là tổng vòng đời.
- `snapshot --json` chứa `ts_ns`, `active.slot`, `active.version`, chế độ và
  metadata XDP, counter node, thống kê mẫu và bloom, cùng mảng `services` đã sắp
  xếp. Thêm `--ifindex <ingress-ifindex>` để báo chế độ XDP `native`/`generic`;
  thiếu nó thì chế độ là `unknown`.
- Nếu một map ghim bắt buộc không đọc được, `dpstat` báo gateway **offline** và
  thoát mã khác 0 thay vì phát snapshot một phần.

Kiểm tra soft-bypass (chuyển tiếp toàn bộ IPv4 hợp lệ, bỏ qua chính sách dịch vụ):

```bash
# ./build/dpstat set-bypass 1
# ./build/dpstat snapshot --json    # kiểm node_control.bypass và bypass.pkts/bytes
# ./build/dpstat set-bypass 0
```

### 3.5 Smoke test có đặc quyền (đường redirect thật)

Smoke tự dựng cặp veth, gắn một prog `XDP_PASS` lên đầu SINK, nạp loader, gửi
một khung IPv4 thủ công và khẳng định gói được `XDP_REDIRECT` với TTL và
checksum IPv4 **không đổi**:

```bash
# cd data-plane
# make smoke
```

`make smoke` chạy tuần tự các kịch bản redirect, bypass, fairness và apply. Đây
là test có đặc quyền và **không** an toàn khi chạy song song vì dùng chung giao
diện và trạng thái gắn XDP của nhân.

### 3.6 Các gate quy mô lớn (chạy khi thay đổi tương ứng)

Chạy từ `data-plane/`:

| Lệnh | Kiểm chứng |
| --- | --- |
| `# make applybulk` | Dựng/verify/lật 1000 dịch vụ trong dưới 5 giây, đúng một lần lật `active_config`, map feed-owned được mang nguyên. |
| `# make blbulk` | Nạp 1.048.576 mục blacklist global + khóa bloom; kiểm tra thành viên bloom/LPM và một verdict `XDP_DROP`. |
| `# make globalapplysmoke` | Đưa một snapshot feed giả qua chính helper thật đến verdict `blacklist_drop`. |
| `# make globalapplyscale` | Nạp 1.048.576 mục, **từ chối** mục thứ 1.048.577 trước khi lật. |

> Các gate này **không** nằm trong `make test`. Chỉ chạy khi bạn đổi dung lượng
> map, hình dạng map, hoặc đường apply.

---

## 4. Control-plane API (FastAPI)

Thư mục làm việc: `control-plane/`.

### 4.1 Chuẩn bị Postgres và Redis

**Cách nhanh (Docker Compose)** — dựng đúng cổng mà cấu hình mặc định trỏ tới
(Postgres `55432`, Redis `56379`):

```bash
$ cd control-plane
$ docker compose -f compose.test.yml up -d
$ docker compose -f compose.test.yml ps   # chờ cả hai ở trạng thái healthy
```

Compose tạo cơ sở dữ liệu `control_plane_test`, người dùng `control_plane`, mật
khẩu `control_plane`.

**Cách cài trực tiếp** — nếu tự cài Postgres/Redis, hãy tạo một database và một
user, rồi đặt `CONTROL_PLANE_DATABASE_URL` / `CONTROL_PLANE_REDIS_URL` ở
[bước cấu hình](#43-cấu-hình-môi-trường) trỏ tới chúng.

### 4.2 Tạo môi trường Python và cài phụ thuộc

```bash
$ cd control-plane
$ python3 -m venv .venv
$ . .venv/bin/activate
$ pip install -e '.[dev]'
```

Lệnh trên cài phụ thuộc runtime (FastAPI, Uvicorn, SQLAlchemy async, asyncpg,
Alembic, Redis, httpx, pydantic-settings, pwdlib) và bộ công cụ dev (pytest,
ruff, mypy).

> Trong môi trường này đã có sẵn venv commit tại `control-plane/.venv/`; có thể
> gọi trực tiếp `control-plane/.venv/bin/{python,pytest,ruff,mypy}` thay cho việc
> tự tạo mới.

### 4.3 Cấu hình môi trường

Cấu hình đọc từ biến môi trường tiền tố `CONTROL_PLANE_` và từ tệp `.env` trong
thư mục làm việc hiện tại. Tạo `control-plane/.env`:

```ini
CONTROL_PLANE_DATABASE_URL=postgresql+asyncpg://control_plane:control_plane@127.0.0.1:55432/control_plane_test
CONTROL_PLANE_REDIS_URL=redis://127.0.0.1:56379/0
CONTROL_PLANE_BOOTSTRAP_ADMIN_USERNAME=admin
CONTROL_PLANE_BOOTSTRAP_ADMIN_PASSWORD=change-me-please
# Chỉ dùng khi test qua HTTP (không TLS): cho phép trình duyệt/curl gửi lại cookie phiên
CONTROL_PLANE_COOKIE_SECURE=false
```

> **Lưu ý về cookie.** Mặc định `cookie_secure=true`, nên cookie phiên chỉ được
> gửi qua HTTPS. Khi kiểm thử cục bộ qua HTTP (curl hoặc `npm run dev`), đặt
> `CONTROL_PLANE_COOKIE_SECURE=false`, nếu không đăng nhập sẽ thành công nhưng
> các lệnh sau đó bị `401` vì cookie không được gửi lại.

### 4.4 Chạy migration cơ sở dữ liệu

Áp toàn bộ migration Alembic lên database:

```bash
$ cd control-plane
$ . .venv/bin/activate
$ alembic upgrade head
```

Kỳ vọng: nâng cấp đến bản `20260714_0011_alerting` (bản head hiện tại) không lỗi.

### 4.5 Tạo tài khoản quản trị đầu tiên

Với hai biến `CONTROL_PLANE_BOOTSTRAP_ADMIN_*` đã đặt, chạy CLI:

```bash
$ python -m app.cli bootstrap-admin
# In ra: bootstrap admin ready: admin
```

### 4.6 Khởi động API

Chạy máy chủ ASGI bằng Uvicorn:

```bash
$ cd control-plane
$ . .venv/bin/activate
$ uvicorn app.main:app --host 0.0.0.0 --port 8000
# Thêm --reload khi phát triển
```

Kiểm tra nhanh còn sống:

```bash
$ curl -s http://127.0.0.1:8000/health
# {"status":"ok"}
```

Tài liệu OpenAPI tương tác có tại `http://127.0.0.1:8000/docs`.

### 4.7 Kiểm thử thủ công qua `curl`

Dùng một tệp cookie jar để giữ phiên đăng nhập giữa các lệnh.

1. **Đăng nhập** (lưu cookie phiên vào `cookies.txt`):

   ```bash
   $ curl -s -c cookies.txt -X POST http://127.0.0.1:8000/auth/login \
       -H 'Content-Type: application/json' \
       -d '{"username":"admin","password":"change-me-please"}'
   ```

2. **Xác minh danh tính phiên**:

   ```bash
   $ curl -s -b cookies.txt http://127.0.0.1:8000/auth/me
   # Trả về id, username, role, tenant_id
   ```

3. **Tạo tenant** (chỉ quản trị viên):

   ```bash
   $ curl -s -b cookies.txt -X POST http://127.0.0.1:8000/tenants \
       -H 'Content-Type: application/json' -d '{"name":"acme"}'
   $ curl -s -b cookies.txt http://127.0.0.1:8000/tenants
   ```

4. **Cấp dải CIDR cho tenant** (ghi lại `tenant_id` từ bước 3):

   ```bash
   $ curl -s -b cookies.txt -X POST http://127.0.0.1:8000/allocations \
       -H 'Content-Type: application/json' \
       -d '{"tenant_id":"<tenant-id>","cidr":"203.0.113.0/24"}'
   ```

5. **Tạo một dịch vụ được bảo vệ** trên dải đã cấp:

   ```bash
   $ curl -s -b cookies.txt -X POST http://127.0.0.1:8000/services \
       -H 'Content-Type: application/json' \
       -d '{"tenant_id":"<tenant-id>","name":"web","cidr_or_ip":"203.0.113.10",
            "plan":{"committed_clean_gbps":"1","ceiling_clean_gbps":"2"}}'
   $ curl -s -b cookies.txt http://127.0.0.1:8000/services
   ```

6. **Thêm whitelist cho dịch vụ** (dùng `service_id` từ bước 5):

   ```bash
   $ curl -s -b cookies.txt -X POST \
       http://127.0.0.1:8000/services/<service-id>/whitelist \
       -H 'Content-Type: application/json' -d '{"source_cidr":"198.51.100.0/24"}'
   ```

7. **Đọc sức khỏe và số liệu node** (chỉ quản trị viên):

   ```bash
   $ curl -s -b cookies.txt http://127.0.0.1:8000/node/health
   $ curl -s -b cookies.txt http://127.0.0.1:8000/node/telemetry
   ```

   Khi chưa có loader/worker, các phản hồi này zero-hóa, `has_data=false`,
   `window_seconds=0` và `stale=true` — đây là hành vi đúng, không phải lỗi.

8. **Xem hàng đợi job của worker** (chỉ quản trị viên):

   ```bash
   $ curl -s -b cookies.txt 'http://127.0.0.1:8000/jobs?status=queued'
   ```

9. **Đăng xuất**:

   ```bash
   $ curl -s -b cookies.txt -X POST http://127.0.0.1:8000/auth/logout -i
   # Kỳ vọng: 204 No Content
   ```

Một số nhóm endpoint khác để kiểm thử tương tự: `/feeds` (feed threat-intel,
quản trị), `/billing/usage` (cước), `/alerts` + `/alerts/rules` +
`/alerts/channels` (cảnh báo), `/blacklist`, `/services/{id}/rules` (allow-rule),
`/node/bypass` và `/node/maintenance`. Xem
[runbook bypass/maintenance](../control-plane/docs/bypass-maintenance-runbook.md)
để biết quy trình vận hành hai control này.

### 4.8 Gate tự động của control-plane

Dựng Postgres + Redis (`compose.test.yml`) trước khi chạy gate có test tích hợp.

| Gate | Lệnh | Khi nào |
| --- | --- | --- |
| **quick** | `ruff check . && ruff format --check . && mypy app/ && pytest -q -m unit` | Vòng lặp nhanh, chỉ test đơn vị. |
| **full** | `ruff check . && ruff format --check . && mypy app/ && pytest -q` | Có test tích hợp (cần compose up). |
| **build** | `python -c "import app.main"` + `alembic upgrade head` | Kiểm tra khung/wiring. |

---

## 5. Worker (tiến trình nền)

Worker dùng **chung** codebase, cấu hình và tệp `.env` với API. Nó phải chạy
sau khi migration đã áp và (để có số liệu thật) khi data-plane loader đang chạy.

### 5.1 Khởi động worker

```bash
$ cd control-plane
$ . .venv/bin/activate
$ python -m app.worker
```

Worker in cấu hình hiệu lực lúc khởi động, đối soát sổ cái trong database, rồi
chờ job trên hàng đợi Redis `apply:jobs`. Chạy **một** tiến trình worker trên
mỗi node gateway; nó xử lý mỗi lần một job.

### 5.2 Các lane nền

Worker chạy nhiều lane độc lập, mỗi lane bật/tắt và định thời qua biến môi
trường (xem [bảng tra cứu](#8-tra-cứu-nhanh-biến-môi-trường)):

| Lane | Nhiệm vụ |
| --- | --- |
| **Apply** | Nhận job `apply:jobs`, serialize snapshot toàn node, exec `xdpgw-apply` để dựng slot không hoạt động, verify, rồi lật một lần `active_config`. |
| **Telemetry** | Gọi `dpstat snapshot --json` theo nhịp mỗi giây, lưu cửa sổ số liệu dịch vụ/node. |
| **Node-control** | Tái khẳng định trạng thái bypass mong muốn qua `dpstat set-bypass`; giữ/xả job khi bật/tắt maintenance. |
| **Billing** | Lấy mẫu băng thông sạch, cập nhật rollup kỳ cước, chốt kỳ đến hạn. |
| **Alert** | Đánh giá luật cảnh báo trên số liệu đã lưu, gửi qua kênh email/webhook. |
| **Feed** | Định thời và chạy đồng bộ feed threat-intel, áp global-deny. |

### 5.3 Kiểm thử thủ công đường apply đầu-cuối

Yêu cầu: API đang chạy, worker đang chạy, và data-plane loader đang chạy (mục
[3.3](#33-chạy-loader-thủ-công-trên-cặp-veth)).

1. Tạo hoặc cập nhật một dịch vụ qua API (mục
   [4.7](#47-kiểm-thử-thủ-công-qua-curl)). Thao tác này commit thay đổi và đẩy
   một job lên `apply:jobs`.

2. Theo dõi job chuyển trạng thái đến `active`:

   ```bash
   $ curl -s -b cookies.txt 'http://127.0.0.1:8000/jobs'
   $ curl -s -b cookies.txt http://127.0.0.1:8000/services/<service-id>/apply-status
   ```

   `active` nghĩa là cấu hình **đã thật sự** tới data-plane, không chỉ là worker
   ghi nhận job. Một lần exec `xdpgw-apply` thất bại/timeout để lại slot tốt
   cuối cùng còn sống và job ở trạng thái thất bại.

3. Xác nhận ở data-plane rằng slot đã lật và version tăng:

   ```bash
   # cd data-plane && ./build/dpstat active_config
   ```

### 5.4 Kiểm thử thủ công đường bypass

```bash
$ curl -s -b cookies.txt -X POST http://127.0.0.1:8000/node/bypass \
    -H 'Content-Type: application/json' \
    -d '{"enabled":true,"reason":"kiem thu thu cong"}'
```

Sau đó poll `GET /node/health` đến khi `bypass.desired` và `bypass.effective`
đều `true` (lane node-control thường khẳng định trong một chu kỳ). Đối chiếu ở
data-plane bằng `dpstat snapshot --json` (`node_control.bypass` và
`bypass.pkts`/`bypass.bytes`). Tắt lại bằng `enabled:false`.

### 5.5 Xác minh worker khi Redis mất kết nối (chạy riêng, thủ công)

Đường này **có chủ đích tách riêng** và không có case tự động. Chạy một mình,
không chạy song song với test tích hợp:

1. Dừng Redis test:

   ```bash
   $ docker compose -f control-plane/compose.test.yml stop redis
   ```

2. Commit một cập nhật dịch vụ bình thường trong lúc Redis đang tắt.

3. Chạy worker và xác minh đối soát sổ cái theo database đạt `active` mà không có
   job thất bại.

4. Bật lại Redis:

   ```bash
   $ docker compose -f control-plane/compose.test.yml start redis
   ```

5. Xác minh log `Redis connection resumed`, rồi kiểm tra một enqueue bình thường
   mới đạt `active` qua BRPOP trong vòng 5 giây.

---

## 6. Frontend SPA (React/Vite)

Thư mục làm việc: `control-plane/frontend/`.

### 6.1 Cài phụ thuộc

```bash
$ cd control-plane/frontend
$ npm ci
```

### 6.2 Chạy máy chủ phát triển

```bash
$ npm run dev
```

Vite proxy `/auth`, `/services` và `/node` sang control-plane API, nên API phải
đang chạy ở [mục 4.6](#46-khởi-động-api) và cấu hình có
`CONTROL_PLANE_COOKIE_SECURE=false` (vì dev server chạy HTTP). Bảng tenant chỉ
liệt kê dịch vụ trả về từ `GET /services` rồi poll dịch vụ đang chọn mỗi hai
giây; bảng quản trị poll số liệu và sức khỏe node mỗi hai giây.

### 6.3 Kiểm thử thủ công trên trình duyệt

1. Mở URL mà `npm run dev` in ra (thường `http://127.0.0.1:5173`).
2. Đăng nhập bằng tài khoản admin đã bootstrap. Yêu cầu trả `401` sẽ redirect về
   **Login**.
3. Kiểm các trạng thái hiển thị: loading, empty, error và **stale**. Chế độ XDP
   `generic` và `offline` được tô là **critical**.
4. Với tài khoản tenant, xác minh chỉ thấy dịch vụ của tenant đó, không thấy số
   liệu node hay dữ liệu của tenant khác.

### 6.4 Build bản production và cho FastAPI phục vụ

```bash
$ cd control-plane/frontend
$ npm run build
$ export CONTROL_PLANE_FRONTEND_STATIC_DIR="$(pwd)/dist"
```

Với biến này, khởi động lại API (mục 4.6). FastAPI phục vụ asset đã build và trả
`index.html` cho các route lịch sử trình duyệt như `/tenant` và `/admin`. Các
tiền tố API và asset thiếu vẫn trả `404` bình thường — fallback không bao giờ
trả HTML SPA cho chúng. Bỏ biến này khi có máy chủ web khác phục vụ frontend.

### 6.5 Gate frontend

```bash
$ cd control-plane/frontend
$ npm run lint && npm run typecheck && npm run test -- --run && npm run build
```

Gate này độc lập với `compose.test.yml`.

---

## 7. Kiểm thử tích hợp đầu-cuối

Trình tự khởi động một node hoàn chỉnh và tự xác minh mọi mắt xích khớp nhau:

1. **Postgres + Redis**: `docker compose -f control-plane/compose.test.yml up -d`.
2. **Migration + admin**: `alembic upgrade head` rồi `python -m app.cli
   bootstrap-admin`.
3. **Data-plane loader**: build ([3.1](#31-build)) rồi nạp trên veth
   ([3.3](#33-chạy-loader-thủ-công-trên-cặp-veth)) với `SERVICE_DEST`.
4. **API**: `uvicorn app.main:app --port 8000`.
5. **Worker**: `python -m app.worker` (trỏ `worker_telemetry_binary_path` và
   `worker_apply_binary_path` tới `../data-plane/build/dpstat` và
   `../data-plane/build/xdpgw-apply` — đây là mặc định khi chạy từ
   `control-plane/`).
6. **Frontend**: `npm run build` + đặt `CONTROL_PLANE_FRONTEND_STATIC_DIR`, hoặc
   `npm run dev`.

Xác minh chuỗi khớp nối:

- **Telemetry chảy lên UI**: loader chạy → lane telemetry của worker đọc `dpstat`
  → `GET /node/telemetry` có `has_data=true` và cửa sổ không stale → bảng quản
  trị hiển thị số liệu.
- **Apply chạm data-plane**: cập nhật dịch vụ qua API → job đạt `active` →
  `dpstat active_config` cho thấy version tăng.
- **Bypass khớp hai chiều**: `POST /node/bypass` → `node/health` báo effective
  `true` → `dpstat snapshot --json` báo `node_control.bypass=1`.

---

## 8. Tra cứu nhanh biến môi trường

Tiền tố `CONTROL_PLANE_`. Đầy đủ mặc định xem `app/core/config.py`.

### Kết nối và phiên

| Biến | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql+asyncpg://control_plane:control_plane@127.0.0.1:55432/control_plane_test` | Postgres (asyncpg). |
| `REDIS_URL` | `redis://127.0.0.1:56379/0` | Redis dùng chung. |
| `COOKIE_SECURE` | `true` | Đặt `false` để test qua HTTP. |
| `BOOTSTRAP_ADMIN_USERNAME` / `_PASSWORD` | rỗng | Thông tin admin cho `bootstrap-admin`. |
| `FRONTEND_STATIC_DIR` | rỗng | Thư mục `dist` để FastAPI phục vụ SPA. |

### Worker — apply và định thời

| Biến | Mặc định |
| --- | --- |
| `WORKER_APPLY_BINARY_PATH` | `../data-plane/build/xdpgw-apply` |
| `WORKER_APPLY_TIMEOUT_SECONDS` | `5.0` |
| `WORKER_POLL_TIMEOUT_SECONDS` | `2.0` |
| `WORKER_RECONCILE_INTERVAL_SECONDS` | `15.0` |
| `WORKER_SHUTDOWN_GRACE_SECONDS` | `10.0` |

### Worker — telemetry, node-control, billing

| Biến | Mặc định |
| --- | --- |
| `WORKER_TELEMETRY_ENABLED` | `true` |
| `WORKER_TELEMETRY_INTERVAL_SECONDS` | `2` (chỉ `1` hoặc `2`) |
| `WORKER_TELEMETRY_BINARY_PATH` | `../data-plane/build/dpstat` |
| `WORKER_TELEMETRY_IFINDEX` | rỗng (đặt để báo chế độ XDP live) |
| `WORKER_NODE_CONTROL_ENABLED` | `true` |
| `WORKER_NODE_CONTROL_INTERVAL_SECONDS` | `1.0` |
| `WORKER_BILLING_ENABLED` | `true` |
| `WORKER_BILLING_INTERVAL_SECONDS` | `300.0` |

### Data-plane loader (không có tiền tố `CONTROL_PLANE_`)

| Biến | Ý nghĩa |
| --- | --- |
| `SERVICE_DEST` | Đích dịch vụ demo (IPv4/CIDR). |
| `IN_IFACE` / `OUT_IFACE` | Giao diện IN/OUT thay cho tham số dòng lệnh. |
| `XDPGW_SEED_WL_CIDR`, `XDPGW_SEED_VIP_PPS`, `XDPGW_SEED_VIP_BPS` | Demo whitelist/VIP. |
| `XDPGW_SEED_GBL_CIDR`, `XDPGW_SEED_SBL_CIDR`, `XDPGW_SEED_BLOCKED_PORT` | Demo blacklist/cổng chặn. |

---

## 9. Xử lý sự cố thường gặp

| Triệu chứng | Nguyên nhân thường gặp | Cách xử lý |
| --- | --- | --- |
| Loader thoát ngay với lỗi map/pin | Thư mục `/sys/fs/bpf/xdp_gateway/` còn pin cũ. | `rm -rf /sys/fs/bpf/xdp_gateway` rồi nạp lại. |
| Loader báo không hỗ trợ native XDP | Giao diện IN không hỗ trợ DRV, hoặc OUT không resolve được. | Dùng veth như [3.3](#33-chạy-loader-thủ-công-trên-cặp-veth); loader **không** rơi về generic/SKB theo thiết kế. |
| `dpstat` báo gateway offline | Loader chưa chạy hoặc không sở hữu map ghim. | Khởi động loader trước, giữ nó chạy khi đọc. |
| Đăng nhập curl OK nhưng lệnh sau `401` | Cookie phiên có cờ Secure, không gửi lại qua HTTP. | Đặt `CONTROL_PLANE_COOKIE_SECURE=false` và đăng nhập lại. |
| Test tích hợp/`alembic` lỗi kết nối | Chưa dựng Postgres/Redis. | `docker compose -f control-plane/compose.test.yml up -d`. |
| `GET /node/telemetry` luôn stale, `has_data=false` | Worker hoặc loader chưa chạy. | Chạy cả loader và worker; chờ ít nhất một chu kỳ telemetry. |
| Job kẹt ở `queued`, không tới `active` | Worker chưa chạy, hoặc node đang ở maintenance. | Chạy `python -m app.worker`; kiểm `GET /node/health` xem maintenance. |
| `npm run dev` gọi API bị lỗi CORS/401 | API chưa chạy hoặc cookie Secure. | Chạy API và đặt `COOKIE_SECURE=false`. |

# Đánh giá hiệu năng chịu tải — Data-plane (XDP gateway)

_Ngày chạy: 2026-07-23 · Kernel 6.8.0-106 · Intel Xeon Platinum 8163 @ 2.7 GHz · 96 vCPU · BPF JIT bật (`bpf_jit_enable=1`)._

## 1. Mục tiêu & phạm vi

Đo **chi phí CPU trên mỗi gói (ns/packet)** của chương trình XDP `xdp_gateway` cho từng
nhánh quyết định (verdict path), từ đó suy ra **trần thông lượng gói mỗi lõi (Mpps/core)** —
tức khả năng chịu tải thực chất của data-plane khi đối mặt với các loại lưu lượng khác nhau
(lưu lượng sạch, tấn công phản xạ/khuếch đại, IP giả mạo, blacklist, v.v.).

Không có card mạng tốc độ cao + máy phát gói trong môi trường này, nên phép đo dùng
**`BPF_PROG_TEST_RUN`** — chạy chính chương trình đã JIT trong kernel với số lần lặp lớn và
đọc lại thời gian thực thi trung bình mỗi gói do kernel báo (`opts.duration`). Đây là phương
pháp micro-benchmark chuẩn cho XDP.

## 2. Phương pháp

- Công cụ: [`data-plane/tests/bench_dp.c`](../data-plane/tests/bench_dp.c), chạy bằng `make -C data-plane bench`.
  Tái sử dụng toàn bộ harness test (`test_parse.c`): nạp skeleton, seed map, dựng gói.
- Mỗi kịch bản: seed map tương ứng → dựng khung gói → `bpf_prog_test_run_opts` với
  `repeat = 2.000.000`, lặp **11 vòng**, mỗi vòng reset map mới. Báo cáo **trung vị** (median).
- Ghim tiến trình vào **CPU 0**; có 1 vòng warm-up 50k để làm nóng icache/map.
- Verdict lấy từ `pkt_meta.verdict` (quyết định thật của data-plane) chứ không lấy mã trả về
  XDP thô — vì đường chấp nhận kết thúc bằng `bpf_redirect_map` vào devmap rỗng nên mã trả về
  thô là XDP_DROP dự phòng, không phản ánh quyết định.

### Phép đo này **có** và **không** bao gồm gì

| Bao gồm | KHÔNG bao gồm |
|---|---|
| Toàn bộ logic eBPF: parse, lọc deny (amp/bogon/bitmap/blacklist), whitelist, tra cứu service (LPM), khớp rule, VIP/ingress-cap, fairness (token bucket + spin-lock), tra cứu next-hop | Chi phí driver NIC + DMA nhận/gửi gói |
| Cập nhật bộ đếm per-CPU, ghi thống kê service | Thao tác `xdp_do_redirect`/TX thật (nhánh chấp nhận) |
| Chạy mã đã JIT (native) | — |

## 3. Kết quả (trung vị, khung UDP tối thiểu 42 byte)

| Kịch bản | Quyết định | ns/gói | Mpps/lõi | Gbps/lõi\* |
|---|---|---:|---:|---:|
| `clean_redirect` — lưu lượng hợp lệ, chạy **toàn bộ** pipeline → redirect | ADMIT | **~620** | **~1,61** | 0,85 |
| `blacklist_drop` — nguồn nằm trong global blacklist | DROP | ~442 | ~2,26 | 1,19 |
| `not_allowed` — không khớp allow-rule (mặc định từ chối) | DROP | ~390 | ~2,56 | 1,35 |
| `amp_port_drop` — khuếch đại UDP, cổng nguồn bị chặn (bitmap) | DROP | ~345 | ~2,90 | 1,53 |
| `bogon_drop` — nguồn RFC1918 giả mạo | DROP | ~335 | ~2,98 | 1,57 |
| `service_miss` — lưu lượng tới đích không được bảo vệ | DROP | ~83 | ~12,0 | 6,3 |
| `non_ipv4_parse` — khung IPv6, từ chối sớm nhất (cận dưới) | DROP | ~51 | ~19,6 | 12,2 |

\* Gbps ở **khung tối thiểu** (đã cộng 24 byte overhead trên dây: preamble+FCS+IFG). DDoS
thường bị giới hạn bởi **pps** (gói nhỏ), nên cột **Mpps/lõi** mới là ràng buộc quyết định.
Ở khung 1500 byte, cùng mức pps cho băng thông cao hơn ~14× (vd. nhánh sạch ≈ **19 Gbps/lõi**).

Độ ổn định rất cao: min ≈ median ≈ mean, lệch giữa các lần chạy < 1%.

## 4. Diễn giải

**Trần lưu lượng sạch (~1,6 Mpps/lõi).** Nhánh chấp nhận đắt nhất (~620 ns ≈ 1.670 chu kỳ)
vì phải đi qua **toàn bộ** chuỗi: parse → 4 tầng lọc deny (kèm bloom + LPM trie) → whitelist
→ service LPM → khớp rule → fairness (spin-lock + `bpf_ktime_get_ns`) → next-hop. Đây là mức
hợp lý cho một pipeline scrubbing đầy đủ.

**Nhánh drop nặng tra cứu (~335–442 ns).** Điểm đáng chú ý: mỗi gói tấn công vẫn chạy phần
lớn chuỗi lọc trước khi bị loại. Thứ tự short-circuit đang hợp lý — bogon (~335) và bitmap
khuếch đại (~345) rẻ hơn blacklist (~442) vì cắt trước khi tra bloom+LPM của global blacklist.
Các loại từ chối sớm (`service_miss` ~83 ns, `non_ipv4` ~51 ns) rẻ hơn hẳn.

**Quy mô tổng hợp (96 lõi, RSS trải luồng).** Các nhánh drop chủ yếu đọc map + bộ đếm per-CPU
nên **mở rộng gần tuyến tính** theo số lõi: bậc **~200–280 Mpps** năng lực loại-bỏ tổng cho
các nhánh lọc, và cao hơn nhiều cho từ chối sớm. Đây là năng lực giảm thiểu (mitigation) rất tốt.

## 5. Cảnh báo về tính đại diện (đọc kỹ trước khi trích số)

Các con số trên là **cận trên lạc quan** cho pps thực địa, vì:

1. **Thiên lệch cache nóng / một luồng.** `TEST_RUN` phát lặp cùng một gói ⇒ mọi entry map
   nằm sẵn trong L1/L2, cùng một cache-line spin-lock. Flood thật có **nhiều IP nguồn khác
   nhau** ⇒ LPM trie đi sâu hơn, bloom có tải thật, cache miss nhiều ⇒ ns/gói thực tế **cao
   hơn**. Hãy coi Mpps nhánh-drop là trần lý tưởng, chưa phải năng lực chịu tải hiện trường.
2. **Overhead của bản test.** Object đo được biên dịch kèm `PKT_TEST_HOOKS` (thêm một
   `write_test_meta` map-write ở nhánh chấp nhận). Bản production nhẹ hơn ở nhánh chấp nhận ⇒
   ~620 ns là **ước lượng thận trọng** (production nhanh hơn đôi chút).
3. **Chưa tính DMA/TX driver.** Nhánh chấp nhận trong thực tế còn cộng chi phí
   `xdp_do_redirect` + TX của driver; nhánh drop (XDP_DROP) thì đã là chi phí đầy đủ.

## 6. Phát hiện đáng lưu ý (cho thiết kế)

- **Nút cổ chai mở rộng: spin-lock của committed bucket.** `fair_committed_admit()` lấy
  `bpf_spin_lock` trên **một bucket global theo từng service** cộng `bpf_ktime_get_ns` mỗi gói
  được nhận. Khi RSS trải lưu lượng của **một VIP nóng** ra nhiều lõi, tất cả lõi tranh chấp
  đúng một cache-line có lock ⇒ nhánh **chấp nhận sẽ KHÔNG mở rộng tuyến tính cho một service
  đơn lẻ**. Đây là giới hạn thông lượng tổng chính đối với lưu lượng hợp lệ dồn về một đích.
- **Kịch bản xấu nhất về CPU: flood "trông sạch" làm cạn ngân sách.** Lưu lượng vượt qua mọi
  bộ lọc rồi vắt kiệt committed+ceiling và bị loại ở tầng fairness có chi phí **≈ nhánh chấp
  nhận (~600 ns)** vì đi hết chuỗi. Khuyến nghị đặt **ingress rate-cap** (rẻ, sớm hơn trong
  chuỗi) làm tuyến phòng thủ đầu để chặn dạng volumetric này trước khi tốn CPU cho fairness.
- **Cơ hội tối ưu nhánh drop.** Vì mỗi gói tấn công chạy nhiều tra cứu map trước khi bị loại,
  có thể xem xét nâng các bộ phân biệt rẻ + tỉ lệ trúng cao lên sớm hơn nữa trong chuỗi.

## 7. Tái lập

```bash
make -C data-plane bench                 # mặc định repeat=500k, rounds=7
data-plane/build/bench_dp 2000000 11     # repeat, rounds tùy chọn
```

Yêu cầu: chạy bằng **root**, kernel bật BPF JIT. Không đụng tới NIC/veth thật (chạy hoàn toàn
qua `BPF_PROG_TEST_RUN`), an toàn để chạy trên máy dev.

---

## 8. Phương án tối ưu hiệu năng

### 8.1 Chẩn đoán: 620 ns của nhánh sạch đi đâu?

Truy vết hot-path cho thấy mỗi gói được chấp nhận phải trả:

| Chi phí | Số lần/gói | Vị trí |
|---|---:|---|
| `bpf_ktime_get_ns()` | **2** (tới **4** nếu bật VIP/svc_rl) | [fairness.h:223](../data-plane/src/fairness.h#L223) (ingress-cap), [fairness.h:381](../data-plane/src/fairness.h#L381) (committed) |
| `bpf_spin_lock` global | **1** | [fairness.h:400](../data-plane/src/fairness.h#L400) |
| `fair_config_lookup` (double-indirection ARRAY_OF_MAPS) | **2** (trùng lặp) | [fairness.h:256](../data-plane/src/fairness.h#L256) và [fairness.h:450](../data-plane/src/fairness.h#L450) |
| Thao tác map tổng cộng | **~15–20** | service, fair_config×2, cap-state, whitelist bloom, bitmap×2, gbl_meta, blacklist bloom+LPM, rule_block, committed-state, nexthop, svc_stat |
| Tầng rate-limit chồng nhau | **4** | ingress_cap [fairness.h:242](../data-plane/src/fairness.h#L242), VIP ceiling [whitelist.h:391](../data-plane/src/whitelist.h#L391), per-rule svc_rl [rules.h:252](../data-plane/src/rules.h#L252), fairness committed/burst/node [fairness.h:450](../data-plane/src/fairness.h#L450) |

**Nguyên nhân nhánh drop đắt:** bộ lọc bogon/amp nằm trong `deny_filter_stage`
([blacklist.h:394](../data-plane/src/blacklist.h#L394)) — được gọi **sau** service-lookup →
ingress-cap → whitelist. Vì vậy gói tấn công spoof/khuếch đại vẫn phải trả toàn bộ tra cứu
map phía trước rồi mới bị loại: `bogon_drop` 335 ns so với từ chối parse 51 ns — **~284 ns bị
đốt vô ích** trước khi lệnh drop bắn ra.

### 8.2 Nhóm 1 — Tối ưu thuần, **không mất tính năng** (rủi ro thấp)

- **A1. ⭐ Đẩy bộ lọc không-trạng-thái (bogon + amp hardcoded) lên ngay sau `parse_l4`.**
  Hai check này **không đọc map**. Chạy trước service-lookup/ingress-cap/whitelist ⇒ gói spoof
  và khuếch đại bị loại trong **~80 ns thay vì ~335 ns (≈4×)**. Đây là thắng lợi lớn nhất cho
  lưu lượng volumetric — đúng thứ data-plane cần làm rẻ nhất.
  _Đánh đổi:_ hiện whitelist được ưu tiên hơn blacklist; đẩy bogon/amp lên trước whitelist
  nghĩa là nguồn đã whitelist mà lại là RFC1918 hoặc dùng cổng nguồn 53 sẽ bị loại. Trên
  gateway public gần như không xảy ra, **nhưng đây là thay đổi chính sách cần xác nhận trước.**
- **A2. Gộp `fair_config_lookup` còn 1 lần**, truyền con trỏ config xuống ingress-cap và
  fair_admit ⇒ bớt 1 double-indirection mỗi gói.
- **A3. Gọi `bpf_ktime_get_ns()` một lần** ở đầu khối rate-limit rồi thread `now` xuống mọi
  bucket ⇒ bỏ 1–3 lời gọi mỗi gói.
- **A4. Gate subsystem bằng bitmask "feature active" per-slot** (thêm 1 field vào
  `active_config`): deployment không cấu hình whitelist / service-blacklist / ingress-cap /
  svc_rl thì **bỏ qua hẳn** các tra cứu đó. Lợi cho cả nhánh accept lẫn drop.

> Ước tính Nhóm 1: giảm **~20–35%** ns/gói nhánh accept và **~3–4×** nhánh drop bogon/amp,
> mà **không bỏ tính năng nào**.

### 8.3 Nhóm 2 — Cắt/hợp nhất tính năng

- **B1. ⭐ Hợp nhất 4 tầng rate-limit xuống 1–2 tầng.** Đây là chỗ phình lớn nhất: ingress-cap,
  VIP-ceiling, per-rule svc_rl và fairness committed/burst/node **đều là token-bucket**, mỗi
  tầng tốn ktime + lookup (+spin-lock). Hướng đề xuất: **giữ một tầng cap sớm và rẻ** làm lá
  chắn volumetric (xem mục 6), **cắt các tầng chồng lấn phía sau** (per-rule svc_rl, VIP
  ceiling) nếu deployment không thực sự dùng ⇒ tiết kiệm ~1–2 ktime + ~3–4 lookup mỗi gói.
- **B2. Bỏ blacklist theo-service** (✅ **Đã hoàn thành**: gỡ bỏ toàn bộ nhánh service-blacklist, sbl bloom/LPM maps và wire payload ở feature `service-blacklist-removal`). Tác động ns/packet thực tế = **0** do nhánh trước đây đã được gate bởi `bl_flags = 0` (D-SBR-3); lợi ích chính là dọn dẹp RAM, map capacity và đơn giản hóa API/UI.
- **B3. Lấy mẫu `svc_stat` thay vì cập nhật mỗi gói** ([svc_stat.h](../data-plane/src/svc_stat.h)):
  hiện mỗi gói được nhận ghi 1 hash update; chuyển sang đếm per-CPU + sample 1/N.
- **B4. Bỏ hẳn whitelist/VIP** nếu tính năng không dùng (gate như A4, hoặc biên dịch tuỳ chọn).

### 8.4 Nhóm 3 — Cấu trúc / mở rộng đa lõi (rủi ro vừa)

- **C1. ⭐ Chuyển committed bucket sang per-CPU (bỏ spin-lock global).** Các bucket khác
  (cap/burst/VIP/svc_rl) **đã** là PERCPU; chỉ committed còn dùng spin-lock để chính xác tuyệt
  đối. Đổi sang per-CPU (mỗi lõi nhận `committed_bps/ncpus`) sẽ **xoá nút cổ chai đa lõi** nêu
  ở mục 6 — đánh đổi là sai số token nhỏ ở tốc độ thấp. Đây là điều kiện để nhánh accept mở
  rộng tuyến tính theo 96 lõi.
- **C2. Giảm double-indirection ARRAY_OF_MAPS.** Cơ chế blue-green 2 slot khiến mỗi subsystem
  tốn 2 lần lookup. Tác động lớn nhưng đụng vào cơ chế apply nguyên tử ⇒ **rủi ro cao, để sau cùng.**

### 8.5 Nhóm 4 — Đo lại cho đúng mốc

- **D1. Bench trên object production** (không `PKT_TEST_HOOKS`). Bản test còn thêm ~3 map-lookup
  mỗi gói ngay đầu chương trình ([xdp_gateway.bpf.c:210-217](../data-plane/src/xdp_gateway.bpf.c#L210)),
  nên số production **đã thấp hơn** số trong mục 3. Cần mốc này trước khi đo đối chứng tối ưu.

### 8.6 Thứ tự triển khai đề xuất

| Ưu tiên | Hạng mục | Tác động | Rủi ro | Mất tính năng |
|---|---|---|---|---|
| 1 | A1 (hoist bogon/amp) | ~4× nhánh drop | Thấp\* | Không |
| 2 | A2 + A3 (dedupe lookup/ktime) | ~10–15% accept | Rất thấp | Không |
| 3 | D1 (mốc production) | — (đo lường) | Không | Không |
| 4 | C1 (committed per-CPU) | Mở rộng đa lõi | Vừa | Không (giảm độ chính xác token) |
| 5 | A4 + B1 (gate & hợp nhất; B2 đã xong, B2 ns=0) | ~15–25% (gánh bởi A4 + B1) | Vừa | Có, tuỳ deployment |
| 6 | C2 (bỏ double-indirection) | Lớn | Cao | Không |

\* Rủi ro kỹ thuật thấp, nhưng cần chốt thay đổi **chính sách** whitelist-vs-bogon/amp (xem A1).

Mọi thay đổi nên đo đối chứng bằng `make -C data-plane bench` (số liệu rất ổn định, lệch <1%)
và chạy lại gate `make -C data-plane test` (hiện 137 passed).

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

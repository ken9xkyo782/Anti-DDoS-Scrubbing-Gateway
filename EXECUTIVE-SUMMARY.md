# Tóm tắt điều hành — Anti-DDoS Scrubbing Gateway (MVP v1)

| Trường | Giá trị | Trường | Giá trị |
|---|---|---|---|
| **Trạng thái** | PRD Final v1.0 — sẵn sàng bàn giao Pilot | **Ngày** | 2026-07-07 |
| **Mô hình** | Nội bộ, có thu phí (chargeback theo Gbps sạch) | **Chi tiết** | Xem `PRD.md` |

## Vấn đề & giải pháp

Các dịch vụ nội bộ đang thiếu một lớp phòng thủ chống tấn công từ chối dịch vụ (DDoS) khối lượng lớn ở tầng mạng (L3/L4). **Anti-DDoS Scrubbing Gateway** là "lá chắn" đặt trước hạ tầng: nhận toàn bộ lưu lượng, lọc bỏ traffic tấn công theo thời gian thực bằng công nghệ XDP/eBPF hiệu năng cao, và chỉ chuyển tiếp traffic sạch tới hệ thống bên trong.

## Giá trị kinh doanh

- Giảm rủi ro gián đoạn dịch vụ do tấn công volumetric (UDP/ICMP flood, reflection/amplification…).
- Mỗi đơn vị tự quản lý bảo vệ dịch vụ của mình (self-service), có giám sát thời gian thực.
- **Thu phí nội bộ theo băng thông sạch (Gbps)** — minh bạch, đo lường được, phân bổ chi phí công bằng.

## Chỉ số cam kết chính (Pilot)

| Năng lực | Cam kết |
|---|---|
| Thông lượng mỗi node | ≥ 40 Gbps / 20 Mpps |
| Độ trễ tăng thêm | p99 ≤ 1 ms với traffic sạch |
| Băng thông sạch cam kết/đơn vị | **Được đảm bảo cứng**, không bị "hàng xóm ồn ào" làm suy giảm |
| Quy mô | 100 đơn vị (tenant), 1.000 dịch vụ |

## Mức độ sẵn sàng

Đã hoàn tất rà soát nghiệp vụ đầy đủ (logic, vận hành, thương mại hóa). **10 hạng mục trọng yếu đã được chốt và thiết kế xong** — toàn bộ phần logic nghiệp vụ, kỹ thuật lõi và vận hành (cảnh báo, bypass khẩn cấp). Đội kỹ thuật có thể khởi động Pilot ngay.

## 3 điều lãnh đạo cần biết & quyết định

1. **Chưa có HA (dự phòng) trong v1 → không cam kết được chỉ tiêu "uptime" ở Pilot.** Thiết bị chạy đơn lẻ; nếu hỏng có thể gián đoạn. **Biện pháp bù:** quy trình bypass khẩn cấp + cửa sổ bảo trì, ghi rõ trong thỏa thuận vận hành nội bộ (OLA). → *Cần chấp thuận vận hành Pilot với giới hạn này; và cấp ngân sách HA cho giai đoạn GA.*
2. **Chưa hỗ trợ IPv6 (bị chặn trong v1).** Dịch vụ có người dùng IPv6 cần được xử lý khi onboard để tránh mất truy cập. → *Product chuẩn bị hướng dẫn onboarding.*
3. **Nguồn dữ liệu threat-intelligence cần rà soát bản quyền** trước khi dùng cho sản phẩm có thu phí. → *Pháp chế rà soát license.*

## Lộ trình

- **Pilot (giai đoạn tới):** vận hành một node, có khách nội bộ trả phí; cam kết cao về độ trễ/độ chính xác/công bằng băng thông; **loại trừ có chủ đích chỉ tiêu uptime**.
- **GA:** bổ sung HA/failover (điều kiện bắt buộc), IPv6, tự động ứng phó tấn công, mở rộng đa node.

## Đề xuất phê duyệt

1. Thông qua triển khai **Pilot** theo PRD Final v1.0, chấp nhận giới hạn Availability có biện pháp bù (OLA).
2. Đưa **HA** vào kế hoạch/ngân sách như điều kiện cổng **GA**.
3. Giao Product (onboarding IPv6, định vị năng lực) và Pháp chế (license feed) xử lý 3 mục Pilot còn lại — song song, không chặn kỹ thuật.

---
*Tài liệu chi tiết: `PRD.md`. Nhật ký quyết định & rà soát: Mục 15 của PRD.*

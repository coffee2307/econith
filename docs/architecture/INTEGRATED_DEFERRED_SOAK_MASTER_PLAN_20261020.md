# ECONITH — Quyết định hoãn kiểm thử dài hạn và nghiệm thu liên phase

Ngày lập: 2026-10-20 · Trạng thái: kế hoạch vận hành, không phải bằng chứng PASS.

## Quyết định được duyệt

Hoàn thiện các hệ thống và bài kiểm thử **ngắn, có tái lập được** ở Phase 1 và các phase tiếp theo trước. Không lặp soak test 24–72 giờ mỗi khi thay đổi nhỏ. Sau khi kiến trúc của các phase đã ổn định, tổ chức **một chiến dịch nghiệm thu tích hợp liên phase** trên commit/release candidate được đóng băng, hạ tầng giám sát và bộ dữ liệu rõ ràng. Nếu sau đó sửa code ảnh hưởng đường dữ liệu, độ trễ hoặc trạng thái, bằng chứng của campaign cũ không tự động áp dụng cho commit mới.

**Quyết định hoãn không có nghĩa là miễn kiểm thử dài hạn.** Các điều kiện thuộc P1-04/05/07 chưa được nghiệm thu vẫn giữ nguyên trọng số và trạng thái OPEN; 81/100 là điểm minh chứng tạm thời, không được tính thành 100/100. Không được phát hành chứng nhận 24/7, auto trading hay production readiness chỉ vì đã hoàn thiện nhiều phase.

## Những việc tiếp tục thực hiện ngay — không cần soak

1. **P1-04:** làm sạch chứng cứ owner fencing, tiếp nhận khi crash, hồi phục cùng idempotent receipt; kiểm thử hard-kill/quarantine ngắn. Bằng chứng mất điện thật/host khác để dành cho campaign cuối.
2. **P1-05:** phát triển source epoch mới sau BOOK_NOT_SYNCED, phải có snapshot mới, boundary UNKNOWN bất biến, kiểm toán từng epoch; kiểm thử fault injection, backlog và CPU/RAM ngắn. Reconnect thật và độ bền nhiều ngày được đưa vào campaign sau.
3. **P1-06:** bảo đảm fail-closed của policy verified-only, AST startup boundary, ASGI HTTP route allowlist và WebSocket deny-list, không tự động kích hoạt CCXT/order bridge/engine. Chuyển **default** chỉ được quyết định sau review độc lập, không âm thầm sửa trên nhánh nghiên cứu.
4. **P1-07:** tăng độ phủ nhiều tài sản, chuỗi nến đã đóng, listing/delisting, timestamp-vintage, dữ liệu thiếu/cũ/quá hạn, manifest dữ liệu và tính tái lập. Kiểm thử dài hạn được gom vào campaign cuối, không bỏ qua.
5. **Các phase tiếp theo:** hoàn thiện chức năng + test ngắn tương ứng, ghi commit và kết quả vào release-candidate manifest. Không cấp lại điểm Phase 1 dựa trên số dòng code hoặc CI xanh ở phase khác.

## Định nghĩa chiến dịch kiểm thử chung sau các phase

- **Tiền điều kiện:** code/release candidate cố định, các PR quan trọng đã được kiểm toán và hợp nhất có kiểm soát; không thực hiện ngay trên chuỗi PR Draft; chạy ở chế độ SIMULATION/read-only, tuyệt đối không gửi lệnh giao dịch.
- **Dữ liệu:** thu bằng chứng raw có xác thực cục bộ, ghi hash, session/epoch ID, source REST/WSS metadata, snapshot/diff continuity, timestamp nguồn và local first receipt. Lưu trong vùng riêng có phân quyền, mã hóa và hạn lưu giữ rõ ràng; không upload raw SQLite hoặc khóa HMAC lên artifact công khai.
- **Soak:** ít nhất một phiên nguồn + HMAC/observer/checkpoint liên tục 24h có telemetry CPU/RAM (parent và children), network I/O, disk, FIFO, backlog, tail latency, reconnect và khoảng UNKNOWN. Khảo sát 72h khi cần theo exit matrix, không thay bằng 12 × 10 phút.
- **Fault/Recovery:** mất nguồn/kill/restart do operator kiểm soát; owner fencing không bị chiếm sai; orphan marker giữ lại khi ACK chưa rõ; xác nhận receiver-owned receipt không trùng, checkpoint khôi phục đúng. Những khoảng không thể chứng minh phải ghi UNKNOWN và không sử dụng book cũ.
- **Đa tài sản:** minh chứng khoảng thời gian, nguồn, danh sách symbol/market status, gaps, duplicates và đối chiếu độc lập; không suy ra venue completeness từ REST finite sample.
- **Nghiệm thu:** đối chiếu theo từng gate bất biến P1-04/05/06/07, tách FAIL/INCOMPLETE/DEFERRED, các artifact có sha256, thời gian, commit, người kiểm duyệt và hạn lưu giữ. Chỉ đóng Phase 1 sau khi *mọi* gate yêu cầu có bằng chứng tương ứng.

## Quy tắc dành cho các vòng phát triển trước campaign

Chỉ chạy **unit/integration, mô phỏng lỗi ngắn, audit tĩnh, CI nhanh**. Phiên public hữu hạn 90–150 giây chỉ thực hiện khi nó trực tiếp giải quyết tính đúng đắn của đường dữ liệu và có phê duyệt opt-in rõ ràng, không chạy lặp với mục tiêu tích lũy giờ. Không tự động tạo automation, thuê máy dài hạn, kích hoạt lịch chạy soak hoặc giao dịch thật. Dự toán thời gian/chi phí campaign cuối khi toàn bộ phase đạt mã đóng băng.

**Nguồn trạng thái ban đầu:** P1-04 14/15, P1-05 13/15, P1-06 4/10, P1-07 0/10, các mục khác 50/50; tổng 81/100 và OPEN. Kế hoạch này không sửa bảng điểm nghiệm thu.

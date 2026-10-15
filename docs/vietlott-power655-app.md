# Vietlott Power 6/55 trong app Streamlit

Mục sản phẩm `Vietlott Power 6/55` hỗ trợ thu thập kết quả, thống kê số 01-55, dò vé và tạo bộ số tham khảo.

## Tải toàn bộ lịch sử

```powershell
uv sync --frozen --no-dev --no-install-project
uv run src/fetch_vietlott_power655.py --all
uv run streamlit run src/xsmn_app.py
```

Mở [http://localhost:8501](http://localhost:8501), chọn **Sản phẩm → Vietlott Power 6/55** ở thanh bên. Trong giao diện cũng có nút **Tải toàn bộ lịch sử Power 6/55**.

Mỗi khi app khởi động, Power 6/55 tự đọc kỳ mới nhất và chỉ tải các ID còn thiếu. Khi thiếu nhiều kỳ, bộ đồng bộ so chi phí số request chi tiết với tải toàn bộ lịch sử để chọn cách ít request hơn. Nhật ký tham khảo chỉ được tính khi mở **Tham khảo & kiểm chứng**; không chạy khi đang dò vé hoặc xem thống kê.

Database mặc định là `data/vietlott_power655.sqlite3`. Có thể đổi đường dẫn:

```powershell
$env:VIETLOTT_POWER655_DATABASE_PATH='data/my_power655.sqlite3'
uv run streamlit run src/xsmn_app.py
```

CLI `--all` dùng API phân trang Ajax phía sau trang lịch sử chính thức. Chương trình:

1. Đọc tổng số kỳ Vietlott đang công bố.
2. Tải lần lượt toàn bộ trang lịch sử, mỗi trang 8 kỳ.
3. Khử trùng theo mã kỳ và từ chối lưu nếu số kỳ tải được không khớp số công bố.
4. Kiểm tra các ID kỳ bị thiếu trong chuỗi.
5. Giữ nguyên dữ liệu Jackpot/giải thưởng chi tiết đã có khi cập nhật hàng loạt.

Để chỉ cập nhật các kỳ mới nhất kèm bảng giải chi tiết:

```powershell
uv run src/fetch_vietlott_power655.py --limit 8
```

## Xác suất và dự đoán

- Một số cụ thể có xác suất xuất hiện trong 6 số chính mỗi kỳ là `6/55 = 10,9091%`.
- Một bộ 6 số cụ thể có xác suất Jackpot 1 là `1/C(55,6) = 1/28.989.675`.
- App hiển thị xác suất lý thuyết từng hạng giải và tần suất thực nghiệm trên toàn bộ dữ liệu.
- Tần suất lịch sử được làm trơn về `6/55`: `(c_i + 100 × 6/55)/(T + 100)`. `c_i` chỉ đếm số chính, `T` là số kỳ; 100 là mức prior cố định để thử nghiệm, không được tuyên bố là tối ưu.
- Bộ số tham khảo v2 được sinh đều, có seed theo ngày và phiên bản. Tăng số bộ không làm thay đổi bộ đầu. Mọi bộ đều có cùng xác suất Jackpot 1; bỏ điểm tương đối 100 gây hiểu nhầm.
- Mọi dữ liệu ngày mục tiêu hoặc tương lai bị loại trước khi tính. Số đặc biệt luôn tách khỏi 6 số chính.

Xếp hạng tần suất là mô tả dữ liệu, không phải bằng chứng dự báo chính xác hơn. Các kỳ quay độc lập; dữ liệu quá khứ không làm một bộ số có xác suất toán học cao hơn bộ khác.

Nhật ký đối chiếu tô nền xanh cho số chính trùng và nền vàng khi bộ dự đoán chứa số đặc biệt. Hiệu suất được so với baseline tổ hợp của một vé 6 số: số chính khớp kỳ vọng là `36/55 ≈ 0,6545` số/kỳ. Đây là baseline toán học, không phải kết quả của một lần mô phỏng random.

Backtest walk-forward và các biến thể top-score nằm trong `analysis/prediction_backtest.py`. V2 là sửa phương pháp và kiểm soát rò rỉ dữ liệu, không phải mô hình có lợi thế đã được chứng minh. Tăng số vé không trùng nhau chỉ tăng số kết quả được bao phủ và chi phí. Kết quả thực nghiệm và so sánh phiên bản ở [báo cáo kiểm chứng](validation-2026-10-15.md).

## Nguồn

- [Lịch sử kết quả Power 6/55 của Vietlott](https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655)
- [Cơ cấu giải Power 6/55](https://www.vietlott.vn/vi/choi/power-6-55/co-cau-giai-thuong)
- [Quy định lĩnh thưởng](https://vietlott.vn/vi/trung-thuong/linh-thuong/quy-dinh-linh-thuong)

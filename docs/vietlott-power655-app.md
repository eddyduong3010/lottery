# Vietlott Power 6/55 trong app Streamlit

Tab `Vietlott Power 6/55` hỗ trợ thu thập kết quả, thống kê số 01-55, dò vé và tạo bộ số tham khảo.

## Tải toàn bộ lịch sử

```powershell
uv sync --frozen --no-dev --no-install-project
uv run src/fetch_vietlott_power655.py --all
uv run streamlit run src/xsmn_app.py
```

Mở [http://localhost:8501](http://localhost:8501), chọn tab `Vietlott Power 6/55`. Trong giao diện cũng có nút **Tải toàn bộ lịch sử Power 6/55**.

Mỗi khi app khởi động, Power 6/55 tự đọc kỳ mới nhất và chỉ tải các ID còn thiếu. Nếu database mới hoặc thiếu trên 32 kỳ, app tự dùng phân trang để tải hàng loạt. Trạng thái đồng bộ và dự đoán cho kỳ tiếp theo xuất hiện ngay ở trang Tổng quan.

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
- Điểm dự đoán kết hợp 50% tần suất gần đây, 30% tần suất dài hạn và 20% số kỳ chưa xuất hiện.

Điểm dự đoán là một cách xếp hạng thống kê có thể tái lập, không phải xác suất đã hiệu chỉnh. Các kỳ quay độc lập; dữ liệu quá khứ không làm một bộ số có xác suất toán học cao hơn bộ khác.

## Nguồn

- [Lịch sử kết quả Power 6/55 của Vietlott](https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655)
- [Cơ cấu giải Power 6/55](https://www.vietlott.vn/vi/choi/power-6-55/co-cau-giai-thuong)
- [Quy định lĩnh thưởng](https://vietlott.vn/vi/trung-thuong/linh-thuong/quy-dinh-linh-thuong)

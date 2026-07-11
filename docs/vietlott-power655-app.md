# Vietlott Power 6/55 trong app Streamlit

Tab `Vietlott Power 6/55` mở rộng app hiện có cho sản phẩm Power 6/55 của Vietlott. Đây là sản phẩm quay 6 số chính từ 01-55 và 1 số đặc biệt; lịch quay công khai là Thứ 3, Thứ 5, Thứ 7, 18:00-18:30.

## Chạy nhanh

```powershell
uv sync --frozen --no-dev --no-install-project
uv run src/fetch_vietlott_power655.py --limit 8
uv run streamlit run src/xsmn_app.py
```

Mở [http://localhost:8501](http://localhost:8501), chọn tab `Vietlott Power 6/55`.

Database mặc định: `data/vietlott_power655.sqlite3`. Có thể đổi bằng biến môi trường:

```powershell
$env:VIETLOTT_POWER655_DATABASE_PATH='data/my_power655.sqlite3'
uv run streamlit run src/xsmn_app.py
```

## Chức năng

1. **Cập nhật dữ liệu:** tải các kỳ gần nhất từ trang lịch sử công khai của Vietlott, sau đó mở từng trang chi tiết để lấy Jackpot 1, Jackpot 2, số lượng giải và giá trị giải.
2. **Thống kê:** đếm tần suất các số 01-55, tỷ lệ quan sát, tỷ lệ kỳ có số xuất hiện, và số kỳ chưa về. Mặc định chỉ tính 6 số chính; có tùy chọn tính thêm số đặc biệt.
3. **Kết quả theo kỳ:** xem bộ 6 số chính, số đặc biệt, bảng hạng giải và link trang nguồn Vietlott.
4. **Dò vé:** nhập 6 số để rà Jackpot 1, Jackpot 2, Giải Nhất, Giải Nhì, Giải Ba.
5. **Dự đoán tham khảo:** xếp hạng số và sinh bộ 6 số bằng điểm thống kê minh bạch. Điểm này không phải xác suất trúng thưởng.

## Quy tắc dò vé

| Hạng giải | Điều kiện |
|---|---|
| Jackpot 1 | Trùng 6 số chính |
| Jackpot 2 | Trùng 5 số chính và số đặc biệt |
| Giải Nhất | Trùng 5 số chính |
| Giải Nhì | Trùng 4 số chính |
| Giải Ba | Trùng 3 số chính |

Giá trị Jackpot 1/2 thay đổi theo kỳ nên app ưu tiên bảng chi tiết của kỳ quay. Các giải cố định hiện dùng mức công khai trên trang kết quả Vietlott: Giải Nhất 40.000.000đ, Giải Nhì 500.000đ, Giải Ba 50.000đ.

## Nguồn và giới hạn

- Kết quả lịch sử và chi tiết kỳ quay: [Vietlott Power 6/55](https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655).
- Cơ cấu giải và cách chơi: [Vietlott Power 6/55](https://www.vietlott.vn/vi/choi/power-6-55/co-cau-giai-thuong).
- Quy định lĩnh thưởng: [Vietlott](https://vietlott.vn/vi/trung-thuong/linh-thuong/quy-dinh-linh-thuong).
- Trang lịch sử công khai hiện hiển thị một trang gần nhất; CLI `--limit` giới hạn số kỳ lấy từ trang đó. Nếu cần toàn bộ lịch sử nhiều năm, cần bổ sung truy cập phân trang Ajax của Vietlott.
- App phục vụ học tập/tham khảo, không khuyến khích xem dự đoán thống kê là bảo đảm trúng thưởng.

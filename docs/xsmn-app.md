# Ứng dụng thống kê xổ số miền Nam

Ứng dụng là dashboard Streamlit chạy local, dùng SQLite làm nguồn dữ liệu chuẩn. Phần nghiệp vụ nằm trong package `src/xsmn/`, tách khỏi giao diện `src/xsmn_app.py` để có thể tái sử dụng cho API hoặc frontend khác.

## Cài đặt và chạy

Yêu cầu `uv`; dự án hiện khóa Python `3.14.*`.

```powershell
Set-Location '<đường-dẫn-đến-repository>'
uv sync --frozen --no-dev --no-install-project
```

Tải dữ liệu gần đây:

```powershell
uv run src/fetch_xsmn.py --days 365
```

Hoặc chọn khoảng ngày cụ thể:

```powershell
uv run src/fetch_xsmn.py --from-date 2025-01-01 --to-date 2026-07-11
```

Khởi động giao diện:

```powershell
uv run streamlit run src/xsmn_app.py
```

Mở [http://localhost:8501](http://localhost:8501). Trong ứng dụng cũng có nút cập nhật dữ liệu theo số ngày ở thanh bên.

## Chức năng

1. **Tổng quan:** lọc thời gian/đài, thống kê đuôi 2 hoặc 3 số, top tần suất, tỷ lệ quan sát, tỷ lệ kỳ và ma trận 00–99.
2. **Kết quả theo kỳ:** xem đủ 18 kết quả từ giải tám đến đặc biệt của từng đài.
3. **Dò vé:** kiểm tra vé 6 chữ số theo đúng ngày và đài; cộng mọi giải trùng.
4. **Lịch mở thưởng:** hiển thị các đài theo từng ngày trong tuần và kỳ gần nhất.
5. **Dự đoán tham khảo:** xếp hạng đuôi 2 số và tạo dãy 6 số cho lần mở tiếp theo.

## Mô hình dữ liệu

- `draws`: một bản ghi cho mỗi cặp ngày–đài, kèm URL nguồn, thời điểm tải và phiên bản parser.
- `prize_results`: một bản ghi cho mỗi giải/thứ tự kết quả.
- Số trúng được lưu dưới dạng `TEXT`, không chuyển sang số nguyên, để giữ các giá trị như `05`, `007` hoặc `003405`.
- Một kỳ chỉ được ghi khi đủ cơ cấu: G8=1, G7=1, G6=3, G5=1, G4=7, G3=2, G2=1, G1=1, ĐB=1.

Database local mặc định là `data/xsmn.sqlite3`. File này nằm trong `.gitignore`.

## Quy tắc dò vé

Vé truyền thống miền Nam gồm 6 chữ số. Các giải thường so khớp liên tiếp từ phải sang trái:

| Giải | Số chữ số khớp | Giá trị mỗi giải |
|---|---:|---:|
| Đặc biệt | 6 | 2.000.000.000₫ |
| Nhất | 5 | 30.000.000₫ |
| Nhì | 5 | 15.000.000₫ |
| Ba | 5 | 10.000.000₫ |
| Tư | 5 | 3.000.000₫ |
| Năm | 4 | 1.000.000₫ |
| Sáu | 4 | 400.000₫ |
| Bảy | 3 | 200.000₫ |
| Tám | 2 | 100.000₫ |

- **Phụ đặc biệt:** trùng 5 số cuối giải đặc biệt và chỉ khác số đầu, trị giá 50.000.000₫.
- **Khuyến khích:** trùng số đầu, chỉ sai đúng một trong 5 vị trí còn lại của giải đặc biệt, trị giá 6.000.000₫.
- Vé trùng nhiều giải được cộng đủ mọi kết quả phù hợp.

Giá trị trên là mức giải danh nghĩa của vé 10.000₫, chưa xử lý thuế hoặc thủ tục lĩnh thưởng. Cơ cấu được đối chiếu với [Công ty XSKT TP.HCM](https://www.xskthcm.com/tin-tuc/thong-bao/co-cau-giai-thuong-moi-ve-xo-so-truyen-thong-cho-seri-1-trieu-ve.html) và [Công ty XSKT Bình Dương](https://www.xosobinhduong.com.vn/tien-ich/co-cau-giai-thuong).

Bộ dò giá trị giải chỉ áp dụng cho kỳ từ **01/01/2017**, là ngày cơ cấu hiện tại bắt đầu có hiệu lực. Dữ liệu cũ hơn vẫn có thể dùng cho thống kê, nhưng ứng dụng chủ động từ chối tính tiền thưởng để không áp sai cơ cấu lịch sử.

## Định nghĩa thống kê

- **Tỷ lệ kết quả:** số lần đuôi xuất hiện chia cho tổng số kết quả giải trong mẫu.
- **Tỷ lệ kỳ:** số kỳ đài có đuôi xuất hiện ít nhất một lần chia cho tổng số kỳ đài.
- **Ngày quay chưa về:** số ngày mở thưởng hoàn tất kể từ lần xuất hiện gần nhất. Các đài cùng ngày không bị sắp thành những “kỳ” nối tiếp giả tạo.
- Ngày thiếu dữ liệu không được tự động tính như một lần không xuất hiện.

## Dự đoán

Điểm dàn 2 số là tổ hợp:

- 50% tần suất trong cửa sổ gần đây.
- 30% tần suất dài hạn.
- 20% độ trễ kể từ lần xuất hiện cuối.

Ba thành phần được chuẩn hóa min–max độc lập về thang 0–100 trước khi ghép điểm; vì vậy chúng là **điểm thành phần tương đối**, không phải tần suất phần trăm hoặc xác suất.

Dãy 6 số sử dụng tần suất chữ số theo từng vị trí của giải đặc biệt, có làm trơn Laplace và seed cố định theo ngày–đài để tái lập kết quả. Đây là **điểm xếp hạng**, không phải xác suất đã hiệu chỉnh và không tạo lợi thế được chứng minh so với quay ngẫu nhiên.

Nhật ký dự đoán hiển thị hai lớp đối chiếu:

- Nền xanh trên dãy đặc biệt đánh dấu phần đuôi trùng liên tiếp; dòng “trùng chính xác toàn giải” chỉ xuất hiện khi số dự đoán bằng số thật trong đúng hạng giải.
- Chỉ số hiệu suất luôn đặt cạnh baseline ngẫu nhiên tính từ cơ cấu giải. Với một bảng đủ 18 kết quả, số khớp chính xác kỳ vọng của dự đoán ngẫu nhiên là `0,012551` kết quả/kỳ; xác suất một dãy đặc biệt khớp ít nhất 2 số cuối là `1%`.

Backtest dùng walk-forward: mỗi kỳ chỉ được dự đoán bằng dữ liệu có trước ngày quay, 100 kỳ đầu mỗi đài làm warm-up, và báo khoảng tin cậy 95%. Chạy lại bằng:

```powershell
uv run python analysis/prediction_backtest.py
```

Không nâng phiên bản mô hình chỉ vì một biến thể có điểm trung bình cao hơn trên cùng tập dữ liệu; chênh lệch phải ổn định ngoài mẫu và khoảng tin cậy không còn bao phủ baseline.

## Kiểm thử

```powershell
uv sync --frozen --group testing --group linting --no-install-project
uv run --frozen --group testing pytest -q
uv run --frozen --group linting ruff check src/xsmn_app.py src/xsmn src/fetch_xsmn.py tests
uv run --frozen --group linting ruff format --check src/xsmn_app.py src/xsmn src/fetch_xsmn.py tests
uv run --frozen --group linting isort --check-only src/xsmn_app.py src/xsmn src/fetch_xsmn.py tests
```

Test bao phủ parser 3/4 đài, số 0 đầu, cơ cấu giải, dò phụ đặc biệt/khuyến khích, nhiều giải đồng thời, SQLite, thống kê, tính tái lập của dự đoán và smoke test Streamlit có biểu đồ, bảng và thao tác dò vé.

## Nguồn và giới hạn

- Kết quả được đọc từ trang AMP công khai của [xoso.com.vn](https://xoso.com.vn/xo-so-mien-nam/xsmn-p1.html).
- Lịch tuần và cơ cấu giải được đối chiếu với [Quyết định 98/QĐ-XSKT](https://xosokiengiang.vn/userfiles/files/QD-98-XSKT.pdf) và [thể lệ vé truyền thống của XSKT Kiên Giang](https://xosokiengiang.vn/userfiles/files/THE%20LE%20THAM%20GIA%20DU%20THUONG%20XO%20SO%20TRUYEN%20THONG.pdf).
- HTML nguồn có thể thay đổi; parser từ chối ghi kỳ thiếu giải thay vì coi dữ liệu thiếu là kết quả hợp lệ.
- Lịch đài nằm trong `src/xsmn/config.py` để có thể cập nhật tập trung khi có thông báo chính thức mới.
- Ứng dụng phục vụ học tập/tham khảo, không khuyến khích coi dự đoán thống kê là bảo đảm trúng thưởng.

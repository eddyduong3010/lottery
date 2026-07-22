# Vietnam Lottery (XSMB) Analysis

Using GitHub Action to automatically fetch and analyze results of the Vietnam lottery daily.

This project is created by [Minh Dương](https://github.com/eddyduong3010). I create this project for education purpose only. You can use any resource in this repository for free without any permission.

Sử dụng GitHub Action để tự động hoá thu thập và phân tích kết quả xổ số hàng ngày của Việt Nam.

Dự án này được tạo bởi [Minh Dương](https://github.com/eddyduong3010). Tôi tạo dự án này chỉ nhằm mục đích học tập. Bạn có thể sử dụng bất kỳ tài nguyên nào trong kho lưu trữ này một cách miễn phí mà không cần bất kỳ sự cho phép nào.

| Lottery (Xổ số) | Loto (Lô tô) |
| :------------: | :----------: |
| <table><tr><td>Date (Ngày)</td><td>11-07-2026</td></tr><tr><td>Special (Giải đặc biệt)</td><td>09401</td></tr><tr><td>First (Giải nhất)</td><td>36061</td></tr><tr><td>Second (Giải nhì)</td><td>77252, 60057</td></tr><tr><td rowspan="2">Third (Giải ba)</td><td>51690, 28065, 93903</td></tr><tr><td>75131, 65832, 12023</td></tr><tr><td>Fourth (Giải tư)</td><td>3626, 1683, 2414, 9774</td></tr><tr><td rowspan="2">Fifth (Giải năm)</td><td>9198, 1500, 3618</td></tr><tr><td>8389, 9640, 0250</td></tr><tr><td>Sixth (Giải sáu)</td><td>425, 731, 475</td></tr><tr><td>Seventh (Giải bảy)</td><td>06, 26, 73, 72</td></tr></table> | <table><tr><td>First (Đầu)</td><td>Last (Đuôi)</td></tr><tr><td>0</td><td>0, 1, 3, 6</td></tr><tr><td>1</td><td>4, 8</td></tr><tr><td>2</td><td>3, 5, 6, 6</td></tr><tr><td>3</td><td>1, 1, 2</td></tr><tr><td>4</td><td>0</td></tr><tr><td>5</td><td>0, 2, 7</td></tr><tr><td>6</td><td>1, 5</td></tr><tr><td>7</td><td>2, 3, 4, 5</td></tr><tr><td>8</td><td>3, 9</td></tr><tr><td>9</td><td>0, 8</td></tr></table> |

## Ứng dụng XSMN

Repository có thêm ứng dụng web local cho xổ số miền Nam với các chức năng:

- Thống kê tần suất và tỷ lệ xuất hiện của đuôi 2/3 số theo đài, thời gian và phạm vi giải.
- Xem lịch mở thưởng trong tuần và toàn bộ kết quả của từng kỳ/đài.
- Dò vé 6 số, cộng đủ các giải trùng gồm giải thường, phụ đặc biệt và khuyến khích.
- Xếp hạng dàn 2 số và sinh dãy 6 số tham khảo cho kỳ kế tiếp bằng mô hình thống kê minh bạch.
- Nhật ký dự đoán highlight trực tiếp phần số trùng với kết quả thật và so hiệu suất với baseline ngẫu nhiên lý thuyết.
- Tab **Vietlott Power 6/55**: cập nhật kết quả Vietlott, thống kê tần suất 01-55, xem đầy đủ từng kỳ, dò bộ 6 số theo Jackpot 1/2 và các giải cố định, lịch quay Thứ 3/5/7, và sinh bộ số tham khảo.

> Dự đoán chỉ phục vụ tham khảo/thử nghiệm. Xổ số là quá trình ngẫu nhiên; tần suất lịch sử không bảo đảm kết quả tương lai.

Backtest walk-forward tái lập nằm tại `analysis/prediction_backtest.py` và
`analysis/prediction_backtest.ipynb`. Mô hình chỉ nên đổi phiên bản khi hiệu năng out-of-sample vượt baseline toán học
với khoảng tin cậy đủ rõ; không dùng một vài kỳ gần nhất để kết luận.

### Chạy nhanh trên PowerShell

```powershell
uv sync --frozen --no-dev --no-install-project
uv run src/fetch_xsmn.py --days 365
uv run src/fetch_vietlott_power655.py --limit 8
uv run src/fetch_vietlott_power655.py --all
uv run streamlit run src/xsmn_app.py
```

Mở `http://localhost:8501` trong trình duyệt. Dữ liệu XSMN được lưu local tại `data/xsmn.sqlite3`; dữ liệu Vietlott Power 6/55 được lưu tại `data/vietlott_power655.sqlite3`; cả hai đều không được commit. Xem thiết kế, công thức và lệnh kiểm thử trong [docs/xsmn-app.md](docs/xsmn-app.md) và [docs/vietlott-power655-app.md](docs/vietlott-power655-app.md).

Khi khởi động, app tự kiểm tra các kết quả còn thiếu của XSMN trong 30 ngày gần nhất và các ID kỳ Power 6/55 chưa có. Kết quả đồng bộ được cache 15 phút để các lần Streamlit rerun không gọi nguồn liên tục. Có thể cấu hình bằng `LOTTERY_AUTO_SYNC_ENABLED`, `LOTTERY_AUTO_SYNC_TTL_SECONDS` và `XSMN_AUTO_SYNC_BOOTSTRAP_DAYS`.

## Data (Dữ liệu)

|          | CSV | JSON | Parquet |
|----------|-----|------|---------|
| Raw      | [xsmb.csv](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb.csv) | [xsmb.json](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb.json) | [xsmb.parquet](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb.parquet) |
| 2-digits | [xsmb-2-digits.csv](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-2-digits.csv) | [xsmb-2-digits.json](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-2-digits.json) | [xsmb-2-digits.parquet](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-2-digits.parquet) |
| Sparse   | [xsmb-sparse.csv](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-sparse.csv) | [xsmb-sparse.json](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-sparse.json) | [xsmb-sparse.parquet](https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-sparse.parquet) |

## Using

You can use `curl` or `wget` to download data files. Or you can load them directly into DataFrame:

Bạn có thể sử dụng curl hoặc wget để tải các tệp dữ liệu. Hoặc bạn có thể tải chúng trực tiếp vào DataFrame:

```sh
wget https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb.csv
```

```sh
curl -O https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-2-digits.csv
```

```python
import pandas as pd

df = pd.read_csv('https://raw.githubusercontent.com/khiemdoan/vietnam-lottery-xsmb-analysis/refs/heads/main/data/xsmb-sparse.csv')
df.info()
```

<details>
  <summary><h2>Analysis of special prices (Phân tích kết quả xổ số)</h2></summary>
  <h3>Amount of day from last appearing (Số ngày từ lần xuất hiện cuối cùng)</h3>

  ![Delta](images/special_delta.jpg)

  <h3>Top 10 amount of day from last appearing (Top 10 số lâu chưa xuất hiện)</h3>

  ![Delta top 10](images/special_delta_top_10.jpg)
</details>

<details>
  <summary><h2>Analysis of one-year Loto results (Phân tích kết quả lô tô trong 1 năm)</h2></summary>

  Max: 128. Min: 74.

  Mean: 97.47. Standard deviation: 10.08.

  <h3>Detail (Chi tiết)</h3>

  ![Detail](images/heatmap.jpg)

  <h3>Top 10</h3>

  ![Top 10](images/top-10.jpg)

  <h3>Distribution (Phân bổ)</h3>

  ![Distribution](images/distribution.jpg)
</details>

<details>
  <summary><h3>Amount of day from last appearing (Số ngày từ lần xuất hiện cuối cùng)</h3></summary>

  ![Delta](images/delta.jpg)

  <h3>Top 10 amount of day from last appearing (Top 10 số lâu chưa xuất hiện)</h3>

  ![Delta top 10](images/delta_top_10.jpg)
</details>

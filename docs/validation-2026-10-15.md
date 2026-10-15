# Kiểm chứng dữ liệu, dự đoán và giao diện — 15/10/2026

## Kết luận

Đã sửa lỗi dùng dữ liệu ngày mục tiêu/tương lai trong Power 6/55, điểm số dễ gây hiểu nhầm, dữ liệu nguồn mâu thuẫn bị cắt bỏ im lặng và điều hướng Streamlit chạy mọi tab. **Chưa có bằng chứng nâng tỷ lệ trúng vượt lựa chọn ngẫu nhiên.** Bản v2 được chọn để có xác suất và nhật ký trung thực; không được quảng bá như thuật toán dự báo chính xác hơn bản cũ.

Phạm vi ứng dụng hiện có: **xổ số truyền thống miền Nam (XSMN) và Vietlott Power 6/55**. Không suy rộng kết luận sang XSMB, XSMT, Mega 6/45, Lotto 5/35 hoặc Keno.

## Dữ liệu và đối chiếu nguồn

Snapshot Git tại `92618d9`:

| Dữ liệu | Số kỳ | Khoảng thời gian |
|---|---:|---|
| XSMN, mỗi đài/ngày là một kỳ | 23.155 | 29/12/2005–12/07/2026 |
| Kết quả giải XSMN | 416.790 | 18 kết quả/kỳ |
| Power 6/55 | 1.370 | 01/08/2017–11/07/2026 |

Kiểm tra mẫu trực tiếp ngày 15/10/2026:

- 6 kỳ HCM trong snapshot từ xoso.com.vn khớp đủ **108/108 giá trị giải** với [Công ty XSKT TP.HCM](https://www.xskthcm.com/). Ví dụ trang [kỳ 11/07/2026](https://www.xskthcm.com/xskt?id=1191). Kết quả này chỉ xác nhận mẫu HCM, chưa xác nhận mọi đài và toàn lịch sử.
- 6 kỳ Power lịch sử khớp [Vietlott](https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/winning-number-655). Snapshot và trang đối chiếu cùng nhà công bố nên đây là kiểm tra lại, không phải hai nguồn độc lập.
- Nguồn Power thứ hai xoso.com.vn trả HTTP 403 trong môi trường này; báo cáo ghi `not_verified`, không coi là đồng thuận. Không bỏ kiểm tra TLS hoặc vượt chặn.
- Đã tải đủ **40 kỳ mới #01371–#01410, từ 14/07 đến 13/10/2026**, từ nguồn chính thức, không lỗi tải. Dữ liệu cập nhật nằm trong bản sao phân tích; snapshot Git được giữ nguyên để tái lập kết quả cũ.
- Phát hiện nhật ký kỳ **01399 ngày 17/09/2026** lưu `06 11 25 27 37 46`, trong khi hai lần đọc trang [kết quả chính thức](https://vietlott.vn/vi/trung-thuong/ket-qua-trung-thuong/655?id=01399&nocatche=1) đều trả `06 11 25 27 37 45`, bóng đặc biệt `15`. Đã sửa riêng kết quả thực tế, giữ nguyên số dự đoán; số khớp của vé đã lưu vẫn là 0. Chi tiết trước/sau và hash nằm trong `power655-01399-correction.json`.
- Kiểm tra toàn bộ 40 kết quả Power trong nhật ký với dữ liệu chính thức: trước sửa có **39 khớp, 1 mâu thuẫn**; sau sửa **40/40 khớp**. CLI trả mã lỗi 1 khi gặp mâu thuẫn, không coi đó là một lần kiểm tra thành công.
- Mỗi phản hồi đối chiếu có URL, thời điểm, SHA-256 và phạm vi. HTML trùng được khử theo danh tính kỳ; mâu thuẫn được báo riêng, không trộn thành nhiều quan sát.

CLI kiểm tra lại (chỉ đọc DB, GET công khai giới hạn mẫu):

```bash
python analysis/source_audit.py --live --sample-draws 6 --output work/source-audit.json
```

## Vì sao điểm dự đoán cũ chưa đáng tin

1. `generate_ticket_candidates` của Power trước đây dùng toàn bộ dữ liệu truyền vào, kể cả ngày mục tiêu/tương lai. V2 áp dụng `< target_date` trước mọi thống kê và ghi đúng training cutoff.
2. Công thức 50% gần đây + 30% dài hạn + 20% lâu chưa về không có bằng chứng ngoài mẫu; min–max 0–100 chỉ là thứ hạng. Số đứng đầu không có nghĩa xác suất trúng 100%.
3. Parser XSMN từng cắt bỏ kết quả thừa và cắt chữ số quá dài ở dữ liệu cũ. V2 chỉ bỏ các khối lặp giống hệt; dữ liệu sai/mâu thuẫn bị từ chối. Các giá trị cũ đã bị biến đổi không thể khôi phục chỉ từ snapshot, nên phân tích hiện hành loại thời kỳ trước 2017.
4. Dự đoán bổ sung sau kỳ quay và bản ghi thiếu bằng chứng thời điểm không thể dùng làm bằng chứng dự báo trước. Nhật ký nay mặc định lọc theo phiên bản và thời điểm đủ điều kiện; vẫn giữ các bản lưu cũ để truy vết.

## Công thức và baseline

- Power, tần suất mô tả có làm trơn: `q_i = (c_i + 100 × 6/55)/(T + 100)`. Chỉ đếm 6 bóng chính, tách bóng đặc biệt. 100 là prior cố định để kiểm nghiệm, không được gọi là tham số tối ưu đã chứng minh.
- XSMN, tần suất trên từng kết quả giải: `q_i = (c_i + 1)/(N + K)`, với `K=100` hoặc `1000`. Không nhầm tỷ lệ từng kết quả với tỷ lệ có ít nhất một lần xuất hiện trong kỳ.
- Bỏ trọng số độ trễ khỏi xếp hạng dự báo. Độ trễ vẫn có thể hiển thị như thông tin lịch sử.
- Khi chưa chứng minh lợi thế, bộ số tham khảo v2 lấy mẫu đều, seed cố định theo ngày/đài/phiên bản; tăng số lượng bộ không đổi bộ đầu. Đây là lựa chọn cơ sở, không phải một mô hình AI có độ chính xác cao hơn.

Theo cơ cấu [Power 6/55 của Vietlott](https://info.vietlott-sms.vn/hdsd/game_power.html), sáu số chính khác nhau từ 01–55 và bóng đặc biệt từ 49 số còn lại. Từ đó suy ra xác suất Jackpot 1 một vé `1/C(55,6)=1/28.989.675`, số chính khớp kỳ vọng `36/55≈0,6545`.

Với mô hình tham chiếu 18 kết quả có đuôi đều và độc lập, dàn 10 đuôi có ít nhất một số trùng đạt `1−0,9^18≈84,99%` ngay cả khi chọn ngẫu nhiên. Đây là giả định baseline toán học; không phải chứng minh các kết quả thực tế độc lập, và không tương đương trúng vé truyền thống.

## Kết quả thực nghiệm

Mọi dự báo walk-forward chỉ dùng kỳ trước; 100 kỳ đầu làm khởi tạo. XSMN dùng 10.519 kỳ từ 2017, đánh giá 8.419 kỳ sau khởi tạo từng đài. Power đánh giá 1.270 kỳ từ snapshot. Mọi biến thể dùng cùng ngày kiểm tra và cùng số lựa chọn.

| Chỉ số | Công thức cũ | Tham khảo đều v2 | Baseline lý thuyết |
|---|---:|---:|---:|
| Power, số chính khớp trung bình/1 vé, 1.270 kỳ | 0,652756 | 0,644882 | 0,654545 |
| XSMN, khớp ít nhất 2 số cuối ĐB, 8.419 kỳ | 0,9740% | 1,1284% | 1% |
| XSMN, tổng khớp chính xác trong bảng đủ giải | 106 | 105 | Xấp xỉ 105,67 |

Khoảng tin cậy 95% Power: cũ `[0,613386; 0,693720]`, v2 `[0,606299; 0,684252]`, đều chứa baseline. Tỷ lệ khớp toàn bộ 6 chữ số ĐB XSMN trong mẫu là 0 cho cả hai. Không lấy mức tăng đuôi 2 số để tuyên bố tăng khả năng trúng ĐB.

Kiểm tra bổ sung 40 kỳ Power sau snapshot DB, với công thức cố định trước khi tải lại từ nguồn chính thức:

| Chỉ số | Công thức cũ | Tham khảo đều v2 |
|---|---:|---:|
| Số chính khớp trung bình/vé | 0,750 | 0,475 |
| Khoảng tin cậy 95% | 0,550–0,975 | 0,300–0,675 |

**V2 thấp hơn bản cũ trên 40 kỳ này**, không có căn cứ nói bản mới tăng tỷ lệ trúng. Cả hai khoảng vẫn chứa baseline 0,6545; mẫu nhỏ và không được dùng để tinh chỉnh rồi đánh giá lại trên chính nó. Tệp provenance ghi hash công thức trước khi lấy dữ liệu chính thức. Tuy nhiên, `prediction_history.json` trong repo đã có kết quả các kỳ này, nên đây là **kiểm tra lịch sử bổ sung, không phải tập holdout bảo đảm chưa được nhìn thấy**, và không phải đăng ký nghiên cứu từ trước khi kỳ quay diễn ra.

Đánh giá xác suất bằng Brier/log loss cũng không ủng hộ tần suất làm trơn vượt baseline. Trong 40 kỳ mới, Brier đều `0,09719008`; dài hạn `0,09724908`; cửa sổ gần đây `0,09732291` (nhỏ hơn tốt hơn). Các khoảng chênh lệch chưa xác nhận lợi thế. Nhật ký gốc có 40 dự báo Power đã đối chiếu, trung bình tính lại 0,875 số chính/vé, khoảng bootstrap 95% 0,65–1,10. Đây là các bản lưu thực tế khác với mô phỏng công thức cố định (khác cửa sổ/đầu vào tại thời điểm sinh); không được gộp hai kết quả. Nhật ký XSMN có 295 kỳ đã đối chiếu nhưng thiếu timestamp riêng lúc sinh bảng đầy đủ giải: timestamp của vé ĐB ban đầu không chứng minh các dự đoán giải khác cũng được tạo trước giờ quay. Giao diện ẩn hiệu suất bảng giải không đủ bằng chứng theo mặc định.

Phân đoạn cuối 20% của snapshot là chẩn đoán theo thời gian, không được coi là tập chưa từng nhìn thấy khi đã thử nhiều biến thể. Khoảng dùng khối ngày cho điểm xác suất; quá ít ngày sẽ ghi không đủ dữ liệu tính khoảng.

Phương pháp dựa trên [time-series cross-validation của Hyndman và Athanasopoulos](https://otexts.com/fpp3/tscv.html), [proper scoring rules của Gneiting–Raftery](https://sites.stat.washington.edu/people/raftery/Research/PDF/Gneiting2007jasa.pdf), và yêu cầu minh bạch nhiều phép thử trong [tuyên bố ASA](https://www.amstat.org/asa/files/pdfs/p-valuestatement.pdf). Báo cáo đủ các biến thể; không lựa chọn một chiến thắng ngẫu nhiên để quảng cáo.

## Giao diện và kiểm thử

- Điều hướng hai cấp **Sản phẩm → Chức năng**; chỉ render màn hình đang chọn.
- Dọn vùng nội dung khi thay màn hình và ẩn các phần tử được Streamlit đánh dấu cũ trong lúc rerun, giữ khoảng trống để giảm xê dịch bố cục. Selector được kiểm tra theo frontend Streamlit 1.59.0; cần kiểm tra lại khi nâng framework.
- Bộ lọc và số vé giữ riêng giữa hai sản phẩm; khoảng ngày chưa đủ có hướng dẫn.
- Cập nhật Power hiển thị kết quả mới ngay trong lần xử lý; không cần bấm thêm tab.
- Bố cục gọn ở cửa sổ hẹp, báo ngày dữ liệu và tình trạng cũ; phần kỹ thuật đưa vào mở rộng.
- Nhật ký chỉ tính khi mở phần kiểm chứng; mặc định chỉ một phiên bản và bản ghi đủ bằng chứng thời điểm. Bảng đủ giải dùng bằng chứng thời điểm riêng; ngày mới nhất của nhật ký và database thống kê được hiển thị tách biệt. Bản dự đoán đã khóa được giữ nguyên; v2 áp dụng cho kỳ chưa có bản lưu.
- Hai file chạy gốc chuyển thành launcher của bản canonical để tránh sửa một nơi nhưng chạy bản cũ khác.

**Kiểm thử cuối: 93 passed in 43,96s**, Python 3.14, Streamlit 1.59.0. `git diff --check` đạt; Ruff đạt trên các file Python đã sửa (giữ ngoại lệ BLE001/S110 vốn có trong app và luồng tải nguồn). Không chạy đồng bộ mạng thật trong suite này.

Các test bao phủ rò rỉ dữ liệu tương lai, xác suất tổ hợp, số 0 đầu, xung đột nguồn, tham số, cutoff/phiên bản, không backfill dự đoán quá khứ, điều hướng, giữ filter, auth, refresh và cô lập nhật ký. Chạy `python -m pytest -q`; cấu hình pytest trỏ suite canonical `tests/` để tránh file test trùng tên ở gốc.

Sau chỉnh CSS cuối, ba bài kiểm thử điều hướng trọng tâm đạt. Trình duyệt xác nhận phần tử cũ có opacity=0 và transition=0s khi đổi sản phẩm; ảnh không còn trộn form/kết quả cũ dưới tiêu đề mới.

Đã kiểm tra trình duyệt local: đổi XSMN↔Power, chuyển chức năng, nhập/dò vé và quay lại vẫn giữ số vé; bố cục ở panel hẹp. Các lần đồng bộ thật và dữ liệu cập nhật phục vụ phân tích được tách khỏi kiểm thử UI trên bản sao DB.

## Trạng thái bàn giao

Mã và tài liệu sửa ở checkout local, có patch áp dụng lên commit gốc. Chưa commit, push, tạo PR hoặc deploy website. Chưa xác nhận dữ liệu XSMN mới cho toàn bộ 21 đài; nguồn phụ Power bị chặn. Không có tuyên bố bảo đảm trúng hoặc nâng độ chính xác đã chứng minh.

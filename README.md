# Chọn Số Tỉnh Táo

App chọn số Vietlott **Mega 6/45** và **Power 6/55**: chọn bộ số ít người chọn, thống kê toàn bộ lịch sử quay, kiểm chứng các mẹo chọn số trên dữ liệu thật, tính xác suất và giá trị thật của một vé, cùng trợ lý AI (Google Gemini).

> Mỗi kỳ quay là ngẫu nhiên. App và AI không dự đoán được số sẽ ra. Hãy chơi cho vui với số tiền nhỏ.

## Cách hoạt động

- `index.html`: toàn bộ app, chạy trên trình duyệt. Vé đã lưu và khóa Gemini chỉ nằm trên máy người dùng.
- `data/draws.json`, `data/meta.json`: kết quả các kỳ quay và jackpot hiện tại.
- `scripts/update.py`: lấy kết quả mới. GitHub Actions (`.github/workflows/update.yml`) chạy nó 3 lần mỗi ngày, lưu dữ liệu mới và đăng lại app lên GitHub Pages.

Nguồn dữ liệu: bộ dữ liệu [vietvudanh/vietlott-data](https://github.com/vietvudanh/vietlott-data), rồi các trang kết quả (đọc bằng Gemini), rồi Gemini kèm Google Search. Một kỳ mới chỉ được ghi khi nguồn tin cậy có nó hoặc hai nguồn khác nhau khớp đủ ngày và số.

## Cài đặt một lần

1. **Settings → Pages → Source: GitHub Actions.**
2. **Settings → Secrets and variables → Actions → New repository secret**: tên `GEMINI_API_KEY`, giá trị là khóa lấy ở [aistudio.google.com](https://aistudio.google.com).
3. **Actions → Cập nhật kết quả và đăng app → Run workflow.**

App nằm ở `https://<tên-tài-khoản>.github.io/chon-so-tinh-tao/`. Mở bằng Safari rồi chọn **Chia sẻ → Thêm vào MH chính** để dùng như một app.

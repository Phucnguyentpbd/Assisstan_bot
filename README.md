# 🏛️ Financial Assistant Bot & X Intel Intelligence Service

Hệ thống Trợ lý Tài chính Toàn diện trên **Telegram** kết hợp **124 công cụ Model Context Protocol (MCP)** và **Dịch vụ Tình báo X (Twitter) thời gian thực**. Hệ thống tự động phân tích thị trường chứng khoán Việt Nam, tài sản toàn cầu, dòng tiền vĩ mô và phát ngôn của các VIP quốc tế.

---

## 🌟 Tính Năng Nổi Bật

### 1. 📡 Bản Tin Thị Trường Toàn Diện 3 Lần/Ngày (Giờ Việt Nam)
Hệ thống tự động tổng hợp dữ liệu định lượng (VNStock, Yahoo Finance) và dữ liệu định tính (24 nguồn X uy tín) biên tập qua AI Gemini theo chuẩn Wall Street:
- 🌅 **07:30 Sáng — Bản Tin Chiến Lược Đầu Ngày:**
  - Vĩ mô quốc tế qua đêm, S&P 500, Giá Dầu, Tỷ giá USD/VND.
  - Diễn biến Giá Vàng & động thái mua/bán của quỹ **SPDR Gold Trust**.
  - Kịch bản mở cửa và khuyến nghị phiên ATO thị trường Việt Nam.
- 🌤️ **11:30 Trưa — Bản Tin Tổng Kết Phiên Sáng:**
  - Khớp lệnh VN-Index, rổ VN30, thanh khoản và dòng tiền khối ngoại.
  - Tin nóng thời sự kinh tế Việt Nam và quốc tế giữa ngày.
- 🌙 **21:30 Tối — Toàn Cảnh Thị Trường & Vĩ Mô Quốc Tế:**
  - Tổng kết đóng cửa phiên giao dịch VN-Index.
  - Điểm nóng địa chính trị, xung đột quân sự, động thái FED/lãi suất.
  - Thị trường tiền mã hóa (Bitcoin, Ethereum, biến động ví cá voi và chuyên gia Crypto).

### 2. 🌐 Tra Cứu Phát Ngôn VIP Trên X (Twitter) Song Ngữ Anh - Việt
- Cú pháp tra cứu: `/x <username>` (Ví dụ: `/x elonmusk`, `/x realDonaldTrump`, `/x saylor`, `/x cz_binance`, `/x zerohedge`).
- **Tự động dịch Song ngữ:** Hiển thị song song bản gốc 🇺🇸 và bản dịch tiếng Việt 🇻🇳 chuẩn xác về tài chính - công nghệ.
- Đầy đủ thông số: Lượt Like 👍, Retweet 🔄 và Link trực tiếp tới bài viết.

### 3. 🧠 Bộ Não ReAct Agent 124 Công Cụ MCP
- **TCBS TCInvest (54 tools):** RS Rating, P/E, P/B, ROE, sức khỏe tài chính doanh nghiệp, định giá.
- **SSI Securities (42 tools):** Thống kê thị trường, chỉ số ngành dẫn dắt, biến động nhóm vốn hóa.
- **VNStock Realtime (21 tools):** Khớp lệnh realtime, sổ lệnh depth, giao dịch tự doanh và khối ngoại.
- **Yahoo Global Finance (5 tools):** Vàng thế giới, Dầu Brent, S&P 500, Dow Jones, USD/VND, BTC.
- **X Intel Service (2 tools):** Cào tin tức nóng và phát ngôn từ mạng xã hội X.

### 4. 🛡️ Cơ Chế Chuỗi Fallback Model An Toàn (Zero-Downtime)
- Tự động luân chuyển model khi Google API gặp quá tải (503) hoặc chạm hạn mức:
  `gemini-3.8-flash` ➡️ `gemini-3.1-pro-preview` ➡️ `gemini-2.5-flash` ➡️ `gemma-4-31b-it`.

---

## 🏗️ Cấu Trúc Dự Án

```
Assisstan_bot/
├── bot/
│   ├── finance_telebot.py       # Telegram Bot chính, vòng lặp Heartbeat 3 cữ
│   ├── agentic_engine.py        # ReAct Engine 124 MCP tools & dịch song ngữ
│   ├── tcbs_mcp_bridge.py       # Cầu nối tích hợp MCP TCBS
│   ├── ssi_mcp_bridge.py        # Cầu nối tích hợp MCP SSI
│   └── finance-telebot.service  # File cấu hình chạy nền Systemd
├── x_intel/
│   ├── x_intel_service.py       # FastAPI cào tin từ X qua twscrape (Port 8890)
│   ├── watchlist.json           # Danh mục 24 nguồn tin tinh hoa được giám sát
│   └── x-intel-mcp.service      # File cấu hình chạy nền Systemd cho X service
├── .env.example                 # Mẫu cấu hình biến môi trường
├── .gitignore                   # Chặn commit thông tin bí mật và database
├── requirements.txt             # Danh sách thư viện Python cần thiết
└── README.md                    # Tài liệu hướng dẫn
```

---

## 🚀 Hướng Dẫn Cài Đặt & Triển Khai (VPS Linux)

### 1. Chuẩn bị môi trường
```bash
# Clone repository
git clone https://github.com/Phucnguyentpbd/Assisstan_bot.git
cd Assisstan_bot

# Cài đặt Python Virtualenv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Cấu hình biến môi trường
Sao chép `.env.example` thành `.env` và điền các khóa cần thiết:
```bash
cp .env.example .env
nano .env
```
Nội dung `.env`:
```ini
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
LLM_PROVIDER=gemini
LLM_API_KEY=YOUR_GEMINI_API_KEY
LLM_MODEL=gemini-3.8-flash
```

### 3. Khởi chạy dịch vụ chạy nền Systemd (24/7)
```bash
# Cấu hình dịch vụ X Intel Service (Port 8890)
sudo cp x_intel/x-intel-mcp.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now x-intel-mcp.service

# Cấu hình dịch vụ Telegram Bot
sudo cp bot/finance-telebot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now finance-telebot.service
```

Kiểm tra trạng thái hoạt động:
```bash
sudo systemctl status finance-telebot.service
sudo systemctl status x-intel-mcp.service
```

---

## 📋 Bảng Lệnh Tra Cứu Trên Telegram

| Lệnh | Chức năng |
| :--- | :--- |
| `/start` | Khởi động bot và hiển thị bàn phím menu tra cứu nhanh |
| `/baocao` | Tạo và xem ngay Bản Tin Thị Trường Toàn Diện mới nhất |
| `/x <username>` | Tra cứu phát ngôn VIP trên X với bản dịch song ngữ Anh - Việt |
| `/morning` | Nhận bản tin phân tích chiến lược phiên sáng |
| `/bctc <mã>` | Thẩm định và bóc tách báo cáo tài chính chuyên sâu |
| `/tcbs <mã>` | Xem chỉ số định giá, RS Rating từ TCBS |
| `/ssi <mã>` | Tra cứu dữ liệu thị trường và thanh khoản từ SSI |
| `/global <mã>` | Tra cứu tài sản toàn cầu (GOLD, OIL, SP500, USDVND, BTC) |

---

## 🔒 Bảo Mật & Bản Quyền
- Dự án tự động loại trừ mọi khóa API cá nhân, cookie Twitter và database ra khỏi kho mã nguồn thông qua `.gitignore`.
- Phát triển bởi **Phucnguyentpbd**.

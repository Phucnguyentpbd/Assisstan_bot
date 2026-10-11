# 📘 DEV CONTEXT & HỒ SƠ KỸ THUẬT DỰ ÁN ASSISSTAN BOT

> **Tài liệu bàn giao ngữ cảnh (Handover Context)** dành cho AI Agent và nhà phát triển làm việc trên Git Worktree / Workspace này.  
> Cập nhật lần cuối: **12/10/2026**.

---

## 1. 🏗️ KIẾN TRÚC HỆ THỐNG & HẠ TẦNG

### 1.1. Môi trường VPS Oracle Cloud (ARM64 - Ubuntu 24.04)
- **Địa chỉ IP:** `140.245.97.156` (User: `ubuntu`)
- **SSH Alias:** `ssh vps` (Sử dụng private key: `~/.ssh/cloud_harness_oracle`)
- **Múi giờ VPS:** Hệ điều hành chạy giờ UTC. Trong toàn bộ mã nguồn Python sử dụng `pytz.timezone('Asia/Ho_Chi_Minh')` để đồng bộ giờ Việt Nam (UTC+7).

### 1.2. Các dịch vụ chạy nền (Systemd Services)
1. **`x-intel-mcp.service` (Port 8890):**
   - Đường dẫn: `/home/ubuntu/x-intel-mcp/`
   - Virtualenv: `/home/ubuntu/x-intel-venv/`
   - Nhiệm vụ: Sử dụng thư viện `twscrape` cào dữ liệu thời gian thực từ X (Twitter) bằng tài khoản clone đã nạp cookie.
   - Cơ chế thu thập: Quét định kỳ và lưu tích lũy tối đa 100 tin mới nhất vào `recent_feed.json`.
   - Cung cấp REST API:
     - `GET /api/digest?limit=15`: Lấy dòng tin tổng hợp từ 24 nguồn tin tinh hoa.
     - `GET /api/tweets?username={user}&limit=3`: Lấy các bài tweet mới nhất của tài khoản chỉ định.
2. **`finance-telebot.service`:**
   - Đường dẫn: `/home/ubuntu/mcp-finance/`
   - Virtualenv: `/home/ubuntu/vnstock-venv/`
   - Nhiệm vụ: Điều phối bot Telegram, chạy vòng lặp Heartbeat phát 3 bản tin/ngày, tích hợp ReAct Agent Engine 124 công cụ MCP.
3. **Các dịch vụ MCP bổ trợ:**
   - `vnstock-mcp.service` (Port 8888)
   - `global-finance-mcp.service` (Port 8889)

---

## 2. 📡 DANH MỤC 24 NGUỒN TIN X TINH HOA (`watchlist.json`)

| Nhóm Nguồn Tin | Các Tài Khoản X Giám Sát |
| :--- | :--- |
| **Chính Sách & Địa Chính Trị** | `@realDonaldTrump`, `@elonmusk`, `@spectatorindex`, `@sentdefender`, `@visegrad24`, `@Faytuks` |
| **Kinh Tế Vĩ Mô & Dòng Tiền** | `@KobeissiLetter`, `@zerohedge`, `@unusual_whales`, `@charliebilello`, `@Schuldensuehner` |
| **Vàng & Quỹ SPDR Gold Trust** | `@KitcoNewsNOW`, `@PeterSchiff`, `@markets` |
| **Nhân Vật Uy Tín Crypto** | `@saylor`, `@cz_binance`, `@VitalikButerin`, `@CryptoHayes`, `@brian_armstrong`, `@whale_alert` |
| **Kinh Tế & Báo Chí Việt Nam** | `@VietnamNewsVNS`, `@VnExpressNet`, `@vietnamnetvn`, `@TuoiTreNews` |

---

## 3. 🛠️ CÁC LỖI QUAN TRỌNG ĐÃ PHÁT HIỆN & PHƯƠNG ÁN FIX TRIỆT ĐỂ

### Lỗi 1: Ảo giác thời gian & chính sách Fed ("quý 2/2026 thay vì cuối năm 2025")
* **Hiện tượng:** Bot tự sinh ra nội dung: *"xác suất Fed giữ nguyên lãi suất trong kỳ họp tới đang ở mức cao (>80%), với kỳ vọng cắt giảm lãi suất bị đẩy lùi sang quý 2/2026 thay vì cuối năm 2025"*.
* **Nguyên nhân gốc rễ:**
  1. Mốc thời gian hệ thống truyền vào prompt là năm 2026.
  2. Prompt trong hàm `execute_summarization` có câu lệnh ép buộc: *"phải có 3-5 ý chính có số liệu cụ thể (doanh thu, lợi nhuận, tỷ lệ, ngày tháng)"*.
  3. Khi tin tức cào về từ X thiếu dữ liệu cuộc họp Fed, AI Gemini đã tự suy diễn (hallucinate) kịch bản tương lai theo mốc năm 2026 để lấp đầy khuôn mẫu.
* **Cách khắc phục:**
  - Sửa prompt trong `agentic_engine.py` và `finance_telebot.py`: Bổ sung điều khoản nghiêm ngặt **Zero-Hallucination & Anti-Slop**.
  - Quy định bắt buộc: Chỉ trích dẫn 100% dữ liệu thực tế có trong bài tweet hoặc MCP. Tuyệt đối không tự bịa đặt mốc năm/quý tương lai hay chính sách Fed. Nếu không có dữ liệu, ghi rõ: *"Chưa có thông tin mới về Fed"*.

### Lỗi 2: Hiển thị text rác mô phỏng công cụ `[Đang chờ truy xuất dữ liệu từ công cụ...]`
* **Hiện tượng:** Bot in chuỗi `[Đang chờ truy xuất dữ liệu từ ssi_marketdata_list_index_daily_data]` ra khung chat thay vì lấy số liệu.
* **Nguyên nhân:** Model AI đôi khi sinh text mô phỏng hành vi gọi tool thay vì trả về `FunctionCall` object.
* **Cách khắc phục:**
  - Cập nhật System Prompt trong `get_agent_system_prompt()`: Cấm tuyệt đối việc in chuỗi dạng `[Đang chờ truy xuất...]`. Bắt buộc phải thực hiện Function Call thật hoặc phản hồi dữ liệu có sẵn.

### Lỗi 3: Bot từ chối "không có quyền truy cập X (Twitter)" khi chat tự do
* **Hiện tượng:** Khi người dùng nhắn: *"Tổng hợp tóm tắt các tweet mới nhất"*, bot trả lời *"tôi không có quyền truy cập trực tiếp vào X"*.
* **Nguyên nhân:** Dịch vụ X Intel MCP (Port 8890) hoạt động độc lập nhưng chưa được đăng ký vào danh mục công cụ ReAct Agent của `agentic_engine.py` (chỉ có 122 công cụ TCBS, SSI, VNStock, Yahoo Finance).
* **Cách khắc phục:**
  - Bổ sung 2 công cụ MCP mới vào `build_all_mcp_tools()`:
    1. `x_get_hot_feed`: Lấy dòng tin tức nóng tổng hợp từ 24 nguồn tin uy tín.
    2. `x_get_latest_tweets`: Tra cứu phát ngôn thời gian thực theo username.
  - Nâng tổng số công cụ MCP của ReAct Loop lên **124 công cụ**.
  - Cập nhật System Prompt dạy cho AI biết bot có kết nối trực tiếp với X Intel MCP.

### Lỗi 4: Không gửi tự động 3 ca phát tin và bot nói sai "chỉ hoạt động on-demand"
* **Hiện tượng:** Cả ngày không nhận được báo cáo tự động lúc 07:30, 11:30, 21:30. Khi hỏi bot thì bot tự trả lời *"chỉ hoạt động on-demand thay vì tự động"*.
* **Nguyên nhân:**
  1. Vòng lặp `heartbeat_loop` cũ chỉ có điều kiện kiểm tra 1 mốc 7h30 sáng và bị kẹt vào hàm tạo báo cáo cũ.
  2. System prompt của AI chưa được nạp thông tin về cơ chế phát tin 3 lần/ngày.
* **Cách khắc phục:**
  - Viết lại `heartbeat_loop` sử dụng cơ chế Slot Cache (`sent_slots`) cho 3 mốc:
    - 🌅 **07:30 Sáng (VN):** Bản tin Chiến lược Đầu ngày (`morning`).
    - 🌤️ **11:30 Trưa (VN):** Bản tin Tổng kết Phiên sáng (`midday`).
    - 🌙 **21:30 Tối (VN):** Toàn cảnh Thị trường & Vĩ mô Quốc tế (`evening`).
  - Cập nhật nút menu "🌅 Bản Tin Sáng Hôm Nay" và lệnh `/baocao` gọi hàm `generate_master_market_report("auto")`.
  - Cập nhật System Prompt xác nhận rõ bot có cơ chế tự động gửi tin 3 lần/ngày.

### Lỗi 5: `name 'httpx' is not defined` khi gọi `/x <username>`
* **Hiện tượng:** Gõ `/x elonmusk` báo lỗi `name 'httpx' is not defined`.
* **Nguyên nhân:** Thiếu dòng khai báo `import httpx` ở đầu file `finance_telebot.py`.
* **Cách khắc phục:** Đã chèn `import httpx` vào dòng 17 của `finance_telebot.py`.

### Lỗi 6: `Can't parse entities: can't find end of the entity starting at byte offset 9294`
* **Hiện tượng:** Khi bot lấy được tweet của Elon Musk nhưng không hiển thị được ra Telegram, báo lỗi parse entity.
* **Nguyên nhân:** Bài tweet tiếng Anh chứa ký tự đặc biệt (`_`, `@`, link URL). Code cũ bọc dấu in nghiêng `_{raw}_` làm vỡ cú pháp Markdown của Telegram.
* **Cách khắc phục:**
  - Loại bỏ bọc in nghiêng toàn bài.
  - Sử dụng hàm `safe_edit_text` có cơ chế tự động chuyển đổi sang Plain Text an toàn nếu Telegram gặp lỗi cú pháp entity.
  - Cắt phân đoạn (chunks) nếu nội dung vượt quá 4000 ký tự.

### Tính năng nâng cấp: Dịch Song Ngữ Anh - Việt tự động cho tweet VIP
* **Nhu cầu:** Các VIP quốc tế đều tweet bằng tiếng Anh. Người dùng cần bản dịch tiếng Việt ngay bên dưới bản gốc để đọc nhanh.
* **Giải pháp:**
  - Viết hàm `translate_tweets_batch(raw_texts)` sử dụng AI Gemini dịch đồng thời cả 3 bài tweet trong 1 lượt gọi API duy nhất (tốc độ dưới 2 giây).
  - Cấu trúc hiển thị:
    - 🇺🇸 **Nguyên văn:** Trích dẫn chuẩn xác tiếng Anh.
    - 🇻🇳 **Bản dịch tiếng Việt:** Biên dịch mượt mà, đúng thuật ngữ tài chính - công nghệ.
    - 👍 Like, 🔄 Retweet và link gốc bài viết trên X.

---

## 4. 🧠 CHUỖI FALLBACK MODEL AI (ZERO-DOWNTIME)

### 4.1. Luồng ReAct Agent Chuyên Sâu (`agentic_engine.py`)
1. 🥇 `gemini-3-flash-preview` (Tốc độ cao nhất, hỗ trợ function calling tốt nhất).
2. 🥈 `gemini-3.1-flash-lite-preview`
3. 🥉 `gemini-3.1-flash-lite`
4. 🏅 `gemini-2.5-flash` (Chốt chặn dự phòng ổn định nhất).

### 4.2. Luồng Báo Cáo & Dịch Thuật Nhanh (`finance_telebot.py`)
1. 🥇 `gemini-3.8-flash` (Đã kiểm tra hết nghẽn, phản hồi 200 OK siêu nhanh).
2. 🥈 `gemini-3.1-pro-preview`
3. 🥉 `gemini-2.5-flash`
4. 🏅 `gemma-4-31b-it`

---

## 5. 💡 HƯỚNG DẪN LÀM VIỆC TRÊN WORKSPACE NÀY
- Thư mục worktree: `D:\AI AGENT\orca\worktrees\Assisstan_bot`
- Nhánh làm việc: `assisstan-bot-main`
- Remote upstream: `https://github.com/Phucnguyentpbd/Assisstan_bot.git`
- Lệnh đẩy code lên GitHub:
  ```bash
  git add .
  git commit -m "mô tả thay đổi"
  git push assistant_source assisstan-bot-main:main
  ```
- Lệnh kéo cập nhật mới:
  ```bash
  git pull assistant_source main
  ```

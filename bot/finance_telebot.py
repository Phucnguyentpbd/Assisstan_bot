#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FINANCIAL TELEGRAM BOT - ALL-IN-ONE (VNStock, Yahoo Finance, TCBS, SSI)
Chạy độc lập trên VPS, tích hợp trực tiếp 4 nguồn dữ liệu MCP.
"""

import os
import sys
import json
import time
import logging
import asyncio
from datetime import datetime
import urllib.request
import urllib.parse
import httpx
from dotenv import load_dotenv

# Nạp file biến môi trường
ENV_PATH = os.path.join(os.path.dirname(__file__), ".env")
load_dotenv(ENV_PATH)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash").strip()

# Import telegram thư viện
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# Cấu hình logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("FinanceBot")

# Import các MCP Bridges
sys.path.insert(0, os.path.dirname(__file__))
try:
    import tcbs_mcp_bridge
except ImportError:
    tcbs_mcp_bridge = None

try:
    import ssi_mcp_bridge
except ImportError:
    ssi_mcp_bridge = None

try:
    import yfinance as yf
except ImportError:
    yf = None

try:
    from vnstock import Vnstock
except ImportError:
    Vnstock = None


# ==============================================================================
# 1. HÀM TRÍCH XUẤT DỮ LIỆU TỪ VNSTOCK
# ==============================================================================
def get_vnstock_quote(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    try:
        if Vnstock:
            stock = Vnstock().stock(symbol=symbol, source='VCI')
            df_price = stock.trading.price_board([symbol])
            if df_price is not None and not df_price.empty:
                row = df_price.iloc[0]
                return {
                    "symbol": symbol,
                    "price": row.get("Khớp lệnh_Giá", 0),
                    "change": row.get("Khớp lệnh_+/-", 0),
                    "pct_change": row.get("Khớp lệnh_%", 0),
                    "volume": row.get("Khớp lệnh_KL", 0),
                    "ref": row.get("Tham chiếu", 0),
                    "ceil": row.get("Trần", 0),
                    "floor": row.get("Sàn", 0),
                    "high": row.get("Cao", 0),
                    "low": row.get("Thấp", 0),
                    "foreign_buy": row.get("ĐTNN_Mua", 0),
                    "foreign_sell": row.get("ĐTNN_Bán", 0),
                }
    except Exception as e:
        logger.warning(f"Lỗi khi lấy vnstock quote cho {symbol}: {e}")

    # Fallback thử qua TCBS
    return get_tcbs_quote_fallback(symbol)

def get_tcbs_quote_fallback(symbol: str) -> dict:
    if not tcbs_mcp_bridge:
        return {}
    token = tcbs_mcp_bridge.get_valid_access_token()
    if not token:
        return {}
    try:
        res = tcbs_mcp_bridge.call_tcbs_tool("tcinvest-getStockRatio", {"ticker": symbol}, token)
        data = res.get("result", {}).get("content", [{}])[0].get("text", "")
        if data:
            d = json.loads(data)
            return {
                "symbol": symbol,
                "price": d.get("price", 0),
                "pe": d.get("pe", 0),
                "pb": d.get("pb", 0),
                "roe": d.get("roe", 0),
                "cap": d.get("capitalize", 0)
            }
    except Exception as e:
        logger.warning(f"Lỗi fallback TCBS cho {symbol}: {e}")
    return {}


# ==============================================================================
# 2. HÀM TRÍCH XUẤT DỮ LIỆU TỪ YAHOO FINANCE (GLOBAL)
# ==============================================================================
GLOBAL_TICKERS = {
    "GOLD": "GC=F",
    "VANG": "GC=F",
    "XAU": "GC=F",
    "OIL": "BZ=F",
    "DAU": "BZ=F",
    "BRENT": "BZ=F",
    "BTC": "BTC-USD",
    "BITCOIN": "BTC-USD",
    "ETH": "ETH-USD",
    "SP500": "^GSPC",
    "S&P500": "^GSPC",
    "NASDAQ": "^IXIC",
    "DOWJONES": "^DJI",
    "DXY": "DX-Y.NYB",
    "USDVND": "USDVND=X",
    "EURUSD": "EURUSD=X"
}

def get_global_quote(symbol: str) -> dict:
    sym_upper = symbol.upper().strip()
    ticker_sym = GLOBAL_TICKERS.get(sym_upper, sym_upper)

    if not yf:
        return {"error": "yfinance chưa được cài đặt"}

    try:
        ticker = yf.Ticker(ticker_sym)
        info = ticker.fast_info
        last_price = getattr(info, 'last_price', None) or getattr(info, 'regular_market_price', None)
        prev_close = getattr(info, 'previous_close', None)
        
        if last_price is None:
            hist = ticker.history(period="2d")
            if not hist.empty:
                last_price = hist['Close'].iloc[-1]
                prev_close = hist['Close'].iloc[-2] if len(hist) > 1 else last_price

        if last_price is not None:
            change = (last_price - prev_close) if prev_close else 0
            pct_change = ((change / prev_close) * 100) if prev_close else 0
            return {
                "symbol": sym_upper,
                "ticker": ticker_sym,
                "price": round(last_price, 2),
                "change": round(change, 2),
                "pct_change": round(pct_change, 2),
                "currency": getattr(info, 'currency', 'USD')
            }
    except Exception as e:
        logger.warning(f"Lỗi khi tra cứu global quote {ticker_sym}: {e}")
    return {}


# ==============================================================================
# 3. HÀM TRÍCH XUẤT DỮ LIỆU TỪ TCBS MCP
# ==============================================================================
def get_tcbs_analysis(symbol: str) -> str:
    symbol = symbol.upper().strip()
    if not tcbs_mcp_bridge:
        return "❌ Module TCBS MCP bridge chưa sẵn sàng."

    token = tcbs_mcp_bridge.get_valid_access_token()
    if not token:
        return "⚠️ TCBS chưa được xác thực. Vui lòng chạy lệnh đăng nhập một lần."

    results = []
    # 1. Chỉ số tài chính & định giá
    try:
        r1 = tcbs_mcp_bridge.call_tcbs_tool("tcinvest-getStockRatio", {"ticker": symbol}, token)
        text1 = r1.get("result", {}).get("content", [{}])[0].get("text", "")
        if text1:
            d = json.loads(text1)
            results.append(
                f"📊 *Chỉ số tài chính ({symbol})*:\n"
                f"• P/E: `{d.get('pe', 'N/A')}` | P/B: `{d.get('pb', 'N/A')}`\n"
                f"• ROE: `{d.get('roe', 'N/A')}%` | ROA: `{d.get('roa', 'N/A')}%`\n"
                f"• Vốn hóa: `{d.get('capitalize', 'N/A'):,} tỷ VND`\n"
                f"• Đỉnh 52T: `{d.get('priceHighest52w', 'N/A')}` | Đáy 52T: `{d.get('priceLowest52w', 'N/A')}`"
            )
    except Exception as e:
        logger.warning(f"Lỗi TCBS getStockRatio: {e}")

    # 2. Đánh giá tổng hợp / Khuyến nghị
    try:
        r2 = tcbs_mcp_bridge.call_tcbs_tool("tcinvest-getGeneralRating", {"ticker": symbol}, token)
        text2 = r2.get("result", {}).get("content", [{}])[0].get("text", "")
        if text2:
            d2 = json.loads(text2)
            results.append(
                f"⭐ *Đánh giá TCInvest*:\n"
                f"• Điểm tổng hợp: `{d2.get('overallScore', 'N/A')}/5`\n"
                f"• Định giá: `{d2.get('valuation', 'N/A')}`\n"
                f"• Sức khỏe tài chính: `{d2.get('financialHealth', 'N/A')}`"
            )
    except Exception as e:
        logger.warning(f"Lỗi TCBS getGeneralRating: {e}")

    return "\n\n".join(results) if results else f"Không tìm thấy dữ liệu TCBS cho mã {symbol}."


# ==============================================================================
# 4. HÀM TRÍCH XUẤT DỮ LIỆU TỪ SSI MCP
# ==============================================================================
def get_ssi_overview(symbol: str) -> str:
    symbol = symbol.upper().strip()
    if not ssi_mcp_bridge:
        return "❌ Module SSI MCP bridge chưa sẵn sàng."

    token = ssi_mcp_bridge.get_valid_access_token()
    if not token:
        return (
            "⚠️ *SSI MCP chưa được đăng nhập*.\n\n"
            "Vui lòng chạy lệnh sau trên máy tính của bạn để đăng nhập tài khoản SSI (chỉ cần làm 1 lần):\n"
            "`python ssi_auth.py`"
        )

    try:
        # Thử gọi tool thị trường SSI
        res = ssi_mcp_bridge.call_ssi_tool("market-stock-quote", {"symbol": symbol}, token)
        content = res.get("result", {}).get("content", [{}])[0].get("text", "")
        if content:
            return f"🏛 *Dữ liệu thị trường SSI ({symbol})*:\n{content[:800]}"
    except Exception as e:
        logger.warning(f"Lỗi SSI MCP call: {e}")

    return f"Không tìm thấy dữ liệu SSI cho mã {symbol} hoặc chưa cấp quyền tool."


# ==============================================================================
# 5. HÀM GỌI AI LLM (GEMINI / OPENAI / GROQ)
# ==============================================================================
def ask_llm(prompt: str, context: str = "") -> str:
    if not LLM_API_KEY:
        return ""

    now_vn = datetime.now(VN_TZ)
    weekday_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    weekday_str = weekday_map[now_vn.weekday()]
    date_str = now_vn.strftime("%d/%m/%Y")
    time_str = now_vn.strftime("%H:%M:%S")

    full_system = (
        f"THÔNG TIN THỜI GIAN THỰC TẾ:\n"
        f"- Hôm nay là: {weekday_str}, ngày {date_str} (Giờ Việt Nam: {time_str}).\n\n"
        "Bạn là Chuyên gia Tư vấn Tài chính & Chứng khoán thông minh, phản hồi ngắn gọn, "
        "chuyên nghiệp, hỗ trợ định dạng Markdown Telegram (sử dụng in đậm, in nghiêng, bullet). "
        "Dữ liệu thị trường cập nhật được cung cấp trong phần bối cảnh."
    )

    messages = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": f"Bối cảnh dữ liệu:\n{context}\n\nCâu hỏi: {prompt}"}
    ]

    # Hỗ trợ Gemini API trực tiếp theo chuỗi Fallback chuẩn:
    # 1. gemini-3-flash-preview -> 2. gemini-3.1-flash-lite-preview -> 3. gemini-2.5-flash
    if LLM_PROVIDER == "gemini":
        candidate_models = [
            "gemini-3-flash-preview",
            "gemini-3.1-flash-lite-preview",
            "gemini-2.5-flash"
        ]

        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": f"{full_system}\n\nBối cảnh dữ liệu:\n{context}\n\nCâu hỏi: {prompt}"}
                    ]
                }
            ],
            "generationConfig": {
                "temperature": 0.0,
                "topP": 0.1,
                "maxOutputTokens": 3000
            }
        }

        for cur_model in candidate_models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{cur_model}:generateContent?key={LLM_API_KEY}"
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"}
                )
                with urllib.request.urlopen(req, timeout=25) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    text_resp = data["candidates"][0]["content"]["parts"][0]["text"]
                    logger.info(f"Phản hồi thành công từ model: {cur_model}")
                    return text_resp
            except urllib.error.HTTPError as e:
                logger.warning(f"Model {cur_model} phản hồi HTTP {e.code}, tự động fallback sang model tiếp theo...")
                continue
            except Exception as e:
                logger.warning(f"Lỗi khi gọi {cur_model}: {e}, thử model tiếp theo...")
                continue
        return ""

    # Hỗ trợ OpenAI / Groq / OpenRouter qua chuẩn OpenAI
    base_urls = {
        "openai": "https://api.openai.com/v1/chat/completions",
        "groq": "https://api.groq.com/openai/v1/chat/completions",
        "openrouter": "https://openrouter.ai/api/v1/chat/completions"
    }
    url = base_urls.get(LLM_PROVIDER, "https://api.openai.com/v1/chat/completions")
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "max_tokens": 800
    }
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json"
            }
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Lỗi OpenAI-compatible API ({LLM_PROVIDER}): {e}")
        return ""


# ==============================================================================
# 6. TELEGRAM BOT HANDLERS & GIAO DIỆN
# ==============================================================================
def get_main_keyboard():
    keyboard = [
        ["🌅 Bản Tin Sáng", "📈 Thị trường VN-Index"],
        ["📊 Báo cáo cổ phiếu", "📑 Phân tích BCTC"],
        ["🪙 Vàng & Thế giới", "🌐 Phát ngôn VIP trên X"],
        ["⌨️ Bảng Tra Lệnh", "💡 Trợ giúp & Hướng dẫn"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_commands_table_text() -> str:
    return (
        "📋 *BẢNG TRA CỨU TOÀN BỘ LỆNH & KỸ NĂNG CỦA BOT*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📊 *BÁO CÁO & PHÂN TÍCH CHUYÊN SÂU:*\n"
        "• `/baocao <mã>` hoặc `/report <mã>` : Lập Báo cáo phân tích đầu tư 5 phần chuẩn Wall Street (VD: `/baocao HPG`)\n"
        "• `/bctc <mã>` hoặc `/statements <mã>` : Bóc tách BCTC, đòn bẩy nợ & cảnh báo rủi ro kế toán (VD: `/bctc VCB`)\n"
        "• `/tomtat <nội dung>` hoặc `/summary` : Tóm tắt cốt lõi tin tức/bài viết theo chuẩn BLUF\n\n"
        "📈 *THỊ TRƯỜNG & CHIẾN LƯỢC:*\n"
        "• `/market` : Báo cáo Toàn diện phiên giao dịch gần nhất (Điểm số, Thanh khoản, Khối ngoại, Nhóm ngành)\n"
        "• `/morning` hoặc `/daily` : Bản tin chiến lược sáng 7h30 trước giờ mở cửa\n"
        "• `/vn <mã>` : Bảng giá realtime, khối lượng & sổ lệnh (VD: `/vn SSI`)\n"
        "• `/tcbs <mã>` : Định giá P/E, P/B, ROE & điểm TCInvest (VD: `/tcbs FPT`)\n"
        "• `/ssi <mã>` : Dữ liệu thị trường SSI Securities (VD: `/ssi TCB`)\n\n"
        "🌍 *HÀNG HÓA & LIÊN THỊ TRƯỜNG QUỐC TẾ:*\n"
        "• `/global gold` : Giá Vàng thế giới realtime\n"
        "• `/global oil` : Giá Dầu thô Brent\n"
        "• `/global btc` : Giá Bitcoin realtime\n"
        "• `/global sp500` : Chỉ số chứng khoán Mỹ S&P 500\n"
        "• `/global usdvnd` : Tỷ giá USD/VND\n\n"
        "🌐 *TÌNH BÁO MẠNG XÃ HỘI X (TWITTER):*\n"
        "• `/x <user>` : Cào tweet mới nhất của các VIP (VD: `/x elonmusk`, `/x realDonaldTrump`, `/x sama`)\n\n"
        "📁 *ĐỌC & XỬ LÝ TÀI LIỆU FILE ĐÍNH KÈM:*\n"
        "• Gửi trực tiếp file PDF, Excel (.xlsx), Word (.docx) hoặc file Text vào chat để bot tự động phân tích!\n\n"
        "💬 *CHAT TỰ DO BẰNG TIẾNG VIỆT:*\n"
        "• Bạn có thể hỏi tự nhiên: _\"So sánh VCB và BID\"_, _\"HPG hôm nay tăng vì sao?\"_, _\"Thị trường hôm nay thế nào?\"_\n\n"
        "🧠 *QUẢN LÝ BỘ NHỚ HỘI THOẠI (CONTEXT MEMORY):*\n"
        "• `/context` hoặc `/memory` : Xem dung lượng ngữ cảnh & mã cổ phiếu đang theo dõi\n"
        "• `/reset` hoặc `/newchat` : Xóa toàn bộ lịch sử, bắt đầu phiên trò chuyện mới từ đầu\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 _Mẹo: Bạn có thể bấm các nút Menu trên bàn phím điện thoại để tra cứu nhanh!_"
    )

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    welcome_text = (
        "👋 *Chào mừng bạn đến với Financial Assistant Bot (Skills Edition)!*\n\n"
        "Bot được trang bị trọn bộ 10 kỹ năng tài chính & xử lý tài liệu chuẩn hóa:\n"
        "• 📈 *Equity Research*: Lập báo cáo phân tích cổ phiếu & định giá chuẩn Wall Street.\n"
        "• 📑 *Financial Statements*: Bóc tách, đánh giá sức khỏe Báo cáo tài chính & đòn bẩy.\n"
        "• 🔍 *Knowledge Synthesis*: Tóm tắt tin tức, tài liệu, trích xuất insight và khử trùng lặp.\n"
        "• ⚡ *Realtime MCP Data*: Kết nối VNStock, TCBS, SSI và Yahoo Finance.\n\n"
        "⏰ *Phát Bản Tin Tự Động (07:30 - 11:30 - 21:30)*: Cập nhật tổng thể Vĩ mô, X VIPs, VNStock, Vàng & Crypto!\n\n"
        "👉 *Cách dùng nhanh*:\n"
        "• Gõ mã cổ phiếu: `HPG`, `VCB`, `SSI`, `FPT` để xem bảng giá và phân tích\n"
        "• `/baocao <mã>` : Xuất báo cáo phân tích toàn diện (Ví dụ: `/baocao HPG`)\n"
        "• `/bctc <mã>` : Phân tích Báo cáo tài chính (Ví dụ: `/bctc VCB`)\n"
        "• `/tomtat <văn bản/link>` : Tóm tắt nhanh thông tin hoặc gửi file trực tiếp\n"
        "• `/reset` : Làm mới phiên chat, bắt đầu cuộc trò chuyện mới từ đầu\n"
        "• Bấm nút *⌨️ Bảng Tra Lệnh* để xem danh sách đầy đủ tất cả các lệnh!"
    )
    await update.message.reply_text(welcome_text, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(get_commands_table_text(), parse_mode="Markdown")

async def reset_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Làm mới phiên chat, xóa toàn bộ bộ nhớ ngữ cảnh"""
    try:
        from agentic_engine import reset_user_session
        msg = reset_user_session(update.effective_chat.id)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi làm mới phiên: {e}")

async def context_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hiển thị trạng thái dung lượng bộ nhớ ngữ cảnh của phiên hiện tại"""
    try:
        from agentic_engine import get_session_summary
        msg = get_session_summary(update.effective_chat.id)
        await update.message.reply_text(msg, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Lỗi kiểm tra bộ nhớ: {e}")

async def vn_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã cổ phiếu. Ví dụ: `/vn HPG`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    try:
        from agentic_engine import update_user_session
        update_user_session(update.effective_chat.id, symbol=symbol)
    except Exception:
        pass
    await send_vnstock_report(update, symbol)

async def global_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã tài sản. Ví dụ: `/global gold`, `/global btc`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    await send_global_report(update, symbol)

async def tcbs_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã cổ phiếu. Ví dụ: `/tcbs HPG`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    try:
        from agentic_engine import update_user_session
        update_user_session(update.effective_chat.id, symbol=symbol)
    except Exception:
        pass
    msg = await update.message.reply_text(f"⏳ Đang tải dữ liệu TCBS cho `{symbol}`...", parse_mode="Markdown")
    report = get_tcbs_analysis(symbol)
    await msg.edit_text(report, parse_mode="Markdown")

async def ssi_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã cổ phiếu. Ví dụ: `/ssi SSI`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    try:
        from agentic_engine import update_user_session
        update_user_session(update.effective_chat.id, symbol=symbol)
    except Exception:
        pass
    msg = await update.message.reply_text(f"⏳ Đang tải dữ liệu SSI cho `{symbol}`...", parse_mode="Markdown")
    report = get_ssi_overview(symbol)
    await msg.edit_text(report, parse_mode="Markdown")

# ==============================================================================
# CÁC HANDLER KỸ NĂNG TÀI CHÍNH (SKILLS INTEGRATION)
# ==============================================================================
async def baocao_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kỹ năng Equity Research: Lập Báo cáo phân tích đầu tư toàn diện"""
    add_subscriber(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã cổ phiếu cần lập báo cáo. Ví dụ: `/baocao HPG` hoặc `/report VCB`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    try:
        from agentic_engine import update_user_session
        update_user_session(update.effective_chat.id, symbol=symbol)
    except Exception:
        pass
    msg = await update.message.reply_text(f"🧠 *Kỹ năng Equity Research đang bóc tách dữ liệu & lập báo cáo cho `{symbol}`...*", parse_mode="Markdown")
    try:
        from agentic_engine import execute_equity_research
        report = execute_equity_research(symbol, LLM_API_KEY)
        if len(report) > 4000:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            await safe_edit_text(msg, chunks[0])
            for c in chunks[1:]:
                await safe_reply_text(update, c)
        else:
            await safe_edit_text(msg, report)
    except Exception as e:
        logger.error(f"Lỗi khi lập báo cáo cổ phiếu {symbol}: {e}")
        await safe_edit_text(msg, f"⚠️ Có lỗi khi tạo báo cáo: {e}")

async def bctc_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kỹ năng Financial Statements: Phân tích chuyên sâu Báo cáo tài chính"""
    add_subscriber(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Vui lòng nhập mã cổ phiếu cần thẩm định BCTC. Ví dụ: `/bctc SSI` hoặc `/statements VCB`", parse_mode="Markdown")
        return
    symbol = context.args[0].upper().strip()
    try:
        from agentic_engine import update_user_session
        update_user_session(update.effective_chat.id, symbol=symbol)
    except Exception:
        pass
    msg = await update.message.reply_text(f"📑 *Kỹ năng Financial Statements đang kiểm tra số liệu BCTC & đòn bẩy `{symbol}`...*", parse_mode="Markdown")
    try:
        from agentic_engine import execute_financial_statements_analysis
        report = execute_financial_statements_analysis(symbol, LLM_API_KEY)
        if len(report) > 4000:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            await safe_edit_text(msg, chunks[0])
            for c in chunks[1:]:
                await safe_reply_text(update, c)
        else:
            await safe_edit_text(msg, report)
    except Exception as e:
        logger.error(f"Lỗi khi thẩm định BCTC {symbol}: {e}")
        await safe_edit_text(msg, f"⚠️ Có lỗi khi phân tích BCTC: {e}")

async def summary_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kỹ năng Summarization & Synthesis: Tóm tắt thông tin tài chính"""
    add_subscriber(update.effective_chat.id)
    if not context.args:
        await update.message.reply_text("Vui lòng nhập nội dung hoặc link cần tóm tắt. Ví dụ:\n`/tomtat Hôm nay khối ngoại mua ròng 1000 tỷ HPG...`\nHoặc gửi trực tiếp file tài liệu vào chat!", parse_mode="Markdown")
        return
    content = " ".join(context.args).strip()
    msg = await update.message.reply_text("🔍 *Kỹ năng Knowledge Synthesis đang phân tích, lọc nhiễu & tóm tắt...*", parse_mode="Markdown")
    try:
        from agentic_engine import execute_summarization
        report = execute_summarization(content, LLM_API_KEY)
        await safe_edit_text(msg, report)
    except Exception as e:
        logger.error(f"Lỗi khi tóm tắt: {e}")
        await safe_edit_text(msg, f"⚠️ Có lỗi khi tóm tắt: {e}")

async def document_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý thông minh khi người dùng gửi file tài liệu (PDF, Excel, Word, Text)"""
    add_subscriber(update.effective_chat.id)
    doc = update.message.document
    if not doc:
        return

    file_name = doc.file_name or "document"
    msg = await update.message.reply_text(f"📥 *Đã nhận tài liệu*: `{file_name}`\n⏳ *Đang tải về và trích xuất dữ liệu...*", parse_mode="Markdown")

    try:
        import tempfile
        file_obj = await context.bot.get_file(doc.file_id)
        suffix = os.path.splitext(file_name)[1].lower()

        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_file:
            temp_path = tmp_file.name

        await file_obj.download_to_drive(temp_path)

        extracted_text = ""
        # Trích xuất dữ liệu tùy theo định dạng file
        if suffix in [".txt", ".csv", ".json", ".md"]:
            with open(temp_path, "r", encoding="utf-8", errors="ignore") as f:
                extracted_text = f.read()[:8000]
        elif suffix == ".pdf":
            try:
                import pypdf
                reader = pypdf.PdfReader(temp_path)
                pages_text = [page.extract_text() or "" for page in reader.pages[:10]]
                extracted_text = "\n".join(pages_text)[:8000]
            except ImportError:
                extracted_text = f"[Tài liệu PDF: {file_name} - Cần cài thư viện pypdf trên môi trường để trích xuất đầy đủ]"
        elif suffix in [".xlsx", ".xls"]:
            try:
                import pandas as pd
                df = pd.read_excel(temp_path)
                extracted_text = df.head(50).to_string()[:8000]
            except Exception:
                extracted_text = f"[Bảng tính Excel: {file_name}]"
        elif suffix == ".docx":
            try:
                import docx
                doc_file = docx.Document(temp_path)
                extracted_text = "\n".join([p.text for p in doc_file.paragraphs[:50]])[:8000]
            except Exception:
                extracted_text = f"[Tài liệu Word: {file_name}]"

        try:
            os.remove(temp_path)
        except Exception:
            pass

        if not extracted_text.strip():
            extracted_text = f"Tài liệu {file_name} gửi từ người dùng."

        await safe_edit_text(msg, f"🧠 *Đang áp dụng bộ kỹ năng phân tích tài liệu cho `{file_name}`...*")
        from agentic_engine import execute_summarization
        result = execute_summarization(f"NỘI DUNG TỪ TÀI LIỆU {file_name}:\n\n{extracted_text}", LLM_API_KEY)

        header = f"📑 *BÁO CÁO PHÂN TÍCH TÀI LIỆU*: `{file_name}`\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        full_res = header + result
        if len(full_res) > 4000:
            chunks = [full_res[i:i+4000] for i in range(0, len(full_res), 4000)]
            await safe_edit_text(msg, chunks[0])
            for c in chunks[1:]:
                await safe_reply_text(update, c)
        else:
            await safe_edit_text(msg, full_res)

    except Exception as e:
        logger.error(f"Lỗi khi xử lý tài liệu {file_name}: {e}")
        await safe_edit_text(msg, f"⚠️ Lỗi khi xử lý file `{file_name}`: {e}")

def translate_tweets_batch(raw_texts: list) -> list:
    """Dịch song ngữ một danh sách tweet sang tiếng Việt bằng Gemini AI trong 1 request duy nhất"""
    if not raw_texts or not LLM_API_KEY:
        return [""] * len(raw_texts)
    
    formatted_input = "\n\n---\n\n".join([f"BÀI {i+1}:\n{t}" for i, t in enumerate(raw_texts)])
    prompt = (
        "Bạn là Chuyên gia Dịch thuật Tài chính & Công nghệ cao cấp. "
        "Hãy dịch các bài đăng trên X (Twitter) dưới đây sang tiếng Việt một cách tự nhiên, mượt mà, đúng thuật ngữ chuyên môn.\n"
        "Định dạng phản hồi BẮT BUỘC theo cấu trúc sau cho từng bài (không thêm bất kỳ lời dẫn nào khác):\n"
        "[BÀI 1]: Bản dịch tiếng Việt bài 1\n"
        "[BÀI 2]: Bản dịch tiếng Việt bài 2\n"
        "[BÀI 3]: Bản dịch tiếng Việt bài 3\n\n"
        f"Danh sách bài đăng cần dịch:\n{formatted_input}"
    )
    try:
        res = ask_llm(prompt)
        translations = []
        for i in range(len(raw_texts)):
            tag = f"[BÀI {i+1}]:"
            next_tag = f"[BÀI {i+2}]:"
            if tag in res:
                part = res.split(tag)[1]
                if next_tag in part:
                    part = part.split(next_tag)[0]
                translations.append(part.strip())
            else:
                translations.append("")
        return translations
    except Exception as ex:
        logger.warning(f"Lỗi dịch batch tweets: {ex}")
        return [""] * len(raw_texts)

async def x_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    if not context.args:
        text = (
            "🌐 *TRA CỨU PHÁT NGÔN X (TWITTER) CỦA CÁC VIP*\n\n"
            "Cú pháp: `/x <username>`\n\n"
            "📌 *Các tài khoản gợi ý:*\n"
            "• `/x elonmusk` - Elon Musk (Tesla, SpaceX, xAI)\n"
            "• `/x realDonaldTrump` - Donald Trump\n"
            "• `/x saylor` - Michael Saylor (MicroStrategy)\n"
            "• `/x cz_binance` - Changpeng Zhao (Binance)\n"
            "• `/x KitcoNewsNOW` - Tin tức vàng Kitco\n"
            "• `/x zerohedge` - ZeroHedge (Vĩ mô & Hàng hóa)\n"
            "• `/x unusual_whales` - Dòng tiền quyền chọn & cá voi\n\n"
            "💡 _Dữ liệu cào realtime và tự động dịch Song ngữ Anh - Việt!_"
        )
        await safe_reply_text(update, text)
        return

    username = context.args[0].replace("@", "").strip()
    msg = await update.message.reply_text(f"⏳ Đang tải phát ngôn mới nhất từ @{username} & dịch song ngữ Anh - Việt...")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"http://127.0.0.1:8890/api/tweets?username={username}&limit=3")
            if resp.status_code == 200:
                data = resp.json()
                tweets = data.get("tweets", [])
                if not tweets:
                    await safe_edit_text(msg, f"Không tìm thấy bài đăng nào gần đây của @{username}.")
                    return

                # Dịch song ngữ hàng loạt qua Gemini AI
                raw_list = [(t.get("rawContent", "") or "").strip() for t in tweets[:3]]
                translations = translate_tweets_batch(raw_list)

                reply_lines = [f"📢 PHÁT NGÔN MỚI NHẤT TỪ @{username} TRÊN X (SONG NGỮ):\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n"]
                for i, t in enumerate(tweets[:3], 1):
                    raw = raw_list[i-1]
                    vi_trans = translations[i-1] if i-1 < len(translations) else ""
                    url = t.get("url", "")
                    likes = t.get("likeCount", 0)
                    rts = t.get("retweetCount", 0)
                    dt = t.get("date", "")
                    dt_clean = dt[:16].replace("T", " ") if dt else ""
                    time_info = f" ({dt_clean})" if dt_clean else ""

                    vi_section = f"🇻🇳 Bản dịch tiếng Việt:\n\"{vi_trans}\"\n\n" if vi_trans else ""

                    reply_lines.append(
                        f"🔹 Bài {i}{time_info}:\n"
                        f"🇺🇸 Nguyên văn:\n\"{raw}\"\n\n"
                        f"{vi_section}"
                        f"👍 {likes:,}  🔄 {rts:,}  🔗 Link: {url}\n"
                        f"──────────────────────────\n"
                    )
                full_reply = "\n".join(reply_lines)
                if len(full_reply) > 4000:
                    chunks = [full_reply[i:i+4000] for i in range(0, len(full_reply), 4000)]
                    await safe_edit_text(msg, chunks[0])
                    for c in chunks[1:]:
                        await safe_reply_text(update, c)
                else:
                    await safe_edit_text(msg, full_reply)
            elif resp.status_code == 400:
                err = resp.json().get("detail", "Chưa cấu hình tài khoản X.")
                await safe_edit_text(msg, f"⚠️ {err}")
            else:
                await safe_edit_text(msg, f"⚠️ Không thể lấy dữ liệu từ X (Mã lỗi {resp.status_code}).")
    except Exception as ex:
        logger.error(f"Lỗi khi tải tweet của {username}: {ex}")
        await safe_edit_text(msg, f"⚠️ Có lỗi khi xử lý phát ngôn của @{username}: {ex}")

async def market_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    msg = await update.message.reply_text("⏳ *Đang kết nối dữ liệu phiên gần nhất & lập Báo Cáo Thị Trường Toàn Diện...*", parse_mode="Markdown")
    try:
        from agentic_engine import generate_comprehensive_market_report
        report = generate_comprehensive_market_report(LLM_API_KEY)
        if len(report) > 4000:
            chunks = [report[i:i+4000] for i in range(0, len(report), 4000)]
            await safe_edit_text(msg, chunks[0])
            for c in chunks[1:]:
                await safe_reply_text(update, c)
        else:
            await safe_edit_text(msg, report)
    except Exception as e:
        logger.error(f"Lỗi khi lập báo cáo thị trường: {e}")
        gold = get_global_quote("GOLD")
        oil = get_global_quote("OIL")
        btc = get_global_quote("BTC")
        sp500 = get_global_quote("SP500")
        usdvnd = get_global_quote("USDVND")
        fallback_text = (
            "🌐 *TỔNG QUAN THỊ TRƯỜNG THẾ GIỚI & VIỆT NAM*\n\n"
            f"• Vàng thế giới: `{gold.get('price', 'N/A')}` USD ({gold.get('pct_change', 0):+}%) \n"
            f"• Dầu Brent: `{oil.get('price', 'N/A')}` USD ({oil.get('pct_change', 0):+}%) \n"
            f"• Tỷ giá USD/VND: `{usdvnd.get('price', 'N/A')}` VND \n"
            f"• S&P 500: `{sp500.get('price', 'N/A')}` ({sp500.get('pct_change', 0):+}%) \n"
            f"• Bitcoin: `{btc.get('price', 'N/A'):,}` USD ({btc.get('pct_change', 0):+}%) \n"
        )
        await safe_edit_text(msg, fallback_text)

async def send_vnstock_report(update: Update, symbol: str):
    msg = await update.message.reply_text(f"⏳ Đang tra cứu cổ phiếu `{symbol}`...", parse_mode="Markdown")
    data = get_vnstock_quote(symbol)

    if not data or data.get("price") == 0:
        # Thử kiểm tra TCBS
        tcbs_text = get_tcbs_analysis(symbol)
        if "Không tìm thấy" not in tcbs_text:
            await msg.edit_text(tcbs_text, parse_mode="Markdown")
            return
        await msg.edit_text(f"❌ Không tìm thấy thông tin cho mã `{symbol}`.", parse_mode="Markdown")
        return

    price = data.get("price", 0)
    change = data.get("change", 0)
    pct = data.get("pct_change", 0)
    vol = data.get("volume", 0)
    ref = data.get("ref", 0)
    ceil = data.get("ceil", 0)
    floor = data.get("floor", 0)

    icon = "🟢" if change > 0 else ("🔴" if change < 0 else "🟡")

    keyboard = [
        [
            InlineKeyboardButton("📊 Phân tích TCBS", callback_data=f"tcbs_{symbol}"),
            InlineKeyboardButton("🏛 Dữ liệu SSI", callback_data=f"ssi_{symbol}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    report = (
        f"{icon} *CỔ PHIẾU {symbol}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• *Giá khớp*: `{price:,.2f}` ({change:+,.2f} | `{pct:+,.2f}%`)\n"
        f"• *Khối lượng*: `{vol:,.0f}` CP\n"
        f"• *Tham chiếu*: `{ref:,.2f}`\n"
        f"• *Trần*: `{ceil:,.2f}` | *Sàn*: `{floor:,.2f}`\n"
        f"• *NN Mua*: `{data.get('foreign_buy', 0):,.0f}` | *NN Bán*: `{data.get('foreign_sell', 0):,.0f}`\n\n"
        f"💡 _Bấm các nút dưới đây để xem phân tích chuyên sâu:_"
    )
    await msg.edit_text(report, parse_mode="Markdown", reply_markup=reply_markup)

async def send_global_report(update: Update, symbol: str):
    msg = await update.message.reply_text(f"⏳ Đang tra cứu tài sản `{symbol}`...", parse_mode="Markdown")
    data = get_global_quote(symbol)
    if not data or "price" not in data:
        await msg.edit_text(f"❌ Không tìm thấy thông tin tài sản toàn cầu `{symbol}`.", parse_mode="Markdown")
        return

    price = data["price"]
    change = data["change"]
    pct = data["pct_change"]
    cur = data["currency"]
    icon = "🟢" if change > 0 else ("🔴" if change < 0 else "🟡")

    report = (
        f"{icon} *TÀI SẢN {data['symbol']}* (`{data['ticker']}`)\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"• *Giá hiện tại*: `{price:,.2f}` {cur}\n"
        f"• *Biến động*: `{change:+,.2f}` ({pct:+,.2f}%)\n"
        f"📅 Cập nhật: _{datetime.now().strftime('%H:%M:%S')}_"
    )
    await msg.edit_text(report, parse_mode="Markdown")

async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("tcbs_"):
        symbol = data[5:]
        report = get_tcbs_analysis(symbol)
        await query.message.reply_text(report, parse_mode="Markdown")
    elif data.startswith("ssi_"):
        symbol = data[4:]
        report = get_ssi_overview(symbol)
        await query.message.reply_text(report, parse_mode="Markdown")

async def safe_edit_text(msg, text: str):
    """Gửi text an toàn, tự động thử Markdown, nếu Telegram lỗi cú pháp entity thì chuyển sang Plain text"""
    try:
        await msg.edit_text(text, parse_mode="Markdown")
        return
    except Exception as e:
        logger.warning(f"Lỗi cú pháp Markdown Telegram ({e}), tự động gửi dạng chuẩn...")
    try:
        await msg.edit_text(text, parse_mode=None)
    except Exception as ex:
        logger.error(f"Lỗi khi gửi safe_edit_text: {ex}")

async def safe_reply_text(update, text: str):
    """Gửi tin nhắn reply an toàn, tự động fallback sang Plain text nếu lỗi Markdown"""
    try:
        await update.message.reply_text(text, parse_mode="Markdown")
        return
    except Exception as e:
        logger.warning(f"Lỗi cú pháp Markdown Telegram ({e}), tự động gửi dạng chuẩn...")
    try:
        await update.message.reply_text(text, parse_mode=None)
    except Exception as ex:
        logger.error(f"Lỗi khi gửi safe_reply_text: {ex}")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    text = update.message.text.strip()

    # Nút bấm menu nhanh
    if text in ["🌅 Bản Tin Sáng", "🌅 Bản Tin Sáng Hôm Nay"]:
        await morning_command_handler(update, context)
        return
    elif text == "📈 Thị trường VN-Index":
        await market_handler(update, context)
        return
    elif text == "📊 Báo cáo cổ phiếu":
        await update.message.reply_text("Nhập lệnh `/baocao <mã>` hoặc gõ tên mã (Ví dụ: `/baocao HPG`, `Phân tích triển vọng VCB`):", parse_mode="Markdown")
        return
    elif text == "📑 Phân tích BCTC":
        await update.message.reply_text("Nhập lệnh `/bctc <mã>` (Ví dụ: `/bctc SSI`, `/bctc VCB`) hoặc gửi trực tiếp file PDF/Excel Báo cáo tài chính vào đây:", parse_mode="Markdown")
        return
    elif text == "🪙 Vàng & Thế giới":
        await send_global_report(update, "GOLD")
        return
    elif text == "🌐 Phát ngôn VIP trên X":
        await x_command_handler(update, context)
        return
    elif text in ["⌨️ Bảng Tra Lệnh", "Bảng tra lệnh", "Danh sách lệnh"]:
        await help_handler(update, context)
        return
    elif text == "💡 Trợ giúp & Hướng dẫn":
        await help_handler(update, context)
        return
    elif text.lower() in ["/reset", "/newchat", "/clear", "reset", "làm mới phiên", "new chat", "xóa lịch sử", "bắt đầu lại"]:
        await reset_command_handler(update, context)
        return
    elif text.lower() in ["/context", "/memory", "bộ nhớ", "ngữ cảnh", "xem bộ nhớ"]:
        await context_command_handler(update, context)
        return

    # Toàn bộ tin nhắn chat tự do được đưa vào Agentic ReAct Engine với 122 công cụ MCP
    from agentic_engine import is_simple_greeting, run_agentic_turn

    # Nếu là lời chào hỏi thông thường: không cần hiển thị thinking, trả lời tức thì
    if is_simple_greeting(text):
        ai_reply = run_agentic_turn(text, LLM_API_KEY, chat_id=update.effective_chat.id)
        await safe_reply_text(update, ai_reply)
        return

    # Đối với tác vụ tài chính: hiển thị trạng thái Agent đang xử lý
    msg = await update.message.reply_text("🧠 *Agent đang suy luận, chắt lọc công cụ và đối chiếu dữ liệu MCP...*", parse_mode="Markdown")
    try:
        ai_reply = run_agentic_turn(text, LLM_API_KEY, chat_id=update.effective_chat.id)
        if ai_reply:
            cleaned_reply = clean_bot_output(ai_reply)
            if len(cleaned_reply) > 4000:
                chunks = [cleaned_reply[i:i+4000] for i in range(0, len(cleaned_reply), 4000)]
                await safe_edit_text(msg, chunks[0])
                for c in chunks[1:]:
                    await safe_reply_text(update, c)
            else:
                await safe_edit_text(msg, cleaned_reply)
        else:
            await safe_edit_text(msg, "⚠️ Không nhận được phản hồi từ hệ thống AI. Vui lòng thử lại.")
    except Exception as e:
        logger.error(f"Lỗi Agentic Engine: {e}")
        await safe_edit_text(msg, f"⚠️ Có lỗi xảy ra trong quá trình Agent suy luận: {str(e)}")


# ==============================================================================
# 7. QUẢN LÝ SUBSCRIBERS & BẢN TIN SÁNG 7H30 (HEARTBEAT)
# ==============================================================================
import pytz
SUBSCRIBERS_FILE = os.path.join(os.path.dirname(__file__), "subscribers.json")
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

def load_subscribers() -> set:
    if not os.path.exists(SUBSCRIBERS_FILE):
        return set()
    try:
        with open(SUBSCRIBERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return set(data)
    except Exception:
        return set()

def save_subscribers(subs: set):
    try:
        with open(SUBSCRIBERS_FILE, "w", encoding="utf-8") as f:
            json.dump(list(subs), f, indent=2)
    except Exception as e:
        logger.error(f"Lỗi khi lưu subscribers: {e}")

def add_subscriber(chat_id: int):
    subs = load_subscribers()
    if chat_id not in subs:
        subs.add(chat_id)
        save_subscribers(subs)
        logger.info(f"Đã thêm subscriber mới: {chat_id}")

def remove_subscriber(chat_id: int):
    subs = load_subscribers()
    if chat_id in subs:
        subs.remove(chat_id)
        save_subscribers(subs)
        logger.info(f"Đã xóa subscriber: {chat_id}")

async def fetch_x_digest_summary() -> str:
    """Lấy danh sách tin tức X đã tích lũy từ X Intel Service (Port 8890)"""
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get("http://127.0.0.1:8890/api/digest?limit=25")
            if resp.status_code == 200:
                feed = resp.json().get("feed", [])
                if not feed:
                    return "• Chưa có bài đăng mới đáng chú ý từ 24 nguồn X."
                lines = []
                for item in feed[:12]:
                    author = item.get("author", item.get("username", ""))
                    cat = item.get("category", "")
                    content = (item.get("content", "") or "").replace("\n", " ")[:150]
                    summary = item.get("summary", "")
                    if summary:
                        lines.append(f"• [{cat}] {author}: {summary} (Trích dẫn: \"{content[:70]}...\")")
                    else:
                        lines.append(f"• [{cat}] {author}: \"{content}\"")
                return "\n".join(lines)
    except Exception as e:
        logger.warning(f"Lỗi fetch_x_digest_summary: {e}")
    return "• Hệ thống đang đồng bộ dữ liệu từ X."

def clean_bot_output(text: str) -> str:
    """Loại bỏ text rác mô phỏng tool, placeholder và khoảng trống thừa"""
    if not text:
        return ""
    text = re.sub(r'\[\s*(?:Đang chờ|Truy xuất|Đang truy xuất|Waiting|Calling tool)[^\]]*\]', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def generate_vn_daily_market_report() -> str:
    """1. Báo cáo Thị trường Chứng khoán Việt Nam phiên gần nhất (Thứ 2 đến Thứ 6 lúc 07:30)"""
    now_vn = datetime.now(VN_TZ)
    date_str = now_vn.strftime("%d/%m/%Y")
    
    # 1. Thử dùng ReAct Engine với 122 MCP tools để thu thập số liệu chuẩn xác
    try:
        from agentic_engine import generate_comprehensive_market_report
        report = generate_comprehensive_market_report(LLM_API_KEY)
        if report and len(report) > 300 and "không thể phản hồi" not in report:
            return clean_bot_output(report)
    except Exception as ex:
        logger.warning(f"Lỗi generate_comprehensive_market_report: {ex}")

    # 2. Thu thập dữ liệu trực tiếp và gọi ask_llm biên tập
    gold = get_global_quote("GOLD")
    oil = get_global_quote("OIL")
    sp500 = get_global_quote("SP500")
    usdvnd = get_global_quote("USDVND")

    key_stocks = ["HPG", "VCB", "TCB", "SSI", "FPT", "VHM", "VIC", "MWG"]
    stock_lines = []
    for s in key_stocks:
        q = get_vnstock_quote(s)
        if q and q.get("price"):
            stock_lines.append(f"• {s}: `{q.get('price', 0):,.2f}` ({q.get('pct_change', 0):+} %)")
        else:
            try:
                tc = get_tcbs_quote_fallback(s)
                if tc and tc.get("price"):
                    stock_lines.append(f"• {s}: `{tc.get('price', 0):,.2f}` (P/E: {tc.get('pe', 'N/A')})")
            except Exception:
                pass
    stock_context = "\n".join(stock_lines) if stock_lines else "• Các cổ phiếu trụ VN30 giao dịch phân hóa tích lũy."

    prompt = f"""
Hãy lập BÁO CÁO PHÂN TÍCH CHIẾN LƯỢC THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM (PHIÊN GẦN NHẤT) ngày {date_str}.
Dữ liệu đầu vào:
- Cổ phiếu trụ cột (VNStock / TCBS):
{stock_context}
- Liên thị trường vĩ mô thế giới (Yahoo Global):
  Vàng thế giới: {gold.get('price', 'N/A')} USD ({gold.get('pct_change', 0):+} %)
  Dầu thô Brent: {oil.get('price', 'N/A')} USD ({oil.get('pct_change', 0):+} %)
  S&P 500: {sp500.get('price', 'N/A')} ({sp500.get('pct_change', 0):+} %)
  Tỷ giá USD/VND: {usdvnd.get('price', 'N/A')} VND

CẤU TRÚC BÁO CÁO BẮT BUỘC (Markdown Telegram rõ ràng, chuyên nghiệp, icon sinh động):
📊 **BÁO CÁO THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM (PHIÊN GẦN NHẤT)**
📅 _Cập nhật: 07:30 sáng ngày {date_str} | Trước giờ mở cửa phiên mới_
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 🏛️ **TỔNG QUAN PHIÊN GIAO DỊCH GẦN NHẤT (VN-INDEX)**:
   - Điểm số đóng cửa, biên độ (+/- điểm, +/- %).
   - Thanh khoản toàn thị trường và độ rộng thị trường.
   
2. 🌊 **DÒNG TIỀN KHỐI NGOẠI & TỰ DOANH**:
   - Xu hướng mua/bán ròng của khối ngoại và top cổ phiếu gom/xả tâm điểm.

3. 🏢 **TÂM ĐIỂM CỔ PHIẾU TRỤ & NHÓM NGÀNH DẪN DẮT**:
   - Diễn biến các nhóm ngành: Ngân hàng, Thép, Bất động sản, Chứng khoán, Công nghệ.

4. 🌍 **BỐI CẢNH VĨ MÔ & TÁC ĐỘNG LIÊN THỊ TRƯỜNG**:
   - Tác động từ Vàng, Dầu thô, Phố Wall và Tỷ giá USD/VND tới tâm lý mở cửa phiên hôm nay.

5. 🧭 **NHẬN ĐỊNH KỸ THUẬT & CHIẾN LƯỢC PHIÊN HÔM NAY**:
   - Vùng Hỗ trợ trọng yếu | Vùng Kháng cự kỳ vọng.
   - Tỷ trọng khuyến nghị Cổ phiếu / Tiền mặt và lưu ý quản trị rủi ro danh mục.
"""
    try:
        report = ask_llm(prompt)
        if report and len(report) > 300:
            return clean_bot_output(report)
    except Exception as ex:
        logger.error(f"Lỗi LLM tạo VN daily report: {ex}")

    return clean_bot_output(
        f"📊 **BÁO CÁO THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM (PHIÊN GẦN NHẤT)**\n"
        f"📅 _Cập nhật: 07:30 sáng ngày {date_str} (Trước giờ mở cửa)_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏛️ **1. TỔNG QUAN PHIÊN GẦN NHẤT**:\n"
        f"• VN-Index vận động cân bằng tích lũy, dòng tiền phân hóa giữa các nhóm ngành cơ bản.\n"
        f"• Thanh khoản khớp lệnh duy trì tương đương mức bình quân 20 phiên.\n\n"
        f"🏢 **2. TÂM ĐIỂM CỔ PHIẾU TRỤ**:\n{stock_context}\n\n"
        f"🌍 **3. LIÊN THỊ TRƯỜNG VĨ MÔ**:\n"
        f"• Vàng: `{gold.get('price', 'N/A')}` USD | Dầu: `{oil.get('price', 'N/A')}` USD | S&P 500: `{sp500.get('price', 'N/A')}` | Tỷ giá: `{usdvnd.get('price', 'N/A')}` VND\n\n"
        f"🧭 **4. CHIẾN LƯỢC HÀNH ĐỘNG**:\n"
        f"• Duy trì tỷ trọng cổ phiếu ở mức an toàn 50-70%, tập trung các mã có tăng trưởng lợi nhuận tốt."
    )

async def generate_vn_weekly_market_report() -> str:
    """2. Báo cáo Tổng kết Tuần Thị trường Chứng khoán Việt Nam (Sáng Thứ 7 lúc 07:30)"""
    now_vn = datetime.now(VN_TZ)
    date_str = now_vn.strftime("%d/%m/%Y")
    
    gold = get_global_quote("GOLD")
    oil = get_global_quote("OIL")
    sp500 = get_global_quote("SP500")
    usdvnd = get_global_quote("USDVND")

    prompt = f"""
Hãy lập BÁO CÁO TỔNG KẾT TOÀN DIỆN TUẦN GIAO DỊCH CHỨNG KHOÁN VIỆT NAM ngày {date_str} (Thứ Bảy).
Dữ liệu tham khảo liên thị trường tuần qua:
- Vàng thế giới: {gold.get('price', 'N/A')} USD ({gold.get('pct_change', 0):+} %)
- Dầu thô Brent: {oil.get('price', 'N/A')} USD ({oil.get('pct_change', 0):+} %)
- S&P 500: {sp500.get('price', 'N/A')} ({sp500.get('pct_change', 0):+} %)
- Tỷ giá USD/VND: {usdvnd.get('price', 'N/A')} VND

CẤU TRÚC BÁO CÁO BẮT BUỘC:
📈 **BÁO CÁO TỔNG KẾT TUẦN GIAO DỊCH CHỨNG KHOÁN VIỆT NAM**
📅 _Cập nhật Thứ Bảy ngày {date_str} | Toàn cảnh tuần qua & Kịch bản tuần tới_
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. 🏛️ **TỔNG KẾT DIỄN BIẾN VN-INDEX CẢ TUẦN**:
   - Mức tăng/giảm điểm số và tỷ lệ % của VN-Index sau 5 phiên giao dịch trong tuần.
   - Đánh giá thanh khoản bình quân toàn thị trường so với tuần trước đó.

2. 🌊 **DÒNG TIỀN KHỐI NGOẠI & TỰ DOANH CẢ TUẦN**:
   - Thống kê tổng giá trị mua/bán ròng của khối ngoại lũy kế cả tuần.
   - Top các cổ phiếu được gom ròng mạnh nhất và bị xả mạnh nhất tuần qua.

3. 🏢 **PHÂN HÓA NHÓM NGÀNH NỔI BẬT TRONG TUẦN**:
   - Nhóm ngành dẫn dắt bứt phá (Ngân hàng, Bất động sản, Thép, Chứng khoán, Công nghệ...).
   - Nhóm ngành chịu áp lực điều chỉnh.

4. 🌍 **BỐI CẢNH VĨ MÔ & THÔNG TIN DOANH NGHIỆP TRỌNG ĐIỂM**:
   - Các sự kiện kinh tế trong nước và thế giới tác động trong tuần.

5. 🧭 **DỰ BÁO XU HƯỚNG & KỊCH BẢN CHIẾN LƯỢC TUẦN TỚI**:
   - Khung dao động kỹ thuật: Vùng Hỗ trợ tuần | Vùng Kháng cự mục tiêu.
   - Chiến lược phân bổ danh mục và khuyến nghị hành động cho tuần giao dịch mới.
"""
    try:
        report = ask_llm(prompt)
        if report and len(report) > 300:
            return clean_bot_output(report)
    except Exception as ex:
        logger.error(f"Lỗi LLM tạo VN weekly report: {ex}")

    return clean_bot_output(
        f"📈 **BÁO CÁO TỔNG KẾT TUẦN GIAO DỊCH CHỨNG KHOÁN VIỆT NAM**\n"
        f"📅 _Cập nhật Thứ Bảy ngày {date_str} (Toàn cảnh tuần qua)_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🏛️ **1. DIỄN BIẾN TOÀN TUẦN**:\n"
        f"• VN-Index khép lại tuần giao dịch với nỗ lực cân bằng tích lũy quanh vùng hỗ trợ then chốt.\n"
        f"• Thanh khoản duy trì ở mức ổn định, áp lực bán có dấu hiệu suy giảm về cuối tuần.\n\n"
        f"🌊 **2. DÒNG TIỀN KHỐI NGOẠI**:\n"
        f"• Khối ngoại tiếp tục xu hướng cơ cấu danh mục, tập trung gom chọn lọc nhóm cổ phiếu đầu ngành.\n\n"
        f"🧭 **3. KỊCH BẢN TUẦN TỚI**:\n"
        f"• Khuyến nghị nhà đầu tư kiểm soát tỷ trọng an toàn, ưu tiên nắm giữ cổ phiếu có triển vọng kinh doanh quý khởi sắc."
    )

async def generate_x_global_intel_report(session_type: str = "morning") -> str:
    """3. Báo cáo Tóm tắt Tin tức X (Twitter) & Global Finance (07:30, 11:30, 21:30 hàng ngày)"""
    now_vn = datetime.now(VN_TZ)
    date_str = now_vn.strftime("%d/%m/%Y")
    time_str = now_vn.strftime("%H:%M")

    if session_type == "morning":
        title = f"🌐 ĐIỂM TIN NÓNG X (TWITTER) & TÀI CHÍNH TOÀN CẦU (07:30 SÁNG - {date_str})"
        focus = "Tâm điểm: Phát ngôn VIPs qua đêm, Vĩ mô quốc tế, Vàng & Crypto trước giờ mở cửa"
    elif session_type == "midday":
        title = f"🌐 TỔNG HỢP TIN TỨC X (TWITTER) & VĨ MÔ QUỐC TẾ (11:30 TRƯA - {date_str})"
        focus = "Tâm điểm: Diễn biến tin tức nóng giữa ngày, biến động Tỷ giá, Vàng & Thị trường châu Á"
    else:
        title = f"🌐 TOÀN CẢNH TIN NÓNG X (TWITTER) & THỊ TRƯỜNG THẾ GIỚI (21:30 TỐI - {date_str})"
        focus = "Tâm điểm: Tin tức trước giờ mở phiên Mỹ, Động thái Fed, Phát ngôn VIPs & Dòng tiền Crypto"

    gold = get_global_quote("GOLD")
    oil = get_global_quote("OIL")
    sp500 = get_global_quote("SP500")
    usdvnd = get_global_quote("USDVND")
    btc = get_global_quote("BTC")
    
    x_news = await fetch_x_digest_summary()

    prompt = f"""
Bạn là Giám đốc Nghiên cứu Chiến lược Toàn cầu (Global Macro & Intelligence Strategist).
Hãy tổng hợp một BẢN TIN TÌNH BÁO MẠNG XÃ HỘI X (TWITTER) VÀ TÀI CHÍNH TOÀN CẦU để gửi tới người dùng Telegram.

TIÊU ĐỀ: {title}
ĐỊNH HƯỚNG PHIÊN: {focus}
THỜI GIAN: {time_str} ngày {date_str}

DỮ LIỆU ĐẦU VÀO ĐƯỢC CUNG CẤP:
1. DỮ LIỆU TÀI SẢN TOÀN CẦU (Yahoo Global Finance):
- Vàng thế giới: {gold.get('price', 'N/A')} USD ({gold.get('pct_change', 0):+} %)
- Dầu thô Brent: {oil.get('price', 'N/A')} USD ({oil.get('pct_change', 0):+} %)
- Chỉ số S&P 500: {sp500.get('price', 'N/A')} ({sp500.get('pct_change', 0):+} %)
- Tỷ giá USD/VND: {usdvnd.get('price', 'N/A')} VND
- Bitcoin: {btc.get('price', 'N/A')} USD

2. TIN TỨC NÓNG TỪ CÁC NGUỒN FLOW TRÊN X (TWITTER):
{x_news}

BỘ QUY TẮC BẮT BUỘC KHÓA CHẶT ZERO-HALLUCINATION:
1. TRUNG THỰC 100% VỚI DỮ LIỆU ĐẦU VÀO:
   - Chỉ được tóm tắt và phân tích đúng 100% nội dung thực tế có trong danh sách bài tweet và số liệu tài sản được cung cấp ở trên.
   - CẤM TUYỆT ĐỐI việc tự suy diễn ngày tháng, tỷ lệ phần trăm, mốc thời gian tương lai, hoặc các chính sách lãi suất/họp Fed không có trong dữ liệu đầu vào.
2. XỬ LÝ KHI THIẾU THÔNG TIN (QUY ĐỊNH RÕ):
   - Nếu trong danh sách tweet KHÔNG CÓ thông tin mới về Fed, Jerome Powell, hay chính sách tiền tệ: BẮT BUỘC ghi rõ 'Chưa có thông tin hoặc phát ngôn mới về Fed trong phiên' chứ CẤM TUYỆT ĐỐI không được tự bịa ngày họp hoặc phỏng đoán lấp vào chỗ trống.
   - Nếu không có tweet về cá voi Crypto hay quỹ SPDR Gold: Ghi rõ 'Dòng tiền duy trì ổn định, chưa ghi nhận giao dịch đột biến từ các quỹ/cá voi'.
3. CẤM SINH TEXT MÔ PHỎNG TOOL:
   - CẤM in ra bất kỳ dòng chữ nào dạng placeholder '[Đang chờ truy xuất dữ liệu...]' hoặc '[Loading...]'.
4. CẤU TRÚC 4 PHẦN CHUẨN MỰC:
   🌐 PHẦN 1: TÂM ĐIỂM TIN NÓNG X & PHÁT NGÔN VIPS (Donald Trump, Elon Musk, Fed/Kobeissi, Whale Alert, ZeroHedge...)
   🪙 PHẦN 2: THỊ TRƯỜNG VÀNG & QUỸ SPDR GOLD TRUST (Tổng hợp biến động vàng, Kitco, xu hướng dòng tiền)
   🚀 PHẦN 3: DÒNG TIỀN CRYPTO & TÀI SẢN TOÀN CẦU (Bitcoin, cá voi crypto, dầu thô, S&P 500, tỷ giá)
   💡 PHẦN 4: ĐÁNH GIÁ TÁC ĐỘNG TÂM LÝ & RỦI RO THỊ TRƯỜNG
"""
    try:
        report = ask_llm(prompt)
        if report and len(report) > 300:
            header = f"*{title}*\n_{focus}_\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            return clean_bot_output(header + report.strip())
    except Exception as ex:
        logger.error(f"Lỗi LLM tạo X report: {ex}")

    return clean_bot_output(
        f"*{title}*\n"
        f"_{focus}_\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📡 **I. TIN NÓNG TỪ CÁC NGUỒN FLOW TRÊN X**:\n{x_news}\n\n"
        f"🌍 **II. TÀI SẢN TOÀN CẦU & VĨ MÔ**:\n"
        f"• Vàng thế giới: `{gold.get('price', 'N/A')}` USD ({gold.get('pct_change', 0):+} %)\n"
        f"• Dầu Brent: `{oil.get('price', 'N/A')}` USD ({oil.get('pct_change', 0):+} %)\n"
        f"• S&P 500: `{sp500.get('price', 'N/A')}` ({sp500.get('pct_change', 0):+} %)\n"
        f"• Tỷ giá USD/VND: `{usdvnd.get('price', 'N/A')}` VND\n"
        f"• Bitcoin: `{btc.get('price', 'N/A')}` USD\n\n"
        f"💡 **III. TỔNG KẾT TÁC ĐỘNG**:\n"
        f"• Theo dõi sát sao biến động tỷ giá và địa chính trị quốc tế tác động lên dòng tiền."
    )

async def send_report_to_all_subscribers(app: Application, report_text: str, report_name: str = "Bản tin"):
    """Phát bản tin tới toàn bộ danh sách đăng ký, tự động chia nhỏ chunk nếu dài"""
    subs = load_subscribers()
    if not subs:
        logger.info(f"Chưa có subscriber nào để gửi ({report_name}).")
        return
    cleaned = clean_bot_output(report_text)
    logger.info(f"Đang phát {report_name} tới {len(subs)} người dùng...")
    for chat_id in list(subs):
        try:
            if len(cleaned) > 4000:
                chunks = [cleaned[i:i+4000] for i in range(0, len(cleaned), 4000)]
                for c in chunks:
                    try:
                        await app.bot.send_message(chat_id=chat_id, text=c, parse_mode="Markdown")
                    except Exception:
                        await app.bot.send_message(chat_id=chat_id, text=c, parse_mode=None)
                    await asyncio.sleep(0.1)
            else:
                try:
                    await app.bot.send_message(chat_id=chat_id, text=cleaned, parse_mode="Markdown")
                except Exception:
                    await app.bot.send_message(chat_id=chat_id, text=cleaned, parse_mode=None)
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"Lỗi khi gửi {report_name} tới {chat_id}: {e}")

async def heartbeat_loop(app: Application):
    """
    Vòng lặp Heartbeat nền kiểm tra mỗi 20s:
    1. Đúng 07:30 Sáng:
       - Thứ 2 đến Thứ 6: Báo cáo CK VN phiên gần nhất + Báo cáo X & Global Finance sáng.
       - Thứ 7: Báo cáo Tổng kết Tuần CK VN + Báo cáo X & Global Finance sáng.
       - Chủ Nhật: Chỉ gửi Báo cáo X & Global Finance sáng (CK VN nghỉ).
    2. Đúng 11:30 Trưa (Tất cả 7 ngày): Báo cáo X & Global Finance trưa.
    3. Đúng 21:30 Tối (Tất cả 7 ngày): Báo cáo X & Global Finance tối.
    """
    logger.info("Heartbeat Task đã chạy! Hẹn giờ chuẩn: 07:30 (CK VN + X), 11:30 (X), 21:30 (X)...")
    sent_slots = set()

    while True:
        try:
            now_vn = datetime.now(VN_TZ)
            today_str = now_vn.strftime("%Y-%m-%d")
            hour = now_vn.hour
            minute = now_vn.minute
            weekday = now_vn.weekday()  # 0: Thứ 2 ... 4: Thứ 6, 5: Thứ 7, 6: Chủ Nhật

            # Dọn dẹp cache slot cũ khi sang ngày mới
            current_day_prefix = f"{today_str}_"
            sent_slots = {s for s in sent_slots if s.startswith(current_day_prefix)}

            # ------------------------------------------------------------------
            # 1. KHUNG 07:30 SÁNG
            # ------------------------------------------------------------------
            slot_0730 = f"{today_str}_07_30"
            if hour == 7 and minute == 30 and slot_0730 not in sent_slots:
                sent_slots.add(slot_0730)
                logger.info(f"⏰ KÍCH HOẠT HEARTBEAT 07:30 SÁNG ({today_str}) [Weekday: {weekday}]")
                
                # A. Báo cáo Chứng khoán Việt Nam:
                if 0 <= weekday <= 4:
                    # Thứ 2 đến Thứ 6: Báo cáo CK VN phiên gần nhất
                    logger.info("-> Đang tạo & gửi Báo cáo CK VN phiên gần nhất...")
                    vn_report = await generate_vn_daily_market_report()
                    await send_report_to_all_subscribers(app, vn_report, "Báo cáo CK VN phiên gần nhất")
                    await asyncio.sleep(2)
                elif weekday == 5:
                    # Thứ 7: Báo cáo Tổng kết Tuần CK VN
                    logger.info("-> Đang tạo & gửi Báo cáo CK VN Tổng kết Tuần...")
                    vn_weekly = await generate_vn_weekly_market_report()
                    await send_report_to_all_subscribers(app, vn_weekly, "Báo cáo CK VN Tổng kết Tuần")
                    await asyncio.sleep(2)
                else:
                    # Chủ Nhật: Nghỉ CK VN
                    logger.info("-> Chủ Nhật: Nghỉ giao dịch CK VN, không phát báo cáo CK VN.")

                # B. Báo cáo Tóm tắt Tin tức X (Twitter) & Global Finance Sáng (tất cả các ngày T2 - CN):
                logger.info("-> Đang tạo & gửi Báo cáo X & Global Finance Sáng 07:30...")
                x_morning = await generate_x_global_intel_report("morning")
                await send_report_to_all_subscribers(app, x_morning, "Bản tin X & Global Finance Sáng 07:30")

            # ------------------------------------------------------------------
            # 2. KHUNG 11:30 TRƯA (TẤT CẢ CÁC NGÀY TRONG TUẦN)
            # ------------------------------------------------------------------
            slot_1130 = f"{today_str}_11_30"
            if hour == 11 and minute == 30 and slot_1130 not in sent_slots:
                sent_slots.add(slot_1130)
                logger.info(f"⏰ KÍCH HOẠT HEARTBEAT 11:30 TRƯA ({today_str})")
                x_midday = await generate_x_global_intel_report("midday")
                await send_report_to_all_subscribers(app, x_midday, "Bản tin X & Global Finance Trưa 11:30")

            # ------------------------------------------------------------------
            # 3. KHUNG 21:30 TỐI (TẤT CẢ CÁC NGÀY TRONG TUẦN)
            # ------------------------------------------------------------------
            slot_2130 = f"{today_str}_21_30"
            if hour == 21 and minute == 30 and slot_2130 not in sent_slots:
                sent_slots.add(slot_2130)
                logger.info(f"⏰ KÍCH HOẠT HEARTBEAT 21:30 TỐI ({today_str})")
                x_evening = await generate_x_global_intel_report("evening")
                await send_report_to_all_subscribers(app, x_evening, "Bản tin X & Global Finance Tối 21:30")

        except Exception as e:
            logger.error(f"Lỗi trong Heartbeat loop: {e}")

        await asyncio.sleep(20)

async def morning_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý nút bấm 🌅 Bản Tin Sáng hoặc lệnh /morning"""
    add_subscriber(update.effective_chat.id)
    now_vn = datetime.now(VN_TZ)
    weekday = now_vn.weekday()

    msg = await update.message.reply_text("⏳ *Đang tổng hợp Bản Tin Thị Trường theo đúng lịch trình...*", parse_mode="Markdown")
    try:
        if 0 <= weekday <= 4:
            vn_report = await generate_vn_daily_market_report()
            await safe_edit_text(msg, vn_report)
        elif weekday == 5:
            vn_report = await generate_vn_weekly_market_report()
            await safe_edit_text(msg, vn_report)
        else:
            await safe_edit_text(msg, "🏖️ **Hôm nay là Chủ Nhật**: Thị trường Chứng khoán Việt Nam nghỉ giao dịch cuối tuần.")
        
        # Gửi tiếp Bản tin X sáng
        x_report = await generate_x_global_intel_report("morning")
        await safe_reply_text(update, x_report)
    except Exception as e:
        logger.error(f"Lỗi morning_command_handler: {e}")
        await safe_edit_text(msg, f"⚠️ Lỗi khi tải bản tin sáng: {e}")

async def subscribe_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_subscriber(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Đăng ký thành công!*\n"
        "Bạn sẽ tự động nhận Bản Tin Sáng Tổng Hợp Vĩ Mô & Cổ Phiếu vào lúc *7h30 sáng* hàng ngày.",
        parse_mode="Markdown"
    )

async def unsubscribe_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    remove_subscriber(update.effective_chat.id)
    await update.message.reply_text(
        "🔕 *Đã hủy nhận bản tin sáng tự động.* Bạn vẫn có thể gõ `/morning` bất kỳ lúc nào để xem báo cáo.",
        parse_mode="Markdown"
    )


# ==============================================================================
# 9. KHỞI CHẠY BOT & HEARTBEAT
# ==============================================================================
async def post_init_callback(application: Application):
    # Khởi chạy background task Heartbeat
    asyncio.create_task(heartbeat_loop(application))

def main():
    if not TELEGRAM_TOKEN or TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        logger.error("CHƯA CÓ TELEGRAM_BOT_TOKEN! Vui lòng điền token vào file .env.")
        print("\n" + "=" * 65)
        print(" [!] THIẾU TELEGRAM_BOT_TOKEN TRONG FILE .env")
        print(" Vui lòng mở file .env trên VPS và điền token nhận được từ @BotFather.")
        print("=" * 65 + "\n")
        return

    logger.info("Khởi động Financial Telegram Bot (Long Polling + 7h30 Heartbeat)...")
    from telegram.request import HTTPXRequest
    req = HTTPXRequest(
        connection_pool_size=8,
        connect_timeout=30.0,
        read_timeout=30.0,
        write_timeout=30.0
    )
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .request(req)
        .post_init(post_init_callback)
        .build()
    )

    # Đăng ký các lệnh
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler(["help", "commands", "lenh", "menu"], help_handler))
    app.add_handler(CommandHandler("vn", vn_command_handler))
    app.add_handler(CommandHandler("global", global_command_handler))
    app.add_handler(CommandHandler("tcbs", tcbs_command_handler))
    app.add_handler(CommandHandler("ssi", ssi_command_handler))
    app.add_handler(CommandHandler(["baocao", "report"], baocao_command_handler))
    app.add_handler(CommandHandler(["bctc", "statements"], bctc_command_handler))
    app.add_handler(CommandHandler(["tomtat", "summary"], summary_command_handler))
    app.add_handler(CommandHandler(["reset", "newchat", "clear"], reset_command_handler))
    app.add_handler(CommandHandler(["context", "memory", "session"], context_command_handler))
    app.add_handler(CommandHandler("market", market_handler))
    app.add_handler(CommandHandler("morning", morning_command_handler))
    app.add_handler(CommandHandler("daily", morning_command_handler))
    app.add_handler(CommandHandler("x", x_command_handler))
    app.add_handler(CommandHandler("twitter", x_command_handler))
    app.add_handler(CommandHandler("subscribe", subscribe_command_handler))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe_command_handler))

    # Đăng ký callback và tin nhắn
    app.add_handler(CallbackQueryHandler(callback_query_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, document_message_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message_handler))

    print("\n✅ FINANCIAL TELEGRAM BOT ĐANG CHẠY NỀN TRÊN VPS (HEARTBEAT 7H30 SẴN SÀNG)...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()


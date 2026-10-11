import os
import sys
import json
import asyncio
import logging
import re
import urllib.request
import urllib.parse
from datetime import datetime
import pytz
import nest_asyncio

nest_asyncio.apply()

logger = logging.getLogger("AgenticEngine")
VN_TZ = pytz.timezone("Asia/Ho_Chi_Minh")

sys.path.insert(0, '/home/ubuntu/mcp-finance')
sys.path.insert(0, '/home/ubuntu/vnstock-mcp')
sys.path.insert(0, '/home/ubuntu/global-finance-mcp')

import tcbs_mcp_bridge
import ssi_mcp_bridge

# Global tool mapping
TOOL_DISPATCH = {}
GEMINI_FUNCTION_DECLARATIONS = []

# Danh sách fallback model tối ưu:
# gemini-3-flash-preview: Model 3.x Flash mới nhất, cực nhạy với function calling và không bị hạn chế TPM
DEFAULT_MODELS_CHAIN = [
    "gemini-3-flash-preview",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash"
]

def clean_schema_type(t):
    if not t:
        return "STRING"
    t_str = str(t).upper()
    if "INT" in t_str:
        return "INTEGER"
    if "NUM" in t_str or "FLOAT" in t_str:
        return "NUMBER"
    if "BOOL" in t_str:
        return "BOOLEAN"
    if "ARRAY" in t_str or "LIST" in t_str:
        return "ARRAY"
    if "OBJ" in t_str or "DICT" in t_str:
        return "OBJECT"
    return "STRING"

def convert_schema_to_gemini(schema):
    """Chuyển đổi JSON Schema của MCP thành Schema tương thích Gemini OpenAPI"""
    if not isinstance(schema, dict):
        return {"type": "OBJECT", "properties": {}}

    props = schema.get("properties", {})
    gemini_props = {}
    for p_name, p_info in props.items():
        if isinstance(p_info, dict):
            p_type = clean_schema_type(p_info.get("type", "string"))
            gemini_props[p_name] = {
                "type": p_type,
                "description": p_info.get("description", p_name)
            }
        else:
            gemini_props[p_name] = {"type": "STRING", "description": p_name}

    res = {
        "type": "OBJECT",
        "properties": gemini_props
    }
    if "required" in schema and isinstance(schema["required"], list):
        res["required"] = [r for r in schema["required"] if r in gemini_props]
    return res

def build_all_mcp_tools():
    """Quét và gom toàn bộ 122 công cụ từ 4 MCP vào danh mục chung"""
    global TOOL_DISPATCH, GEMINI_FUNCTION_DECLARATIONS
    TOOL_DISPATCH.clear()
    GEMINI_FUNCTION_DECLARATIONS.clear()

    # 1. TCBS MCP Tools (54 tools)
    token = tcbs_mcp_bridge.get_valid_access_token()
    if token:
        try:
            res = tcbs_mcp_bridge.list_tcbs_tools(token)
            for t in res.get('result', {}).get('tools', []):
                raw_name = t['name']
                clean_name = "tcbs_" + raw_name.replace("-", "_").replace(".", "_")
                TOOL_DISPATCH[clean_name] = {
                    "source": "tcbs",
                    "raw_name": raw_name,
                    "handler": lambda args, rn=raw_name: tcbs_mcp_bridge.call_tcbs_tool(rn, args, tcbs_mcp_bridge.get_valid_access_token())
                }
                param_schema = convert_schema_to_gemini(t.get("inputSchema", {}))
                GEMINI_FUNCTION_DECLARATIONS.append({
                    "name": clean_name,
                    "description": f"[TCBS TCInvest] {t.get('description', '')[:300]}",
                    "parameters": param_schema
                })
        except Exception as e:
            logger.error(f"Lỗi khi nạp TCBS tools: {e}")

    # 2. SSI MCP Tools (42 tools)
    ssi_token = ssi_mcp_bridge.get_valid_access_token()
    if ssi_token:
        try:
            res = ssi_mcp_bridge.list_ssi_tools(ssi_token)
            for t in res.get('result', {}).get('tools', []):
                raw_name = t['name']
                clean_name = "ssi_" + raw_name.replace("-", "_").replace(".", "_")
                TOOL_DISPATCH[clean_name] = {
                    "source": "ssi",
                    "raw_name": raw_name,
                    "handler": lambda args, rn=raw_name: ssi_mcp_bridge.call_ssi_tool(rn, args, ssi_mcp_bridge.get_valid_access_token())
                }
                param_schema = convert_schema_to_gemini(t.get("inputSchema", {}))
                GEMINI_FUNCTION_DECLARATIONS.append({
                    "name": clean_name,
                    "description": f"[SSI Securities] {t.get('description', '')[:300]}",
                    "parameters": param_schema
                })
        except Exception as e:
            logger.error(f"Lỗi khi nạp SSI tools: {e}")

    # 3. Global Finance MCP Tools (5 tools)
    try:
        from global_finance_server import mcp as gf_mcp
        gf_tools = asyncio.run(gf_mcp.list_tools())
        for t in gf_tools:
            raw_name = t.name
            clean_name = "global_" + raw_name.replace("-", "_").replace(".", "_")
            TOOL_DISPATCH[clean_name] = {
                "source": "global_finance",
                "raw_name": raw_name,
                "handler": lambda args, rn=raw_name, gm=gf_mcp: asyncio.run(gm.call_tool(rn, args))
            }
            param_schema = convert_schema_to_gemini(t.parameters if hasattr(t, 'parameters') else {})
            GEMINI_FUNCTION_DECLARATIONS.append({
                "name": clean_name,
                "description": f"[Yahoo Global Finance] {t.description[:300] if t.description else ''}",
                "parameters": param_schema
            })
    except Exception as e:
        logger.error(f"Lỗi khi nạp Global Finance tools: {e}")

    # 4. VNStock MCP Tools (21 tools)
    try:
        from run_vnstock_mcp import mcp as vn_mcp
        vn_tools = asyncio.run(vn_mcp.list_tools())
        for t in vn_tools:
            raw_name = t.name
            clean_name = "vnstock_" + raw_name.replace("-", "_").replace(".", "_")
            TOOL_DISPATCH[clean_name] = {
                "source": "vnstock",
                "raw_name": raw_name,
                "handler": lambda args, rn=raw_name, vm=vn_mcp: asyncio.run(vm.call_tool(rn, args))
            }
            param_schema = convert_schema_to_gemini(t.parameters if hasattr(t, 'parameters') else {})
            GEMINI_FUNCTION_DECLARATIONS.append({
                "name": clean_name,
                "description": f"[VNStock Realtime] {t.description[:300] if t.description else ''}",
                "parameters": param_schema
            })
    except Exception as e:
        logger.error(f"Lỗi khi nạp VNStock tools: {e}")

    # 5. X Intel MCP Tools (Port 8890 - Realtime Twitter/X Feed)
    try:
        def fetch_x_tweets(args):
            username = args.get("username", "").replace("@", "").strip()
            limit = int(args.get("limit", 5))
            url = f"http://127.0.0.1:8890/api/tweets?username={username}&limit={limit}"
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "AgenticEngine/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read().decode("utf-8")
            except Exception as ex:
                return json.dumps({"status": "error", "message": str(ex)}, ensure_ascii=False)

        def fetch_x_hot_digest(args):
            limit = int(args.get("limit", 15))
            url = f"http://127.0.0.1:8890/api/digest?limit={limit}"
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "AgenticEngine/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.read().decode("utf-8")
            except Exception as ex:
                return json.dumps({"status": "error", "message": str(ex)}, ensure_ascii=False)

        TOOL_DISPATCH["x_get_latest_tweets"] = {
            "source": "x_intel",
            "raw_name": "x_get_latest_tweets",
            "handler": fetch_x_tweets
        }
        GEMINI_FUNCTION_DECLARATIONS.append({
            "name": "x_get_latest_tweets",
            "description": "[X/Twitter Realtime] Tra cứu phát ngôn, tweet mới nhất theo username của các VIP (Ví dụ: realDonaldTrump, elonmusk, saylor, cz_binance, KitcoNewsNOW, zerohedge, unusual_whales).",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "username": {"type": "STRING", "description": "Username tài khoản X (không cần dấu @)"},
                    "limit": {"type": "INTEGER", "description": "Số lượng tweet cần lấy (mặc định 5)"}
                },
                "required": ["username"]
            }
        })

        TOOL_DISPATCH["x_get_hot_feed"] = {
            "source": "x_intel",
            "raw_name": "x_get_hot_feed",
            "handler": fetch_x_hot_digest
        }
        GEMINI_FUNCTION_DECLARATIONS.append({
            "name": "x_get_hot_feed",
            "description": "[X/Twitter Realtime] Lấy dòng tin tức nóng tổng hợp từ 24 nguồn tin uy tín (Địa chính trị, Kinh tế vĩ mô, Vàng/SPDR Gold, VIP Crypto, Báo chí Việt Nam). Dùng khi người dùng hỏi tin nóng, tổng hợp tweet mới nhất hoặc tình hình thế giới.",
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "limit": {"type": "INTEGER", "description": "Số lượng tin cần lấy (mặc định 15)"}
                }
            }
        })
    except Exception as e:
        logger.error(f"Lỗi khi nạp X Intel tools: {e}")

    logger.info(f"Đã nạp thành công {len(GEMINI_FUNCTION_DECLARATIONS)} công cụ MCP vào Agent Engine!")

def execute_tool(func_name, args):
    """Thực thi công cụ trên MCP tương ứng"""
    if func_name not in TOOL_DISPATCH:
        return f"Công cụ {func_name} không tồn tại trong danh mục."
    handler_info = TOOL_DISPATCH[func_name]
    handler = handler_info["handler"]
    logger.info(f"-> [THỰC THI TOOL MCP]: {func_name} ({handler_info['source']}) | Tham số: {args}")
    try:
        res = handler(args)
        if hasattr(res, "content"):
            texts = [c.text for c in res.content if hasattr(c, "text")]
            return "\n".join(texts)
        if isinstance(res, dict):
            if "result" in res:
                content = res["result"].get("content", [])
                if isinstance(content, list) and content and "text" in content[0]:
                    return content[0]["text"]
                return json.dumps(res["result"], ensure_ascii=False)
            return json.dumps(res, ensure_ascii=False)
        return str(res)
    except Exception as e:
        logger.error(f"Lỗi khi thực thi {func_name}: {e}")
        return f"Lỗi thực thi công cụ: {str(e)}"

def is_simple_greeting(query: str) -> bool:
    """Nhận diện nhanh các lời chào hoặc câu hỏi giao tiếp thông thường để phản hồi tức thì"""
    q = query.strip().lower()
    greetings = [
        "alo", "alo bot", "alobot", "hi", "hello", "chào", "chào bạn", "chào bot",
        "hey", "helo", "có ai không", "bạn là ai", "bot là ai", "bạn tên gì",
        "start", "/start", "help", "/help", "menu"
    ]
    if q in greetings or re.match(r"^(alo|hi|hello|chào)\b", q):
        return True
    return False

def get_agent_system_prompt():
    now_vn = datetime.now(VN_TZ)
    weekday_map = {0: "Thứ Hai", 1: "Thứ Ba", 2: "Thứ Tư", 3: "Thứ Năm", 4: "Thứ Sáu", 5: "Thứ Bảy", 6: "Chủ Nhật"}
    weekday_str = weekday_map[now_vn.weekday()]
    date_str = now_vn.strftime("%d/%m/%Y")
    time_str = now_vn.strftime("%H:%M:%S")

    return (
        f"THỜI GIAN THỰC TẾ HỆ THỐNG: {weekday_str}, ngày {date_str} (Giờ Việt Nam: {time_str}).\n\n"
        "VAI TRÒ & NĂNG LỰC:\n"
        "Bạn là Chuyên gia Tư vấn Tài chính & Chiến lược Đầu tư Cấp cao. Bạn được kết nối trực tiếp với hơn 120 công cụ MCP từ 5 nguồn dữ liệu lớn:\n"
        "- X/Twitter Realtime (x_*): Tra cứu phát ngôn VIP (Trump, Elon Musk, Saylor, CZ, FED, ZeroHedge...) và dòng tin tức nóng 24 nguồn tinh hoa từ x-intel-mcp (Port 8890).\n"
        "- TCBS TCInvest (tcbs_*): 54 công cụ phân tích tài chính chuyên sâu, RS Rating, P/E, P/B, ROE, sức khỏe tài chính doanh nghiệp.\n"
        "- SSI Securities (ssi_*): 42 công cụ dữ liệu thị trường, chỉ số VN-Index, VN30, ngành dẫn dắt, dòng tiền, thanh khoản.\n"
        "- VNStock Realtime (vnstock_*): 21 công cụ khớp lệnh, sổ lệnh depth, giao dịch tự doanh và nhà đầu tư nước ngoài.\n"
        "CƠ CHẾ PHÁT BẢN TIN TỰ ĐỘNG CỦA HỆ THỐNG:\n"
        "1. KHUNG 07:30 SÁNG:\n"
        "   - Thứ 2 đến Thứ 6: Tự động phát 'Báo cáo Tổng hợp Thị trường Chứng khoán Việt Nam phiên gần nhất' + 'Bản tin X & Global Finance sáng'.\n"
        "   - Thứ 7: Tự động phát 'Báo cáo Tổng kết Tuần Thị trường Chứng khoán Việt Nam' + 'Bản tin X & Global Finance sáng'.\n"
        "   - Chủ Nhật: Thị trường CK VN nghỉ giao dịch cuối tuần; hệ thống chỉ phát 'Bản tin X & Global Finance sáng'.\n"
        "2. KHUNG 11:30 TRƯA (Tất cả 7 ngày trong tuần): Tự động phát 'Bản tin Tóm tắt Tin tức X (Twitter) & Vĩ mô Quốc tế giữa ngày'.\n"
        "3. KHUNG 21:30 TỐI (Tất cả 7 ngày trong tuần): Tự động phát 'Bản tin Toàn cảnh Tin tức Nóng X & Thị trường Thế giới trước phiên Mỹ'.\n"
        "4. BẤT KỲ LÚC NÀO: Người dùng có thể yêu cầu xem báo cáo tức thì qua nút '🌅 Bản Tin Sáng' hoặc các lệnh /market, /baocao, /x.\n"
        "5. KHI NGƯỜI DÙNG HỎI VỀ X / TWITTER / PHÁT NGÔN / TIN NÓNG: BẮT BUỘC gọi công cụ x_get_hot_feed hoặc x_get_latest_tweets để trích xuất dữ liệu thực tế từ cổng 8890. TUYỆT ĐỐI KHÔNG NÓI 'tôi không có quyền truy cập X'.\n\n"
        "====================================================================================\n"
        "BỘ QUY TẮC BẮT BUỘC VỀ TÍNH CHÍNH XÁC VÀ TRUNG THỰC SỐ LIỆU (ZERO-HALLUCINATION RULES):\n"
        "1. TUYỆT ĐỐI KHÔNG BỊA ĐẶT SỐ LIỆU & KHÔNG IN TEXT MÔ PHỎNG TOOL:\n"
        "   - CẤM in ra các dòng chữ dạng '[Đang chờ truy xuất dữ liệu từ công cụ...]' vào khung chat. Nếu cần dữ liệu, hãy GỌI FUNCTION CALL thực tế của công cụ.\n"
        "   - Mọi con số (giá cổ phiếu, điểm số chỉ số, tỷ lệ % tăng/giảm, P/E, P/B, ROE, khối lượng khớp lệnh, giá trị mua/bán ròng) BẮT BUỘC trích xuất 100% từ kết quả trả về của các công cụ MCP.\n"
        "   - TUYỆT ĐỐI KHÔNG tự bịa mốc năm/quý tương lai (như bịa dời lịch họp Fed sang 2026 hay cuối 2025). Nếu nguồn không có thông tin, hãy nói thẳng 'Hiện chưa có phát ngôn hoặc số liệu mới'.\n"
        "2. ĐỐI CHIẾU CHÉO SỐ LIỆU GIỮA CÁC NGUỒN (CROSS-VERIFICATION):\n"
        "   - Chủ động gọi công cụ từ cả TCBS, SSI và VNStock để đối chiếu số liệu.\n"
        "   - Nêu rõ nguồn cho từng chỉ số quan trọng: [Nguồn VNStock], [Nguồn TCBS], [Nguồn SSI], [Nguồn Global Finance], [Nguồn X].\n"
        "3. KIỂM TRA MỐC THỜI GIAN (TIMESTAMP) - KHÔNG LẤY SỐ LIỆU CŨ:\n"
        "   - Kiểm tra ngày tháng phiên giao dịch. Thị trường cơ sở Việt Nam chỉ giao dịch 9h00 - 15h00 từ Thứ Hai đến Thứ Sáu.\n"
        "   - Nếu hỏi ngoài giờ, sáng sớm hoặc cuối tuần: Hãy ghi rõ 'Giá đóng cửa phiên gần nhất' kèm ngày tháng cụ thể.\n"
        "4. ĐỊNH DẠNG TRÌNH BÀY:\n"
        "   - Sử dụng Markdown rõ ràng: tiêu đề in đậm, bảng biểu tóm tắt, bullet phân tách ý, emoji chuyên nghiệp.\n"
        "===================================================================================="
    )

def call_gemini_api(payload: dict, api_key: str, models_chain: list = None, timeout: int = 35):
    """Gửi request tới Gemini API theo chuỗi fallback model an toàn"""
    if models_chain is None:
        models_chain = DEFAULT_MODELS_CHAIN

    for m in models_chain:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
        try:
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                return resp_data, m
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"Model {m} phản hồi HTTP {e.code}: {err_msg[:150]}")
            continue
        except Exception as e:
            logger.warning(f"Lỗi khi gọi model {m}: {e}")
            continue
    return None, None

# ==============================================================================
# QUẢN LÝ BỘ NHỚ HỘI THOẠI & CONTEXT TRACKER (MULTI-TURN PERSISTENT MEMORY)
# ==============================================================================
import time

SESSION_DIR = os.path.join(os.path.dirname(__file__), "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)
SESSION_MEMORY = {}
SESSION_TTL_SECONDS = 86400 * 3   # Lưu phiên bền vững 3 ngày (72 giờ)
MAX_HISTORY_MESSAGES = 40         # Lưu tới 40 tin nhắn (20 cặp hỏi-đáp chuyên sâu tương đương coding agent)

def _get_session_path(chat_id: int) -> str:
    return os.path.join(SESSION_DIR, f"{chat_id}.json")

def load_session_from_disk(chat_id: int) -> dict:
    p = _get_session_path(chat_id)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
                if time.time() - data.get("updated_at", 0) <= SESSION_TTL_SECONDS:
                    return data
        except Exception as e:
            logger.warning(f"Lỗi đọc session file cho {chat_id}: {e}")
    return None

def save_session_to_disk(chat_id: int, session: dict):
    p = _get_session_path(chat_id)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Lỗi lưu session file cho {chat_id}: {e}")

def get_user_session(chat_id: int) -> dict:
    if not chat_id:
        return {"chat_id": 0, "last_symbol": "", "history": [], "updated_at": time.time()}
    now = time.time()
    session = SESSION_MEMORY.get(chat_id)
    if not session:
        session = load_session_from_disk(chat_id)
        if session:
            SESSION_MEMORY[chat_id] = session

    if not session or (now - session.get("updated_at", 0) > SESSION_TTL_SECONDS):
        session = {
            "chat_id": chat_id,
            "last_symbol": "",
            "history": [],
            "updated_at": now
        }
        SESSION_MEMORY[chat_id] = session
        save_session_to_disk(chat_id, session)
    return session

def reset_user_session(chat_id: int) -> str:
    """Xóa bỏ toàn bộ ngữ cảnh hội thoại, đặt lại phiên chat mới từ đầu (New Chat)"""
    if not chat_id:
        return "⚠️ Không xác định được phiên người dùng."
    now = time.time()
    SESSION_MEMORY[chat_id] = {
        "chat_id": chat_id,
        "last_symbol": "",
        "history": [],
        "updated_at": now
    }
    p = _get_session_path(chat_id)
    if os.path.exists(p):
        try:
            os.remove(p)
        except Exception:
            pass
    return (
        "🧹 **ĐÃ LÀM MỚI PHIÊN HỘI THOẠI (NEW CHAT)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Toàn bộ ngữ cảnh và lịch sử trò chuyện trước đó đã được xóa sạch.\n"
        "Bạn có thể bắt đầu chủ đề phân tích hoặc tra cứu mã cổ phiếu mới hoàn toàn!"
    )

def get_session_summary(chat_id: int) -> str:
    """Báo cáo chi tiết dung lượng ngữ cảnh đang được ghi nhớ của người dùng"""
    session = get_user_session(chat_id)
    hist = session.get("history", [])
    last_sym = session.get("last_symbol") or "Chưa có"
    msg_count = len(hist)
    pairs_count = msg_count // 2
    return (
        "🧠 **TRẠNG THÁI BỘ NHỚ NGỮ CẢNH (SESSION MEMORY)**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• 🎯 **Cổ phiếu tâm điểm (Active Entity)**: `{last_sym}`\n"
        f"• 💬 **Dung lượng lịch sử đã lưu**: `{msg_count}` tin nhắn (~`{pairs_count}` lượt hỏi - đáp)\n"
        f"• 🚀 **Giới hạn bộ nhớ tối đa**: `{MAX_HISTORY_MESSAGES}` tin nhắn (~20 lượt trao đổi liên tục)\n"
        f"• 💾 **Trạng thái lưu trữ**: Đã đồng bộ bền vững vào file JSON trên VPS\n"
        f"• ⏱️ **Thời gian lưu phiên**: 72 giờ liên tục kể từ tin nhắn cuối\n\n"
        "💡 _Mẹo: Bạn có thể gõ `/reset` hoặc `/newchat` bất kỳ lúc nào để bắt đầu phiên mới!_"
    )

def update_user_session(chat_id: int, symbol: str = None, user_msg: str = None, model_msg: str = None):
    if not chat_id:
        return
    session = get_user_session(chat_id)
    session["updated_at"] = time.time()
    if symbol:
        session["last_symbol"] = symbol.upper().strip()
    if user_msg and model_msg:
        # Lưu nguyên vẹn câu hỏi và câu trả lời, không cắt cụt để giữ trọn vẹn số liệu và phân tích
        clean_user = user_msg.strip()
        clean_model = model_msg.strip()
        session["history"].append({"role": "user", "text": clean_user})
        session["history"].append({"role": "model", "text": clean_model})
        if len(session["history"]) > MAX_HISTORY_MESSAGES:
            session["history"] = session["history"][-MAX_HISTORY_MESSAGES:]
    save_session_to_disk(chat_id, session)

def build_gemini_contents_with_history(history: list, current_query: str) -> list:
    """Chuẩn hóa lịch sử hội thoại thành chuỗi contents hợp lệ cho Gemini API (user <-> model luân phiên)"""
    contents = []
    expected_role = "user"
    for item in history:
        r = item.get("role")
        t = item.get("text", "").strip()
        if not t:
            continue
        if r == expected_role:
            contents.append({"role": r, "parts": [{"text": t}]})
            expected_role = "model" if expected_role == "user" else "user"

    # Nếu phần tử cuối cùng trong contents là user, loại bỏ để chuẩn bị gắn lượt user mới
    if contents and contents[-1]["role"] == "user":
        contents.pop()

    # Thêm câu hỏi mới của người dùng
    contents.append({"role": "user", "parts": [{"text": current_query}]})
    return contents

def extract_symbols_from_query(query: str) -> list:
    """Trích xuất mã cổ phiếu từ câu hỏi người dùng"""
    found = re.findall(r'\b[A-Z]{3}\b', query)
    stopwords = {"VND", "USD", "GDP", "CPI", "FED", "BTC", "ETH", "TOP", "BUY", "EPS", "ROE", "ROA", "RED", "MAX", "MIN", "ALL", "NOW", "NEW"}
    cleaned = [s for s in found if s not in stopwords]
    if cleaned:
        return cleaned

    common_stocks = [
        "hpg", "vcb", "tcb", "ssi", "fpt", "mbb", "vhm", "vic", "mwg", "msn",
        "stb", "acb", "bid", "ctg", "dgc", "nvl", "pdr", "pvd", "pvs", "vnd",
        "hsg", "nkg", "kbc", "dig", "dpm", "dcm", "gvr", "plx", "gas", "vre"
    ]
    query_lower = query.lower()
    for cs in common_stocks:
        if re.search(r'\b' + cs + r'\b', query_lower):
            cleaned.append(cs.upper())
    return cleaned

def resolve_contextual_query(query: str, chat_id: int) -> tuple:
    """Kế thừa mã cổ phiếu gần nhất nếu người dùng dùng đại từ hoặc hỏi tiếp không nhắc tên mã"""
    symbols = extract_symbols_from_query(query)
    session = get_user_session(chat_id)
    last_symbol = session.get("last_symbol", "")

    if symbols:
        active_symbol = symbols[0]
        update_user_session(chat_id, symbol=active_symbol)
        return query, active_symbol

    pronouns = [
        "nó", "mã này", "con này", "cổ phiếu này", "doanh nghiệp này", "công ty này",
        "thế còn", "vậy còn", "p/e", "pb", "pe", "roe", "roa", "bctc", "báo cáo tài chính",
        "lợi nhuận", "doanh thu", "nợ", "nợ vay", "định giá", "vùng giá", "mua được không",
        "có nên mua", "bán không", "cắt lỗ", "kháng cự", "hỗ trợ", "target", "giá mục tiêu",
        "cổ tức", "tin tức", "triển vọng", "đánh giá", "sao rồi", "thế nào", "ổn không"
    ]
    query_lower = query.lower()
    is_followup = any(p in query_lower for p in pronouns) or len(query.split()) <= 6

    if is_followup and last_symbol:
        augmented_query = f"(Ngữ cảnh tiếp nối: Đang hỏi về cổ phiếu {last_symbol}) -> {query}"
        logger.info(f"-> [CONTEXT RESOLUTION] Kế thừa mã '{last_symbol}' cho câu hỏi: '{query}'")
        return augmented_query, last_symbol

    return query, last_symbol

def run_agentic_turn(user_query: str, api_key: str, chat_id: int = None, models_chain: list = None) -> str:
    """Vòng lặp ReAct Agent: LLM tự quyết định gọi công cụ -> Python thực thi -> LLM suy luận đối chiếu"""
    if models_chain is None:
        models_chain = DEFAULT_MODELS_CHAIN

    # 1. Phân luồng nhanh: Nếu là lời chào xã giao
    if is_simple_greeting(user_query):
        quick_payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{
                        "text": (
                            f"Bạn là Financial Assistant AI kết nối 4 hệ thống MCP (TCBS, SSI, VNStock, Yahoo Finance). "
                            f"Người dùng vừa nhắn: '{user_query}'. "
                            f"Hãy chào lại một cách chuyên nghiệp, thân thiện, ngắn gọn trong 2-3 câu bằng tiếng Việt và gợi ý người dùng nhập mã cổ phiếu (ví dụ: HPG, VCB, SSI) hoặc yêu cầu phân tích thị trường."
                        )
                    }]
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 250}
        }
        resp, _ = call_gemini_api(quick_payload, api_key, models_chain, timeout=10)
        if resp and "candidates" in resp:
            try:
                return resp["candidates"][0]["content"]["parts"][0]["text"].strip()
            except Exception:
                pass
        return "👋 Xin chào bạn! Tôi là Financial Assistant Bot, sẵn sàng phân tích dữ liệu chuyên sâu từ TCBS, SSI, VNStock và Yahoo Finance. Bạn có thể nhập mã cổ phiếu (ví dụ: HPG, VCB, SSI) hoặc bấm '🌅 Bản Tin Sáng' để xem báo cáo!"

    # 2. Xử lý tác vụ tài chính: Nạp 122 MCP tools và chạy ReAct Loop
    if not GEMINI_FUNCTION_DECLARATIONS:
        build_all_mcp_tools()

    resolved_query, active_symbol = resolve_contextual_query(user_query, chat_id)
    session = get_user_session(chat_id)

    system_text = get_agent_system_prompt()
    if active_symbol:
        system_text += f"\n\n[NGỮ CẢNH TRỌNG TÂM]: Người dùng đang tập trung hỏi về cổ phiếu: {active_symbol}. Khi người dùng nói 'nó', 'mã này', 'con này', 'thế còn...', bạn phải hiểu chính là mã {active_symbol}."

    # Nạp toàn bộ lịch sử hội thoại dài hạn (lên tới 40 tin nhắn ~ 20 cặp Q&A sâu)
    hist = session.get("history", [])
    contents = build_gemini_contents_with_history(hist, resolved_query)

    tools_payload = [{"functionDeclarations": GEMINI_FUNCTION_DECLARATIONS}]

    MAX_ROUNDS = 8
    for round_idx in range(MAX_ROUNDS):
        payload = {
            "systemInstruction": {
                "parts": [{"text": system_text}]
            },
            "contents": contents,
            "tools": tools_payload,
            "generationConfig": {
                "temperature": 0.0,
                "topP": 0.1,
                "maxOutputTokens": 3000
            }
        }

        resp_data, used_model = call_gemini_api(payload, api_key, models_chain, timeout=35)

        if not resp_data or "candidates" not in resp_data:
            return "⚠️ Hiện tại tất cả các máy chủ AI đều bận hoặc chưa thể phản hồi. Vui lòng thử lại sau ít giây."

        candidate = resp_data["candidates"][0]["content"]
        parts = candidate.get("parts", [])

        function_calls = [p["functionCall"] for p in parts if "functionCall" in p]
        text_parts = [p["text"] for p in parts if "text" in p]

        # Nếu Model không gọi tool nữa và đã đưa ra câu trả lời phân tích cuối cùng
        if not function_calls:
            final_text = "\n".join(text_parts).strip()
            logger.info(f"Agent hoàn tất suy luận sau {round_idx+1} lượt (Model: {used_model})")
            update_user_session(chat_id, symbol=active_symbol, user_msg=user_query, model_msg=final_text)
            return final_text

        # Thêm lượt trả lời của model vào lịch sử
        contents.append({"role": "model", "parts": parts})

        # Thực thi các Function Calls
        func_response_parts = []
        for fc in function_calls:
            f_name = fc["name"]
            f_args = fc.get("args", {})
            tool_output = execute_tool(f_name, f_args)

            # Giới hạn kích thước kết quả để tránh tràn ngữ cảnh
            if len(tool_output) > 8000:
                tool_output = tool_output[:8000] + "\n...(dữ liệu đã được cắt gọn)..."

            func_response_parts.append({
                "functionResponse": {
                    "name": f_name,
                    "response": {"result": tool_output}
                }
            })

        # Nạp lại kết quả thực thi cho Model để suy luận tiếp
        contents.append({"role": "function", "parts": func_response_parts})

    # NẾU ĐÃ CHẠY HẾT MAX_ROUNDS: Ép model tổng hợp ngay dựa trên dữ liệu đã thu thập được thay vì báo lỗi
    logger.info("Đã đạt giới hạn số vòng lặp tool, yêu cầu Agent tổng hợp ngay kết quả cuối cùng...")
    try:
        summary_contents = contents + [
            {
                "role": "user",
                "parts": [{
                    "text": "Đã hoàn thành quá trình thu thập dữ liệu MCP. Bây giờ hãy dừng việc gọi thêm công cụ và tổng hợp ngay bản phân tích/danh sách chi tiết, đầy đủ và trực quan nhất bằng tiếng Việt gửi cho người dùng dựa trên toàn bộ các dữ liệu đã lấy được ở trên."
                }]
            }
        ]
        summary_payload = {
            "contents": summary_contents,
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 3000
            }
        }
        summary_resp, smodel = call_gemini_api(summary_payload, api_key, models_chain, timeout=35)
        if summary_resp and "candidates" in summary_resp:
            c_parts = summary_resp["candidates"][0]["content"].get("parts", [])
            c_text = [p["text"] for p in c_parts if "text" in p]
            if c_text:
                final_res = "\n".join(c_text).strip()
                update_user_session(chat_id, symbol=active_symbol, user_msg=user_query, model_msg=final_res)
                return final_res
    except Exception as e:
        logger.error(f"Lỗi khi chạy bước tổng hợp cuối cùng: {e}")

    return "⚠️ Quá trình suy luận vượt quá số lượt cho phép."

def generate_morning_briefing_agentic(api_key: str) -> str:
    """Tạo Bản Tin Sáng 7h30 hoàn toàn tự động bằng Agentic ReAct Engine với 122 MCP tools"""
    prompt = (
        "Hãy tạo BẢN TIN SÁNG VĨ MÔ & THỊ TRƯỜNG CHỨNG KHOÁN VIỆT NAM (Morning Financial Briefing) gửi vào lúc 7h30 sáng trước giờ mở cửa.\n"
        "Bạn hãy chủ động gọi các công cụ MCP phù hợp để thu thập:\n"
        "1. Vĩ mô & Hàng hóa thế giới đêm qua: Vàng thế giới, Dầu Brent, S&P 500, Dow Jones, Tỷ giá USD/VND (sử dụng công cụ global_*).\n"
        "2. Diễn biến thị trường chứng khoán Việt Nam phiên gần nhất: Điểm số VN-Index, VN30, biến động %, thanh khoản khớp lệnh (sử dụng ssi_* hoặc tcbs_* hoặc vnstock_*).\n"
        "3. Các cổ phiếu trụ dẫn dắt và nhóm ngành đáng chú ý: Diễn biến các mã lớn (HPG, VCB, VIC, SSI, FPT) kèm dòng tiền và tác động chỉ số.\n"
        "4. Nhận định chiến lược phiên hôm nay: Khuyến nghị hành động, vùng điểm số quan sát và quản trị danh mục cho nhà đầu tư.\n\n"
        "Yêu cầu:\n"
        "- Trích xuất số liệu 100% trung thực từ công cụ MCP, ghi rõ nguồn [TCBS], [SSI], [Global Finance].\n"
        "- Định dạng Markdown Telegram đẹp mắt, chuyên nghiệp, sinh động, độ dài vừa vặn khoảng 400-550 từ."
    )
    return run_agentic_turn(prompt, api_key)


# ==============================================================================
# BỔ SUNG CÁC KỸ NĂNG TÀI CHÍNH & XỬ LÝ TÀI LIỆU CHUYÊN SÂU (SKILLS LAYER)
# ==============================================================================

def execute_equity_research(symbol: str, api_key: str) -> str:
    """Kỹ năng Equity Research: Lập Báo cáo Phân tích Đầu tư Chuẩn Wall Street kết hợp 122 MCP tools"""
    prompt = (
        f"Hãy lập BÁO CÁO PHÂN TÍCH CỔ PHIẾU TOÀN DIỆN (Equity Research Snapshot) cho mã {symbol.upper()}.\n"
        "Bạn hãy chủ động gọi các công cụ MCP phù hợp để thu thập:\n"
        f"1. Dữ liệu giao dịch realtime & sổ lệnh của {symbol} (giá, khối lượng, dòng tiền khối ngoại).\n"
        f"2. Các chỉ số định giá & tài chính từ TCBS/SSI (P/E, P/B, ROE, ROA, vốn hóa, điểm TCInvest).\n"
        f"3. Đánh giá sức khỏe tài chính & cơ cấu nợ vay.\n\n"
        "CẤU TRÚC BÁO CÁO BẮT BUỘC:\n"
        f"📊 **BÁO CÁO PHÂN TÍCH CỔ PHIẾU: {symbol.upper()}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. 🏢 **TỔNG QUAN & LUẬN ĐIỂM ĐẦU TƯ (Investment Thesis)**: Vị thế doanh nghiệp và lý do nên quan tâm.\n"
        "2. 📈 **THÔNG SỐ GIAO DỊCH & ĐỊNH GIÁ (Valuation Snapshot)**: Bảng tóm tắt Giá, P/E, P/B, ROE, Vốn hóa.\n"
        "3. 💼 **SỨC KHỎE TÀI CHÍNH (Financial Quality)**: Đánh giá cơ cấu nợ, biên lợi nhuận và khả năng tạo tiền.\n"
        "4. ⚖️ **CƠ HỘI (Bull Case) & RỦI RO TRỌNG YẾU (Bear Case)**: Động lực tăng trưởng vs rủi ro tiềm ẩn.\n"
        "5. 🎯 **KHUYẾN NGHỊ & VÙNG GIÁ MỤC TIÊU**: Mua/Tích lũy/Quan sát, vùng giá mua tham khảo và ngưỡng cắt lỗ.\n\n"
        "Yêu cầu: Trình bày Markdown Telegram sinh động, chuyên nghiệp và có chiều sâu."
    )
    return run_agentic_turn(prompt, api_key)


def execute_financial_statements_analysis(symbol: str, api_key: str) -> str:
    """Kỹ năng Financial Statements: Thẩm định & Bóc tách Báo cáo tài chính chuyên sâu"""
    prompt = (
        f"Hãy thực hiện BÓC TÁCH VÀ THẨM ĐỊNH BÁO CÁO TÀI CHÍNH (Financial Statements Forensic) cho mã {symbol.upper()}.\n"
        "Hãy chủ động gọi các công cụ MCP của TCBS và SSI để lấy thông tin tỷ lệ tài chính, doanh thu, lợi nhuận, đòn bẩy nợ của doanh nghiệp.\n\n"
        "CẤU TRÚC PHÂN TÍCH:\n"
        f"📑 **THẨM ĐỊNH BÁO CÁO TÀI CHÍNH: {symbol.upper()}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "1. 💰 **HIỆU QUẢ KINH DOANH & TĂNG TRƯỞNG**: Tăng trưởng doanh thu, lợi nhuận sau thuế, biên lợi nhuận gộp & ròng.\n"
        "2. 🛡️ **CƠ CẤU VỐN & ĐÒN BẨY TÀI CHÍNH**: Tỷ lệ Nợ/Vốn chủ sở hữu (D/E), khả năng chi trả lãi vay.\n"
        "3. 🌊 **DÒNG TIỀN & CHẤT LƯỢNG LỢI NHUẬN**: Dòng tiền hoạt động kinh doanh (CFO), rủi ro công nợ phải thu và hàng tồn kho.\n"
        "4. ⚠️ **CẢNH BÁO RỦI RO KẾ TOÁN (Red Flags)**: Có dấu hiệu bất thường về nợ, vay ngắn hạn hay dòng tiền âm không?\n"
        "5. 💡 **ĐÁNH GIÁ TỔNG KẾT**: Thang điểm sức khỏe tài chính (1-10) và lưu ý trọng yếu cho nhà đầu tư.\n\n"
        "Định dạng Markdown Telegram mạch lạc, rõ ràng."
    )
    return run_agentic_turn(prompt, api_key)


def execute_summarization(content: str, api_key: str) -> str:
    """Kỹ năng Summarization & Knowledge Synthesis: Tóm tắt thông tin tài chính theo chuẩn BLUF"""
    prompt = (
        "Bạn là Chuyên gia Tổng hợp & Phân tích Thông tin Tài chính cao cấp (Chief Research Officer).\n"
        "Nhiệm vụ: Phân tích, lọc nhiễu và tóm tắt văn bản/tin tức tài chính được cung cấp theo cấu trúc chuẩn mực:\n\n"
        "1. 📌 **TÓM TẮT CỐT LÕI (BLUF - Bottom Line Up Front)**: 1-2 câu kết luận quan trọng nhất.\n"
        "2. 🔍 **CÁC ĐIỂM THEN CHỐT (Key Highlights)**: 3-5 ý chính bám sát 100% nội dung thực tế. Nếu văn bản có số liệu thì trích dẫn chính xác, TUYỆT ĐỐI KHÔNG TỰ BỊA ĐẶT số liệu, ngày tháng hoặc chính sách vĩ mô không có trong nguồn.\n"
        "3. ⚖️ **TÁC ĐỘNG THỊ TRƯỜNG & CỔ PHIẾU (Market Impact)**:\n"
        "   - Chiều hướng: 🟢 TÍCH CỰC / 🔴 TIÊU CỰC / 🟡 TRUNG LẬP\n"
        "   - Nhóm ngành/Cổ phiếu chịu tác động trực tiếp.\n"
        "4. 🎯 **KHUYẾN NGHỊ HÀNH ĐỘNG (Takeaways for Investors)**: Nhà đầu tư nên làm gì (Quan sát, Chốt lời, Gom tích lũy, Quản trị rủi ro).\n\n"
        f"Văn bản/Dữ liệu cần tóm tắt và phân tích:\n{content}"
    )
    return run_agentic_turn(prompt, api_key)


def generate_comprehensive_market_report(api_key: str) -> str:
    """Kỹ năng Báo cáo Thị trường Chuyên sâu Toàn diện kết hợp 122 MCP tools"""
    now_vn = datetime.now(VN_TZ)
    date_str = now_vn.strftime("%d/%m/%Y")
    prompt = (
        f"Hãy lập BÁO CÁO PHÂN TÍCH CHIẾN LƯỢC THỊ TRƯỜNG TOÀN DIỆN (Market Strategy Report) ngày {date_str}.\n"
        "Bạn hãy chủ động gọi các công cụ MCP phù hợp để thu thập:\n"
        "1. Diễn biến phiên giao dịch gần nhất của chứng khoán Việt Nam: Điểm số đóng cửa VN-Index, VN30, HNX-Index, thanh khoản khớp lệnh (giá trị GD tính bằng tỷ đồng), độ rộng thị trường.\n"
        "2. Thống kê giao dịch Khối ngoại: Mua/bán ròng bao nhiêu tỷ đồng, top cổ phiếu gom ròng và xả ròng nhiều nhất (dùng công cụ từ tcbs hoặc ssi hoặc vnstock).\n"
        "3. Tác động của nhóm cổ phiếu trụ & các nhóm ngành dẫn dắt (Ngân hàng, Bất động sản, Thép, Chứng khoán, Công nghệ).\n"
        "4. Liên thị trường & Vĩ mô thế giới: Vàng thế giới, Dầu Brent, S&P 500, Dow Jones, Tỷ giá USD/VND (dùng công cụ global_*).\n"
        "5. Nhận định xu hướng kỹ thuật VN-Index, các mốc Hỗ trợ / Kháng cự then chốt, và Chiến lược hành động cụ thể cho nhà đầu tư.\n\n"
        "CẤU TRÚC BÁO CÁO BẮT BUỘC:\n"
        "📊 **BÁO CÁO CHIẾN LƯỢC THỊ TRƯỜNG TOÀN DIỆN**\n"
        f"📅 _Cập nhật ngày: {date_str} | Chuyên gia phân tích cấp cao_\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "1. 🏛️ **TỔNG QUAN PHIÊN GIAO DỊCH GẦN NHẤT (VN-INDEX)**:\n"
        "   - Đóng cửa, biến động (+/- điểm, +/- %).\n"
        "   - Thanh khoản thị trường so với mức bình quân 20 phiên.\n"
        "   - Độ rộng thị trường và tâm lý dòng tiền.\n\n"
        "2. 🌊 **DÒNG TIỀN KHỐI NGOẠI & TỰ DOANH**:\n"
        "   - Giá trị mua/bán ròng của khối ngoại và các mã tâm điểm gom/xả.\n\n"
        "3. 🏢 **TÂM ĐIỂM CỔ PHIẾU TRỤ & DIỄN BIẾN NHÓM NGÀNH**:\n"
        "   - Các nhóm ngành thu hút dòng tiền (Ngân hàng, Thép, BĐS, Chứng khoán, Công nghệ).\n\n"
        "4. 🌍 **BỐI CẢNH VĨ MÔ & LIÊN THỊ TRƯỜNG QUỐC TẾ**:\n"
        "   - Vàng, Dầu, Phố Wall (S&P 500) và Tỷ giá USD/VND.\n\n"
        "5. 🧭 **NHẬN ĐỊNH KỸ THUẬT & CHIẾN LƯỢC PHIÊN TIẾP THEO**:\n"
        "   - Vùng Hỗ trợ (Support) | Vùng Kháng cự (Resistance).\n"
        "   - Tỷ trọng khuyến nghị Cổ phiếu / Tiền mặt và lưu ý quản trị rủi ro.\n\n"
        "Yêu cầu: Trình bày chuẩn mực CTCK, chuyên nghiệp, định dạng Markdown Telegram đẹp mắt."
    )
    return run_agentic_turn(prompt, api_key)

import os
import json
import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Query, Body, BackgroundTasks
from pydantic import BaseModel
from dotenv import load_dotenv
from twscrape import API, gather

# Load cấu hình từ .env
load_dotenv("/home/ubuntu/x-intel-mcp/.env")

PORT = int(os.getenv("PORT", "8890"))
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_ADMIN_CHAT_ID = os.getenv("TELEGRAM_ADMIN_CHAT_ID", "1053147321")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
WATCH_INTERVAL_MINUTES = int(os.getenv("WATCH_INTERVAL_MINUTES", "10"))

BASE_DIR = "/home/ubuntu/x-intel-mcp"
DB_PATH = os.path.join(BASE_DIR, "accounts.db")
WATCHLIST_PATH = os.path.join(BASE_DIR, "watchlist.json")
STATE_PATH = os.path.join(BASE_DIR, "state.json")
FEED_PATH = os.path.join(BASE_DIR, "recent_feed.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("x-intel")

app = FastAPI(
    title="X Intel MCP Service",
    description="Dịch vụ thu thập thông tin X (Twitter) từ các VIP & tích hợp Telegram/Gemini AI",
    version="1.0.0"
)

# Khởi tạo twscrape API
tw_api = API(pool=DB_PATH)

# Helper đọc ghi state và watchlist
def load_json(filepath: str, default_val: Any) -> Any:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Lỗi đọc {filepath}: {e}")
    return default_val

def save_json(filepath: str, data: Any):
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Lỗi ghi {filepath}: {e}")

# Models
class CookieAccountRequest(BaseModel):
    username: str
    cookies: str

class CredentialAccountRequest(BaseModel):
    username: str
    password: str
    email: str
    email_password: str

# Helper Telegram & Gemini
async def send_telegram_alert(text: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_ADMIN_CHAT_ID:
        logger.warning("Chưa cấu hình Telegram Bot Token hoặc Admin Chat ID")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_ADMIN_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                logger.error(f"Lỗi gửi Telegram: {resp.text}")
    except Exception as e:
        logger.error(f"Lỗi ngoại lệ gửi Telegram: {e}")

async def summarize_tweet_with_gemini(author_name: str, tweet_text: str) -> str:
    if not GEMINI_API_KEY:
        return ""
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    prompt = (
        f"Bạn là chuyên gia phân tích tài chính và tin tức thị trường. "
        f"Lãnh đạo/VIP: '{author_name}' vừa đăng một bài viết trên X với nội dung sau:\n"
        f"\"\"\"{tweet_text}\"\"\"\n\n"
        f"Yêu cầu:\n"
        f"1. Dịch ý chính sang tiếng Việt mạch lạc, súc tích (1-2 câu).\n"
        f"2. Đánh giá nhanh tác động ngắn hạn (nếu có) tới thị trường chứng khoán, công nghệ hoặc crypto (1 câu).\n"
        f"Trình bày ngắn gọn, gạch đầu dòng rõ ràng."
    )
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300}
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(endpoint, json=payload)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as e:
        logger.error(f"Lỗi gọi Gemini AI tóm tắt: {e}")
    return ""

# API Endpoints
@app.get("/health")
@app.get("/api/status")
async def get_status():
    try:
        accounts = await tw_api.pool.accounts_info()
        return {
            "status": "online",
            "active_accounts": len([a for a in accounts if a.get("active", False)]),
            "total_accounts": len(accounts),
            "accounts": accounts,
            "watch_interval_minutes": WATCH_INTERVAL_MINUTES
        }
    except Exception as e:
        return {"status": "warning", "error": str(e), "total_accounts": 0}

@app.post("/api/accounts/add_cookies")
async def add_account_cookies(req: CookieAccountRequest):
    try:
        await tw_api.pool.add_account_cookies(req.username, req.cookies)
        return {"status": "success", "message": f"Đã thêm tài khoản @{req.username} bằng cookie thành công."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/accounts/add_credential")
async def add_account_credential(req: CredentialAccountRequest):
    try:
        await tw_api.pool.add_account(req.username, req.password, req.email, req.email_password)
        return {"status": "success", "message": f"Đã thêm tài khoản @{req.username} vào pool."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/accounts/login_all")
async def login_all():
    try:
        res = await tw_api.pool.login_all()
        return {"status": "success", "result": res}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/tweets")
async def get_user_tweets(username: str = Query(..., description="Username X, ví dụ: elonmusk"), limit: int = Query(5, ge=1, le=20)):
    accounts = await tw_api.pool.accounts_info()
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="Chưa có tài khoản X trong hệ thống. Vui lòng thêm cookie tài khoản clone vào trước."
        )
    try:
        user = await tw_api.user_by_login(username)
        if not user:
            raise HTTPException(status_code=404, detail=f"Không tìm thấy người dùng @{username}")
        
        tweets_raw = await gather(tw_api.user_tweets(user.id, limit=limit))
        result = []
        for t in tweets_raw:
            result.append({
                "id": str(t.id),
                "url": t.url,
                "date": t.date.isoformat() if t.date else None,
                "rawContent": t.rawContent,
                "likeCount": t.likeCount,
                "retweetCount": t.retweetCount,
                "replyCount": t.replyCount,
                "user": {
                    "username": t.user.username,
                    "displayname": t.user.displayname
                }
            })
        return {
            "status": "success",
            "username": username,
            "user_id": user.id,
            "count": len(result),
            "tweets": result
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Lỗi lấy tweet của @{username}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/search")
async def search_tweets(q: str = Query(..., description="Từ khóa tìm kiếm"), limit: int = Query(5, ge=1, le=20)):
    try:
        tweets_raw = await gather(tw_api.search(q, limit=limit))
        result = []
        for t in tweets_raw:
            result.append({
                "id": str(t.id),
                "url": t.url,
                "date": t.date.isoformat() if t.date else None,
                "rawContent": t.rawContent,
                "likeCount": t.likeCount,
                "retweetCount": t.retweetCount,
                "user": {
                    "username": t.user.username,
                    "displayname": t.user.displayname
                }
            })
        return {"status": "success", "query": q, "count": len(result), "tweets": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/watchlist")
async def get_watchlist():
    return load_json(WATCHLIST_PATH, [])

@app.post("/api/watchlist")
async def update_watchlist(watchlist: List[Dict[str, str]] = Body(...)):
    save_json(WATCHLIST_PATH, watchlist)
    return {"status": "success", "count": len(watchlist)}

@app.get("/api/digest")
async def get_digest(limit: int = Query(25, ge=1, le=50)):
    feed = load_json(FEED_PATH, [])
    return {
        "status": "success",
        "count": len(feed[:limit]),
        "feed": feed[:limit]
    }

# Logic Watcher định kỳ - Tích lũy tin tức phục vụ báo cáo tổng hợp 3 cữ mỗi ngày
async def run_vip_check_cycle():
    accounts = await tw_api.pool.accounts_info()
    if not accounts:
        logger.info("Chưa có tài khoản X trong pool, bỏ qua vòng quét.")
        return

    watchlist = load_json(WATCHLIST_PATH, [])
    if not watchlist:
        return

    state = load_json(STATE_PATH, {})
    feed = load_json(FEED_PATH, [])
    new_items_count = 0

    for item in watchlist:
        username = item.get("username")
        display_name = item.get("name", username)
        category = item.get("category", "General")
        if not username:
            continue

        try:
            user = await tw_api.user_by_login(username)
            if not user:
                continue
            
            tweets = await gather(tw_api.user_tweets(user.id, limit=3))
            if not tweets:
                continue

            last_seen_id = state.get(username)
            latest_tweet = tweets[0]

            # Nếu là lần chạy đầu tiên cho VIP này
            if not last_seen_id:
                state[username] = str(latest_tweet.id)
                # Lưu tweet đầu tiên vào feed
                feed.insert(0, {
                    "id": str(latest_tweet.id),
                    "username": username,
                    "author": display_name,
                    "category": category,
                    "time": latest_tweet.date.strftime('%H:%M %d/%m/%Y') if latest_tweet.date else "Gần đây",
                    "content": latest_tweet.rawContent,
                    "url": latest_tweet.url,
                    "likes": latest_tweet.likeCount,
                    "retweets": latest_tweet.retweetCount,
                    "summary": ""
                })
                continue

            # Kiểm tra xem có tweet mới hơn không
            new_tweets = []
            for t in tweets:
                if str(t.id) == last_seen_id:
                    break
                new_tweets.append(t)

            if new_tweets:
                for t in reversed(new_tweets):
                    logger.info(f"Thu thập tin mới từ @{username}: {t.id}")
                    ai_summary = await summarize_tweet_with_gemini(display_name, t.rawContent)
                    
                    feed.insert(0, {
                        "id": str(t.id),
                        "username": username,
                        "author": display_name,
                        "category": category,
                        "time": t.date.strftime('%H:%M %d/%m/%Y') if t.date else "Vừa xong",
                        "content": t.rawContent,
                        "url": t.url,
                        "likes": t.likeCount,
                        "retweets": t.retweetCount,
                        "summary": ai_summary
                    })
                    new_items_count += 1

                state[username] = str(latest_tweet.id)

        except Exception as e:
            logger.error(f"Lỗi kiểm tra VIP @{username}: {e}")

    # Giữ lại tối đa 100 tin gần nhất (khử trùng lặp theo ID)
    seen_ids = set()
    deduped_feed = []
    for item in feed:
        item_id = item.get("id")
        if item_id and item_id not in seen_ids:
            seen_ids.add(item_id)
            deduped_feed.append(item)
    
    save_json(FEED_PATH, deduped_feed[:100])
    if state:
        save_json(STATE_PATH, state)
    logger.info(f"Hoàn thành vòng thu thập tin. Số tin mới tích lũy: {new_items_count}, tổng feed: {len(deduped_feed[:100])}")

@app.post("/api/poll_now")
async def poll_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_vip_check_cycle)
    return {"status": "success", "message": "Đang chạy vòng quét VIP trong nền."}

# Background Loop
async def watcher_loop():
    logger.info(f"Khởi động vòng lặp Watcher VIP X (chu kỳ {WATCH_INTERVAL_MINUTES} phút)...")
    # Đợi 10 giây khi khởi động service
    await asyncio.sleep(10)
    while True:
        try:
            await run_vip_check_cycle()
        except Exception as e:
            logger.error(f"Lỗi trong watcher_loop: {e}")
        await asyncio.sleep(WATCH_INTERVAL_MINUTES * 60)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(watcher_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("x_intel_service:app", host="0.0.0.0", port=PORT, reload=False)

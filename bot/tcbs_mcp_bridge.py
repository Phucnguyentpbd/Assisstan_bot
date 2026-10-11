import sys
import json
import os
import urllib.request
import urllib.parse
import time

CREDS_FILE = os.path.expanduser("~/.tcbs-mcp/credentials.json")
TCBS_MCP_URL = "https://mcp.tcbs.com.vn/mcp/tcinvest/"
TOKEN_ENDPOINT = "https://mcp.tcbs.com.vn/tcinvest/token"
CLIENT_ID = "tcinvest"
CLIENT_SECRET = "dummy"

CURRENT_SESSION_ID = None

def load_credentials():
    if not os.path.exists(CREDS_FILE):
        return None
    try:
        with open(CREDS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

def save_credentials(creds):
    os.makedirs(os.path.dirname(CREDS_FILE), exist_ok=True)
    with open(CREDS_FILE, "w", encoding="utf-8") as f:
        json.dump(creds, f, indent=2, ensure_ascii=False)

def refresh_token(creds):
    rf = creds.get("refresh_token")
    if not rf:
        return None
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": rf
    }).encode('utf-8')

    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            creds.update(data)
            creds["updated_at"] = time.time()
            save_credentials(creds)
            return creds.get("access_token")
    except Exception as e:
        sys.stderr.write(f"[tcbs_bridge] Error refreshing token: {e}\n")
        return None

def get_valid_access_token():
    creds = load_credentials()
    if not creds:
        return None
    updated_at = creds.get("updated_at", 0)
    expires_in = creds.get("expires_in", 3600)
    if time.time() - updated_at > (expires_in - 300) and "refresh_token" in creds:
        new_token = refresh_token(creds)
        if new_token:
            return new_token
    return creds.get("access_token")

def forward_request_to_tcbs(json_rpc_req, access_token):
    global CURRENT_SESSION_ID
    body = json.dumps(json_rpc_req).encode('utf-8')
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {access_token}",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json, text/event-stream"
    }
    if CURRENT_SESSION_ID:
        headers["mcp-session-id"] = CURRENT_SESSION_ID

    req = urllib.request.Request(TCBS_MCP_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            new_sess = resp.headers.get("mcp-session-id")
            if new_sess:
                CURRENT_SESSION_ID = new_sess

            resp_content = resp.read().decode('utf-8')
            # Kiểm tra xem có dòng data:
            for line in resp_content.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str:
                        return json.loads(data_str)
            
            # Fallback nếu trả về JSON trực tiếp
            if resp_content.strip():
                return json.loads(resp_content.strip())
            return {"jsonrpc": "2.0", "id": json_rpc_req.get("id"), "result": {}}

    except urllib.error.HTTPError as e:
        err_body = e.read().decode('utf-8', errors='ignore')
        if e.code == 401:
            creds = load_credentials()
            if creds and "refresh_token" in creds:
                new_token = refresh_token(creds)
                if new_token:
                    return forward_request_to_tcbs(json_rpc_req, new_token)
        return {
            "jsonrpc": "2.0",
            "id": json_rpc_req.get("id"),
            "error": {"code": e.code, "message": f"TCBS Server Error: {err_body}"}
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": json_rpc_req.get("id"),
            "error": {"code": -32603, "message": f"TCBS Bridge Forwarding Error: {str(e)}"}
        }

def list_tcbs_tools(access_token):
    # Đảm bảo handshake initialize trước
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "finance-client", "version": "1.0.0"}
        }
    }
    forward_request_to_tcbs(init_req, access_token)

    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    return forward_request_to_tcbs(req, access_token)

def call_tcbs_tool(tool_name, arguments, access_token):
    req = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    return forward_request_to_tcbs(req, access_token)

def run_stdio_bridge():
    token = get_valid_access_token()
    if not token:
        sys.stderr.write("[tcbs_bridge] WARNING: No credentials found. Run tcbs_auth.py first!\n")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        token = get_valid_access_token()
        if not token:
            res = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "error": {
                    "code": -32000,
                    "message": "TCBS MCP chưa được xác thực. Vui lòng chạy tcbs_auth.py để đăng nhập."
                }
            }
        else:
            res = forward_request_to_tcbs(req, token)

        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    run_stdio_bridge()

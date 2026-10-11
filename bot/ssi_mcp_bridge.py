import sys
import json
import os
import urllib.request
import urllib.parse
import time

CREDS_FILE = os.path.expanduser("~/.ssi-mcp/credentials.json")
SSI_MCP_URL = "https://mcp.ssi.com.vn/mcp"
TOKEN_ENDPOINT = "https://mcp.ssi.com.vn/oauth/token"
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
    client_id = creds.get("client_id", "dcr-fc1ee423c2c024a50068cba2ba076666")
    if not rf:
        return None
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": client_id,
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
            sys.stderr.write("[ssi_bridge] Successfully refreshed SSI access token.\n")
            return creds.get("access_token")
    except Exception as e:
        sys.stderr.write(f"[ssi_bridge] Error refreshing SSI token: {e}\n")
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

def forward_request_to_ssi(json_rpc_req, access_token):
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

    req = urllib.request.Request(SSI_MCP_URL, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            new_sess = resp.headers.get("mcp-session-id")
            if new_sess:
                CURRENT_SESSION_ID = new_sess

            resp_content = resp.read().decode('utf-8')
            for line in resp_content.splitlines():
                line = line.strip()
                if line.startswith("data:"):
                    data_str = line[5:].strip()
                    if data_str:
                        return json.loads(data_str)
            
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
                    return forward_request_to_ssi(json_rpc_req, new_token)
        return {
            "jsonrpc": "2.0",
            "id": json_rpc_req.get("id"),
            "error": {"code": e.code, "message": f"SSI Server Error: {err_body}"}
        }
    except Exception as e:
        return {
            "jsonrpc": "2.0",
            "id": json_rpc_req.get("id"),
            "error": {"code": -32603, "message": f"SSI Bridge Forwarding Error: {str(e)}"}
        }

def list_ssi_tools(access_token):
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
    forward_request_to_ssi(init_req, access_token)
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    return forward_request_to_ssi(req, access_token)

def call_ssi_tool(tool_name, arguments, access_token):
    req = {
        "jsonrpc": "2.0",
        "id": int(time.time()),
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments
        }
    }
    return forward_request_to_ssi(req, access_token)

def run_stdio_bridge():
    token = get_valid_access_token()
    if not token:
        sys.stderr.write("[ssi_bridge] WARNING: No credentials found. Run ssi_auth.py first!\n")

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
                    "message": "SSI MCP chưa được xác thực. Vui lòng chạy ssi_auth.py để đăng nhập."
                }
            }
        else:
            res = forward_request_to_ssi(req, token)

        sys.stdout.write(json.dumps(res, ensure_ascii=False) + "\n")
        sys.stdout.flush()

if __name__ == "__main__":
    run_stdio_bridge()

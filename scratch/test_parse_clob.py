import httpx
import json

CLOB_API = "https://clob.polymarket.com"
condition_id = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f"

def get_market_tokens_clob(condition_id):
    try:
        resp = httpx.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            tokens = data.get("tokens", [])
            return {t["outcome"].lower(): t["token_id"] for t in tokens}
    except Exception as e:
        print(f"Error: {e}")
    return None

tokens = get_market_tokens_clob(condition_id)
print(f"Parsed tokens: {json.dumps(tokens, indent=2)}")

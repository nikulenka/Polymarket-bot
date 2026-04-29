import httpx
import json

GAMMA_API = "https://gamma-api.polymarket.com"
condition_id = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f"

def test_api(param_name):
    print(f"Testing with param: {param_name}")
    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params={param_name: condition_id}, timeout=10)
        print(f"Status: {resp.status_code}")
        print(f"Response: {resp.text[:500]}...")
    except Exception as e:
        print(f"Error: {e}")

test_api("condition_id")
test_api("condition_ids")

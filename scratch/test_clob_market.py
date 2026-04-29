import httpx
import json

CLOB_API = "https://clob.polymarket.com"
condition_id = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f"

def test_clob_market():
    print(f"Testing CLOB API for condition_id: {condition_id}")
    try:
        resp = httpx.get(f"{CLOB_API}/markets/{condition_id}", timeout=10)
        print(f"Status: {resp.status_code}")
        if resp.status_code == 200:
            print(f"Response: {resp.text}")
        else:
            print(f"Error response: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

test_clob_market()

import httpx
import json

GAMMA_API = "https://gamma-api.polymarket.com"
condition_id = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f"

def test_search_by_id():
    print(f"Searching for condition_id as a search query: {condition_id}")
    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params={"search": condition_id}, timeout=10)
        print(f"Status: {resp.status_code}")
        data = resp.json()
        print(f"Found {len(data)} results.")
        for m in data:
            print(f"Title: {m.get('question')} (ID: {m.get('conditionId')})")
    except Exception as e:
        print(f"Error: {e}")

test_search_by_id()

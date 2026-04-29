import httpx
import json

GAMMA_API = "https://gamma-api.polymarket.com"
condition_id = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f"

def test_api(params):
    print(f"Testing with params: {params}")
    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params=params, timeout=10)
        print(f"Status: {resp.status_code}")
        data = resp.json()
        if data:
            print(f"Found {len(data)} results. First one: {data[0].get('question')} (ID: {data[0].get('conditionId')})")
            if any(m.get('conditionId') == condition_id for m in data):
                print("✅ Found the EXACT match in the list!")
            else:
                print("❌ No exact match in the returned list.")
        else:
            print("Response is empty list []")
    except Exception as e:
        print(f"Error: {e}")
    print("-" * 30)

test_api({"condition_id": condition_id})
test_api({"conditionId": condition_id})
test_api({"condition_ids": condition_id})
test_api({"conditionIds": condition_id})
test_api({"id": condition_id})

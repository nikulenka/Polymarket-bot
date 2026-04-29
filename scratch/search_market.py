import httpx
import json

GAMMA_API = "https://gamma-api.polymarket.com"

def search_market(query):
    print(f"Searching for: {query}")
    try:
        resp = httpx.get(f"{GAMMA_API}/markets", params={"search": query}, timeout=10)
        print(f"Status: {resp.status_code}")
        data = resp.json()
        for m in data:
            print(f"Title: {m.get('question')}")
            print(f"ConditionID: {m.get('conditionId')}")
            print(f"Active: {m.get('active')}")
            print(f"Closed: {m.get('closed')}")
            print("-" * 20)
    except Exception as e:
        print(f"Error: {e}")

search_market("Trump dance")

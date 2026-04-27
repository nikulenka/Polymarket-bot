import httpx
import os
import json
from dotenv import load_dotenv

load_dotenv()
api_key = os.getenv("OPENROUTER_API_KEY")

def test_claude():
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    # Тестируем тот же запрос, что в боте
    payload = {
        "model": "anthropic/claude-3-5-haiku-latest",
        "messages": [{"role": "user", "content": "Test. Return JSON: {'status': 'ok'}"}],
        "response_format": {"type": "json_object"}
    }
    
    try:
        resp = httpx.post(url, headers=headers, json=payload, timeout=10)
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_claude()

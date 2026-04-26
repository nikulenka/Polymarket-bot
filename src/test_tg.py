import os
from dotenv import load_dotenv
import httpx

load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOCKEN") or os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    print("Отсутствует токен или Chat ID")
    exit(1)

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
msg = "✅ <b>Polymarket Bot</b>\n\nИнтеграция с Telegram успешно настроена! Бот готов присылать сигналы."

try:
    resp = httpx.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML"}, timeout=5)
    if resp.status_code == 200:
        print("✅ Тестовое сообщение успешно отправлено!")
    else:
        print(f"⚠️ Ошибка от Telegram: {resp.text}")
except Exception as e:
    print(f"Ошибка: {e}")

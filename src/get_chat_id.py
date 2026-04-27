import httpx
import os
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    print("❌ Токен не найден в .env")
    exit(1)

print("🔍 Проверяю последние сообщения бота...")
try:
    resp = httpx.get(f"https://api.telegram.org/bot{TOKEN}/getUpdates")
    data = resp.json()
    
    if data.get("ok") and data.get("result"):
        for item in data["result"]:
            if "message" in item:
                chat = item["message"]["chat"]
                print(f"✅ Найден Chat ID: {chat['id']} (Пользователь: {chat.get('username', 'Unknown')})")
                
                # Дописываем в .env если там еще нет
                with open(".env", "r") as f:
                    env_content = f.read()
                
                if "TELEGRAM_CHAT_ID" not in env_content:
                    with open(".env", "a") as f:
                        f.write(f"\nTELEGRAM_CHAT_ID={chat['id']}\n")
                    print("📝 TELEGRAM_CHAT_ID автоматически добавлен в .env!")
                break
        else:
            print("⚠️ Сообщений не найдено. Пожалуйста, напиши любое сообщение своему боту в Telegram и запусти скрипт снова.")
    else:
        print("⚠️ Сообщений не найдено. Напиши боту /start и попробуй снова.")
        
except Exception as e:
    print(f"Ошибка запроса: {e}")

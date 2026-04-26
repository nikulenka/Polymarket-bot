import os
from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from dotenv import load_dotenv

load_dotenv()

def onboard_and_derive():
    pk = os.getenv("POLY_PRIVATE_KEY")
    if not pk or "ваш_ключ" in pk:
        print("❌ ОШИБКА: Убедитесь, что POLY_PRIVATE_KEY в .env — это ваш реальный HEX ключ.")
        return

    print(f"⏳ Начинаем процесс настройки для кошелька...")
    
    try:
        # Инициализируем клиент
        client = ClobClient(
            host="https://clob.polymarket.com",
            chain_id=POLYGON,
            key=pk,
            signature_type=1 # EOA
        )
        
        # 1. Пытаемся извлечь существующие API ключи
        try:
            creds = client.derive_api_key()
            print("✅ Существующие ключи успешно извлечены!")
        except:
            print("⚠️ Не удалось извлечь ключи. Попытка создать НОВЫЕ (Onboarding)...")
            creds = client.create_api_key()
            print("✅ Новые API ключи созданы!")

        # 2. Регистрируем ключи в клиенте и делаем Onboard
        client.set_api_creds(creds)
        
        try:
            client.onboard()
            print("✅ Кошелек успешно зарегистрирован (Onboarded)!")
        except Exception as e:
            if "already onboarded" in str(e).lower():
                print("✅ Кошелек уже был зарегистрирован ранее.")
            else:
                print(f"⚠️ Ошибка при Onboard (возможно уже зарегистрирован): {e}")

        print("\n🚀 ВАШИ ДАННЫЕ ДЛЯ .env (СКОПИРУЙТЕ ИХ):")
        # Используем атрибуты напрямую
        print(f"POLY_API_KEY={creds.api_key}")
        print(f"POLY_API_SECRET={creds.api_secret}")
        print(f"POLY_API_PASSPHRASE={creds.api_passphrase}")
        
    except Exception as e:
        print(f"\n❌ ОШИБКА: {e}")

if __name__ == "__main__":
    onboard_and_derive()

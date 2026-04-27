from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds
import os
from dotenv import load_dotenv

load_dotenv()

def test_client():
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    key = os.getenv("POLY_PRIVATE_KEY")
    if not key.startswith("0x"):
        key = "0x" + key
        
    client = ClobClient(
        host="https://clob.polymarket.com",
        chain_id=POLYGON,
        key=key,
        creds=creds
    )
    
    try:
        print(f"Client Address: {client.get_address()}")
        # Попробуем получить баланс или что-то простое
        # Но для начала просто проверим инициализацию и адрес.
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    test_client()

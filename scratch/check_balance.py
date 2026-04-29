import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.constants import POLYGON
from py_clob_client_v2.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

load_dotenv()

def check():
    creds = ApiCreds(
        api_key=os.getenv("POLY_API_KEY"),
        api_secret=os.getenv("POLY_API_SECRET"),
        api_passphrase=os.getenv("POLY_API_PASSPHRASE"),
    )
    key = os.getenv("POLY_PRIVATE_KEY")
    if key and not key.startswith("0x"):
        key = "0x" + key
        
    proxy = os.getenv("POLY_PROXY_ADDRESS")
    
    client = ClobClient("https://clob.polymarket.com", POLYGON, key, creds, signature_type=2, funder=proxy)
    
    print(f"Checking balances for Proxy: {proxy}")
    
    # Check Collateral Balance (pUSD)
    params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    resp = client.get_balance_allowance(params)
    if resp:
        raw_balance = float(resp.get("balance", 0))
        print(f"Collateral Balance: ${raw_balance / 10**6:.2f} pUSD")

    import json
    if os.path.exists("data/open_positions.json"):
        with open("data/open_positions.json", "r") as f:
            pos = json.load(f)
            for tid, data in pos.items():
                print(f"\nChecking token: {tid} ({data.get('market')})")
                try:
                    # В V2 можно получить баланс конкретного токена
                    token_params = BalanceAllowanceParams(asset_type=AssetType.CONDITIONAL, token_id=tid)
                    t_resp = client.get_balance_allowance(token_params)
                    if t_resp:
                        t_balance = float(t_resp.get("balance", 0)) / 10**6
                        print(f"Current Balance: {t_balance:.4f} tokens")
                    print(f"Expected tokens in bot memory: {data.get('tokens')}")
                except Exception as e:
                    print(f"Error checking token balance: {e}")

if __name__ == "__main__":
    check()

from src.trader import get_client
from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
import os

def test():
    try:
        client = get_client()
        print("Testing get_balance_allowance with params...")
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params=params)
        print(f"Response: {resp}")
    except Exception as e:
        print(f"Error with params=params: {e}")
        
    try:
        client = get_client()
        print("\nTesting get_balance_allowance with positional argument...")
        params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
        resp = client.get_balance_allowance(params)
        print(f"Response: {resp}")
    except Exception as e:
        print(f"Error with positional: {e}")

if __name__ == "__main__":
    test()

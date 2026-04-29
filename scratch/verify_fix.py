import sys
import os
import json

# Add src to path
sys.path.append(os.getcwd())

from src.monitor import get_market_tokens

# Test IDs
CLOSED_ID = "0x2d55f622bc12e23dc1f1bb4db8360c28c92155f9376bf73953c0756ee1387b2f" # Trump dance (Closed)
ACTIVE_ID = "0x9c1a953fe92c8357f1b646ba25d983aa83e90c525992db14fb726fa895cb5763" # Russia-Ukraine GTA VI (Active)

def test():
    print(f"Testing Closed Market: {CLOSED_ID}")
    res = get_market_tokens(CLOSED_ID)
    print(f"Result: {res}")
    
    print(f"\nTesting Active Market: {ACTIVE_ID}")
    res = get_market_tokens(ACTIVE_ID)
    print(f"Result: {json.dumps(res, indent=2) if isinstance(res, dict) else res}")

if __name__ == "__main__":
    test()

import httpx
import pandas as pd
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def test_polymarket_connection():
    url = "https://clob.polymarket.com/markets"
    
    try:
        print(f"Connecting to {url}...")
        response = httpx.get(url)
        response.raise_for_status()
        
        data = response.json()
        
        # Polymarket API returns a list of markets or an object with data
        # Based on CLOB documentation, it's usually a list of markets
        markets = data if isinstance(data, list) else data.get('data', [])
        
        if not markets:
            print("No markets found.")
            return

        print("\n--- First 3 Markets ---")
        for i, market in enumerate(markets[:3]):
            question = market.get('question', 'N/A')
            active = market.get('active', 'N/A')
            volume = market.get('volume', 'N/A')
            print(f"{i+1}. Question: {question}")
            print(f"   Active: {active}")
            print(f"   Volume: {volume}")
            print("-" * 20)
            
        print("\n✓ Соединение работает")
        
    except httpx.HTTPStatusError as e:
        print(f"HTTP error occurred: {e}")
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    test_polymarket_connection()

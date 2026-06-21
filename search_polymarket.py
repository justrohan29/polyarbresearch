import argparse
import requests
import json

def search_polymarket(query: str, proxy: str = None):
    proxies = {"http": proxy, "https": proxy} if proxy else None
    url = "https://gamma-api.polymarket.com/markets"
    
    # Fetch top 500 active markets to search locally
    params = {"limit": 500, "active": "true"}
    
    print(f"Searching active Polymarket markets for: '{query}'")
    if proxy:
        print(f"Using proxy: {proxy}")
        
    try:
        resp = requests.get(url, params=params, timeout=30, proxies=proxies)
        resp.raise_for_status()
    except requests.exceptions.ProxyError as e:
        print(f"[!] Proxy dead or unreachable: {e}")
        return
    except requests.exceptions.RequestException as e:
        print(f"[!] Connection failed (likely geo-blocked). Use --proxy.\nError: {e}")
        return
        
    markets = resp.json()
    
    # Client-side filter
    query = query.lower()
    matches = []
    for m in markets:
        question = m.get("question", "").lower()
        slug = m.get("slug", "").lower()
        if query in question or query in slug:
            matches.append(m)
            
    if not matches:
        print(f"\nNo active markets found containing '{query}'.")
        return
        
    print(f"\nFound {len(matches)} matching markets:\n")
    for m in matches:
        print(f"Title   : {m.get('question')}")
        print(f"Slug    : {m.get('slug')}")
        print(f"Volume  : ${float(m.get('volume', 0)):,.2f}")
        print("-" * 60)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Search Polymarket for specific market slugs.")
    parser.add_argument("query", type=str, help="Search keyword (e.g., NVDA, Bitcoin)")
    parser.add_argument("--proxy", type=str, default=None, help="Proxy URL (e.g., http://ip:port)")
    args = parser.parse_args()
    
    search_polymarket(args.query, args.proxy)

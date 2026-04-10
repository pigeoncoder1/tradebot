
import requests
import re
import json
from datetime import datetime, timedelta
import time
import sqlite3
import os

# --- Proxy logic ---
def load_proxies(filename="proxies.txt"):
    proxies = []
    with open(filename, "r") as f:
        for line in f:
            line = line.strip()
            if line:
                proxies.append(line)
    return proxies

def proxy_dict(proxy_str):
    # Format: user:pass@ip:port
    if '@' in proxy_str:
        auth, host = proxy_str.split('@')
        user, pwd = auth.split(':')
        ip, port = host.split(':')
        proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
    else:
        ip, port = proxy_str.split(':')
        proxy_url = f"http://{ip}:{port}"
    return {"http": proxy_url, "https": proxy_url}


# --- Proxy rotation state ---
_global_proxy_index = 0

def roli_request(url, *, headers=None, cookies=None, timeout=20, proxies_list=None, max_retries=1, **kwargs):
    global _global_proxy_index
    if proxies_list is None:
        proxies_list = [None]
    tries = 0
    num_proxies = len(proxies_list)
    start_idx = _global_proxy_index % num_proxies
    proxy_idx = start_idx
    while tries < max_retries:
        proxy = proxies_list[proxy_idx % num_proxies]
        proxy_cfg = proxy_dict(proxy) if proxy else None
        try:
            resp = requests.get(url, headers=headers, cookies=cookies, timeout=timeout, proxies=proxy_cfg, **kwargs)
            if resp.status_code == 429:
                print(f"Rate limited with proxy {proxy}. Switching proxy...")
                tries += 1
                proxy_idx += 1
                time.sleep(2)
                continue
            resp.raise_for_status()
            # Update global proxy index for next request
            _global_proxy_index = (proxy_idx + 1) % num_proxies
            return resp
        except requests.RequestException as e:
            print(f"Request error with proxy {proxy}: {e}. Switching proxy...")
            tries += 1
            proxy_idx += 1
            time.sleep(2)
    # Update global proxy index even on failure
    _global_proxy_index = (proxy_idx + 1) % num_proxies
    raise Exception(f"Failed to fetch {url} after {max_retries} attempts with proxies.")

cookies = {
    "_RoliVerification": os.environ.get("_RoliVerification"),
    "_RoliData": os.environ.get("_RoliData")
}
headers = {
    "User-Agent": "Mozilla/5.0"
}


def get_target_owner_ids(assetId: int, proxies) -> list[int]:
    url = f"https://www.rolimons.com/item/{assetId}"
    response = roli_request(url, headers=headers, cookies=cookies, timeout=20, proxies_list=proxies)

    match = re.search(r'var\s+bc_copies_data\s*=\s*(\{.*?\});', response.text, re.DOTALL)
    if not match:
        raise ValueError("Could not find bc_copies_data.")

    data = json.loads(match.group(1))

    owner_ids = data.get("owner_ids", [])
    bc_updated = data.get("bc_updated", [])

    now = datetime.utcnow()

    filtered = []

    for owner_id, updated_ms in zip(owner_ids, bc_updated):
        if not updated_ms:
            continue

        updated_time = datetime.utcfromtimestamp(updated_ms / 1000)
        age = now - updated_time

        # convert to days
        days = age.total_seconds() / 86400

        # 🎯 keep only 2–4 days OR 7–8 days
        if (2 <= days <= 4):
            filtered.append(owner_id)

    return filtered


def filter_users(owner_ids: list[int], proxies, user_limit=100) -> list[int]:
    valid_users = []
    conn = sqlite3.connect("storage/main.db")
    c = conn.cursor()
    # Create table if it doesn't exist
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY
        )
    """)
    conn.commit()
    # Get current user count in DB
    c.execute("SELECT COUNT(*) FROM users")
    current_count = c.fetchone()[0]
    if current_count >= user_limit:
        print(f"User limit of {user_limit} already reached. Skipping.")
        conn.close()
        return valid_users
    for user_id in owner_ids:
        # Check if we've hit the user limit
        c.execute("SELECT COUNT(*) FROM users")
        current_count = c.fetchone()[0]
        if current_count >= user_limit:
            print(f"User limit of {user_limit} reached. Stopping.")
            break
        try:
            # Check if user_id already exists in the database BEFORE making the API call
            c.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,))
            if c.fetchone():
                continue  # Skip API call if user already in DB

            url = f"https://api.rolimons.com/players/v1/playerassets/{user_id}"
            res = roli_request(url, headers=headers, timeout=15, proxies_list=proxies)

            data = res.json()
            if not data.get("success"):
                continue

            # Badge filter: skip if user has any of these badges
            badges = data.get("badges", {})
            skip_badges = [
                "own_100_items",
                "own_10_of_1_item",
                "value_500k"
            ]
            if any(badge in badges for badge in skip_badges):
                print(f"User {user_id} skipped due to badge filter")
                continue

            player_assets = data.get("playerAssets", {})

            if len(player_assets) < 16:
                c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
                conn.commit()
                valid_users.append(user_id)
                print(str(user_id) + " has been added to db")
            else:
                print(str(user_id) + " was not added to db")

            time.sleep(2)

        except Exception as e:
            print(f"Error with user {user_id}: {e}")
            continue

    conn.close()
    return valid_users


if __name__ == "__main__":
    assetIds = [583722932,1609390589,16477149823,564449640,928908332,19027209,1082932,1048037,583721561,113598419875472,162066057,2470750640,110673146052704,1213472762,398674411,10159610478,10159617728,327318670,128217885]
    proxies = load_proxies()
    if not proxies:
        print("No proxies loaded! Exiting.")
        exit(1)
    USER_LIMIT = 100
    for assetId in assetIds:
        owners = get_target_owner_ids(assetId, proxies)
        final_users = filter_users(owners, proxies, user_limit=USER_LIMIT)
        # Check if user limit reached after each assetId
        conn = sqlite3.connect("storage/main.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        current_count = c.fetchone()[0]
        conn.close()
        print(f"Stored {len(final_users)} users in the SQLite database (this run). Total in DB: {current_count}")
        if current_count >= USER_LIMIT:
            print(f"User limit of {USER_LIMIT} reached. Exiting.")
            break
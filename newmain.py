import sys
import os
import ast
import json
import math
import time
import sqlite3
import threading
import requests

from datetime import datetime
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from noob_finder import load_proxies, get_target_owner_ids, filter_users
from twofa import RobloxAuthenticator, Roblox2FAError
from trade_calc import calculate_trade, normalize_best_trade
from owned_items import find_and_calc_items

class TradeSendSkip(Exception):
    pass

DB_PATH = "main.db"
NUM_THREADS = 10
TRADE_SEND_URL = "https://trades.roblox.com/v2/trades/send"

BOT_ITEMS_CACHE = {}
BOT_ITEMS_CACHE_LOCK = threading.Lock()


def get_user_count(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(DISTINCT user_id) FROM users")
    count = cursor.fetchone()[0]
    conn.close()
    return count


def populate_users_for_bots(num_bots, target_users, asset_ids=None):
    if asset_ids is None:
        asset_ids = [
            583722932, 1609390589, 16477149823, 564449640, 928908332,
            19027209, 1082932, 1048037, 583721561, 113598419875472,
            162066057, 2470750640, 110673146052704, 1213472762,
            398674411, 10159610478, 10159617728, 327318670, 128217885,0
        ]

    proxies = load_proxies()
    if not proxies:
        print("No proxies loaded! Exiting.")
        sys.exit(1)

    print(f"[INFO] Populating users: target={target_users}")
    keep_finding = True
    while keep_finding:
        current_count = get_user_count(DB_PATH)
        if current_count >= target_users:
            print(f"[INFO] Target reached: {current_count} / {target_users}")
            return

        for asset_id in asset_ids:
            if asset_id == 0:
                keep_finding = False
                return
            current_count = get_user_count(DB_PATH)
            if current_count >= target_users:
                print(f"[INFO] Target reached: {current_count} / {target_users}")
                return

            try:
                owners = get_target_owner_ids(asset_id, proxies)
                filter_users(owners, proxies)

                current_count = get_user_count(DB_PATH)
                print(f"[INFO] Current user count: {current_count} / {target_users}")

                if current_count >= target_users:
                    print(f"[INFO] Target reached after asset {asset_id}: {current_count} / {target_users}")
                    return

            except Exception as e:
                print(f"[WARN] Failed asset {asset_id}: {e}")

        time.sleep(2)


def is_new_day(flag_file=".last_run_day"):
    today = datetime.utcnow().date()
    try:
        with open(flag_file, "r") as f:
            last_day = f.read().strip()
        if last_day == str(today):
            return False
    except Exception:
        pass

    with open(flag_file, "w") as f:
        f.write(str(today))
    return True


def proxy_dict(proxy_str: str) -> Optional[dict]:
    if not proxy_str:
        return None

    if '@' in proxy_str:
        auth, host = proxy_str.split('@')
        user, pwd = auth.split(':')
        ip, port = host.split(':')
        proxy_url = f"http://{user}:{pwd}@{ip}:{port}"
    else:
        ip, port = proxy_str.split(':')
        proxy_url = f"http://{ip}:{port}"

    return {"http": proxy_url, "https": proxy_url}


def proxy_request(url, *, headers=None, cookies=None, timeout=20, proxies_list=None, max_retries=10, **kwargs):
    if proxies_list is None or not proxies_list:
        proxies_list = [None]

    tries = 0
    proxy_idx = 0

    while tries < max_retries:
        proxy = proxies_list[proxy_idx % len(proxies_list)]
        proxy_cfg = proxy_dict(proxy) if proxy else None

        try:
            resp = requests.get(
                url,
                headers=headers,
                cookies=cookies,
                timeout=timeout,
                proxies=proxy_cfg,
                **kwargs
            )
            if resp.status_code in (403, 429):
                print(f"[WARN] {resp.status_code} for {url} with proxy {proxy}. Switching proxy...")
                tries += 1
                proxy_idx += 1
                time.sleep(0.5)
                continue

            resp.raise_for_status()
            return resp

        except requests.RequestException as e:
            print(f"[WARN] Request error with proxy {proxy}: {e}. Switching proxy...")
            tries += 1
            proxy_idx += 1
            time.sleep(0.5)

    raise Exception(f"Failed to fetch {url} after {max_retries} attempts with proxies.")


def get_all_users(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def load_bots(filename="bot_userids.txt"):
    bots = []
    with open(filename, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            bot = ast.literal_eval(line)
            bot["user_id"] = int(bot["user_id"])
            bots.append(bot)
    return bots


def build_session(roblosecurity_cookie):
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://www.roblox.com",
        "Referer": "https://www.roblox.com/",
    })
    session.cookies.set(".ROBLOSECURITY", roblosecurity_cookie, domain=".roblox.com")
    return session


def extract_instance_ids(items):
    ids = []
    for item in items:
        instance_id = item.get("collectibleItemInstanceId")
        if not instance_id:
            raise ValueError(f"Missing collectibleItemInstanceId in item: {item}")
        ids.append(instance_id)
    return ids


def extract_trade_sides(best_trade: dict):
    if not best_trade:
        raise ValueError("best_trade is empty")

    if "offer_items" in best_trade and "request_items" in best_trade:
        return best_trade["offer_items"], best_trade["request_items"]

    if "your_items" in best_trade and "their_items" in best_trade:
        return best_trade["your_items"], best_trade["their_items"]

    if "my_items" in best_trade and "their_items" in best_trade:
        return best_trade["my_items"], best_trade["their_items"]

    if "sender_items" in best_trade and "recipient_items" in best_trade:
        return best_trade["sender_items"], best_trade["recipient_items"]

    if "bot_items" in best_trade and "user_items" in best_trade:
        return best_trade["bot_items"], best_trade["user_items"]

    if "offer" in best_trade and "request" in best_trade:
        return best_trade["offer"], best_trade["request"]

    raise ValueError(f"Unsupported best_trade format. Keys found: {list(best_trade.keys())}")


def send_trade(
    session: requests.Session,
    authenticator: RobloxAuthenticator,
    sender_user_id: int,
    recipient_user_id: int,
    best_trade: dict,
):
    my_items, their_items = extract_trade_sides(best_trade)

    payload = {
        "senderOffer": {
            "userId": sender_user_id,
            "robux": 0,
            "collectibleItemInstanceIds": extract_instance_ids(my_items),
        },
        "recipientOffer": {
            "userId": recipient_user_id,
            "robux": 0,
            "collectibleItemInstanceIds": extract_instance_ids(their_items),
        }
    }

    resp = session.post(TRADE_SEND_URL, json=payload)

    token = authenticator._get_csrf(resp)
    if token:
        session.headers["X-CSRF-TOKEN"] = token

    if resp.status_code == 403 and not authenticator.is_challenge_required(resp):
        resp = session.post(TRADE_SEND_URL, json=payload)

    if authenticator.is_challenge_required(resp):
        print(f"[DEBUG] 2FA required for bot {sender_user_id}, solving...")
        ctx = authenticator.solve_challenge(session, resp)
        authenticator.apply_challenge_headers(session, ctx)
        resp = session.post(TRADE_SEND_URL, json=payload)

        token = authenticator._get_csrf(resp)
        if token:
            session.headers["X-CSRF-TOKEN"] = token

    if resp.status_code == 400:
        try:
            body = resp.json()
        except Exception:
            body = {}

        for err in body.get("errors", []):
            code = err.get("code")
            field = err.get("field")
            message = (err.get("message") or "").lower()

            if code == 22 and field == "recipient":
                raise TradeSendSkip(
                    f"recipient privacy too strict for user {recipient_user_id}"
                )

            if "privacy settings are too strict" in message:
                raise TradeSendSkip(
                    f"privacy settings blocked trade for user {recipient_user_id}"
                )

            if "trade partner is not available" in message:
                raise TradeSendSkip(
                    f"trade partner unavailable for user {recipient_user_id}"
                )

    if not resp.ok:
        raise RuntimeError(
            f"Trade send failed: status={resp.status_code}, body={resp.text}, payload={json.dumps(payload)}"
        )

    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


def preload_bot_items(bots):
    for bot in bots:
        bot_id = bot["user_id"]
        roblosecurity_cookie = bot["roblosecurity_cookie"]

        try:
            items = find_and_calc_items(roblosecurity_cookie, bot_id)
            with BOT_ITEMS_CACHE_LOCK:
                BOT_ITEMS_CACHE[bot_id] = items
            print(f"[DEBUG] Cached {len(items)} items for bot {bot_id}")
        except Exception as e:
            print(f"[ERROR] Failed loading items for bot {bot_id}: {e}")
            with BOT_ITEMS_CACHE_LOCK:
                BOT_ITEMS_CACHE[bot_id] = []


def get_cached_bot_items(bot_id):
    with BOT_ITEMS_CACHE_LOCK:
        return BOT_ITEMS_CACHE.get(bot_id, [])


def chunk_list(items, num_chunks):
    if not items:
        return []

    chunk_size = math.ceil(len(items) / num_chunks)
    return [items[i:i + chunk_size] for i in range(0, len(items), chunk_size)]


def load_user_items_with_any_bot_cookie(user_id, bots):
    """
    Fetch user items once per user.
    Tries bot cookies in order until one succeeds.
    """
    last_error = None

    for bot in bots:
        bot_id = bot["user_id"]
        cookie = bot["roblosecurity_cookie"]

        try:
            items = find_and_calc_items(cookie, user_id)
            return items
        except Exception as e:
            last_error = e
            print(f"[WARN] user={user_id} item fetch failed with bot={bot_id}: {e}")

    if last_error:
        print(f"[ERROR] user={user_id} failed item fetch with all bot cookies: {last_error}")

    return []


def process_user(user_id, bots):
    """
    Try bots in order for a single user.
    Stop after the first successful trade.
    """
    print(f"[USER_START] user={user_id}")

    # fetch once
    user_items = load_user_items_with_any_bot_cookie(user_id, bots)
    if not user_items:
        print(f"[SKIP] user={user_id} -> no user items")
        return None

    for bot in bots:
        bot_id = bot["user_id"]

        if user_id == bot_id:
            continue

        bot_items = get_cached_bot_items(bot_id)
        if not bot_items:
            print(f"[SKIP] bot={bot_id} user={user_id} -> bot has no cached items")
            continue

        print(f"[TRY] bot={bot_id} user={user_id}")

        try:
            best_trade = calculate_trade(bot_items, user_items)
        except Exception as e:
            print(f"[ERROR] bot={bot_id} user={user_id} -> calculate_trade failed: {e}")
            continue

        if not best_trade:
            print(f"[SKIP] bot={bot_id} user={user_id} -> no trade found")
            continue

        try:
            best_trade = normalize_best_trade(best_trade)
        except Exception as e:
            print(f"[ERROR] bot={bot_id} user={user_id} -> normalize_best_trade failed: {e}")
            continue

        if not best_trade:
            print(f"[SKIP] bot={bot_id} user={user_id} -> normalized trade is empty")
            continue

        roblosecurity_cookie = bot["roblosecurity_cookie"]
        otp_secret = bot["otp_secret"]

        session = build_session(roblosecurity_cookie)
        authenticator = RobloxAuthenticator(bot_id, otp_secret, roblosecurity_cookie)

        try:
            result = send_trade(
                session=session,
                authenticator=authenticator,
                sender_user_id=bot_id,
                recipient_user_id=user_id,
                best_trade=best_trade,
            )
            print(f"[SENT] bot={bot_id} user={user_id} -> {result}")
            print(f"[USER_DONE] user={user_id} handled by bot={bot_id}")
            return result

        except TradeSendSkip as e:
            print(f"[SKIP] bot={bot_id} user={user_id} -> {e}")
            continue
        except Roblox2FAError as e:
            print(f"[ERROR] bot={bot_id} user={user_id} -> 2FA failed: {e}")
            continue
        except Exception as e:
            print(f"[ERROR] bot={bot_id} user={user_id} -> send_trade failed: {e}")
            continue

    print(f"[USER_DONE] user={user_id} -> no bot could send")
    return None


def worker(thread_id, user_chunk, bots):
    print(f"[THREAD_START] thread={thread_id} users={len(user_chunk)}")

    sent_count = 0

    for user_id in user_chunk:
        try:
            result = process_user(user_id, bots)
            if result is not None:
                sent_count += 1
        except Exception as e:
            print(f"[ERROR] thread={thread_id} user={user_id} -> unhandled: {e}")

    print(f"[THREAD_DONE] thread={thread_id} sent_count={sent_count}")
    return sent_count


def run_once():
    bots = load_bots()
    if not bots:
        print("[ERROR] No bots loaded")
        return

    if is_new_day():
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM users")
        conn.commit()
        conn.close()
        print("All records deleted from 'users' table.")

        target_users = len(bots) * 100
        print(f"[INFO] New day detected. Ensuring at least {target_users} users in main.db...")
        populate_users_for_bots(len(bots), target_users)
    else:
        print("[INFO] Not a new day, skipping user population.")

    users = get_all_users(DB_PATH)
    if not users:
        print("[ERROR] No users loaded")
        return

    print(f"[DEBUG] Loaded {len(bots)} bots")
    print(f"[DEBUG] Loaded {len(users)} users")

    print("[DEBUG] Preloading bot inventories...")
    preload_bot_items(bots)

    user_chunks = chunk_list(users, NUM_THREADS)
    print(f"[DEBUG] Split users into {len(user_chunks)} chunks")

    total_sent = 0

    with ThreadPoolExecutor(max_workers=NUM_THREADS) as executor:
        futures = {
            executor.submit(worker, idx + 1, chunk, bots): idx + 1
            for idx, chunk in enumerate(user_chunks)
            if chunk
        }

        for future in as_completed(futures):
            thread_id = futures[future]
            try:
                sent_count = future.result()
                total_sent += sent_count
                print(f"[DEBUG] thread={thread_id} finished with sent_count={sent_count}")
            except Exception as e:
                print(f"[ERROR] thread={thread_id} crashed: {e}")

    print(f"[DONE] Total trades sent this run: {total_sent}")

def main():
    while True:
        try:
            print("[INFO] Starting run_once()")
            run_once()
            print("[INFO] Run finished. Sleeping for 24 hours...")
        except Exception as e:
            print(f"[FATAL] Top-level crash: {e}")

        time.sleep(24 * 60 * 60)

if __name__ == "__main__":
    main()
import itertools
import threading

# Proxy manager
class ProxyRotator:
    def __init__(self, proxy_file="proxies.txt"):
        with open(proxy_file, "r") as f:
            self.proxies = [line.strip() for line in f if line.strip()]
        self.lock = threading.Lock()
        self.proxy_cycle = itertools.cycle(self.proxies)
        self.current_proxy = next(self.proxy_cycle)

    def get_next_proxy(self):
        with self.lock:
            self.current_proxy = next(self.proxy_cycle)
            return self.current_proxy

    def get_current_proxy(self):
        with self.lock:
            return self.current_proxy

def proxy_to_requests(proxy):
    # Format: user:pass@host:port
    if '@' in proxy:
        creds, hostport = proxy.split('@')
        user, pwd = creds.split(':')
        host, port = hostport.split(':')
        proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    else:
        host, port = proxy.split(':')
        proxy_url = f"http://{host}:{port}"
    return {"http": proxy_url, "https": proxy_url}

proxy_rotator = ProxyRotator()

import os
import requests

from op_calc import estimate_from_item
from item_filter import get_rolimons_item_data, get_filtered_collectibles




roli_cookies = {
    "_RoliVerification": os.environ.get("_RoliVerification"),
    "_RoliData": os.environ.get("_RoliData"),
}

roli_session = requests.Session()
roli_session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "text/html,application/xhtml+xml"
})
roli_session.cookies.update(roli_cookies)





def get_collectibles(user_id: int, roblosecurity_cookie: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    })
    session.cookies.set(".ROBLOSECURITY", roblosecurity_cookie, domain=".roblox.com")
    url = f"https://inventory.roblox.com/v1/users/{user_id}/assets/collectibles"
    collectibles = []
    cursor = None
    while True:
        params = {
            "limit": 100,
            "sortOrder": "Asc"
        }
        if cursor:
            params["cursor"] = cursor
        while True:
            proxy = proxy_rotator.get_current_proxy()
            proxies = proxy_to_requests(proxy)
            try:
                response = session.get(url, params=params, timeout=20, proxies=proxies)
                if response.status_code == 429:
                    print(f"Rate limited on proxy {proxy}, switching...")
                    proxy_rotator.get_next_proxy()
                    continue
                response.raise_for_status()
                break
            except (requests.exceptions.ProxyError, requests.exceptions.ConnectionError, requests.exceptions.Timeout):
                print(f"Proxy error with {proxy}, switching...")
                proxy_rotator.get_next_proxy()
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    print(f"Rate limited (HTTP 429) on proxy {proxy}, switching...")
                    proxy_rotator.get_next_proxy()
                    continue
                raise
        data = response.json()
        for item in data.get("data", []):
            if item.get("isOnHold") is True:
                continue
            collectibles.append(item)
        cursor = data.get("nextPageCursor")
        if not cursor:
            break
    return collectibles


def normalize_item_for_calc(collectible: dict, roli_data: dict) -> dict:
    details = roli_data["item_details"]

    return {
        "collectibleItemInstanceId": collectible["collectibleItemInstanceId"],
        "assetId": details["item_id"],
        "name": details["item_name"],
        "recentAveragePrice": details["rap"],
        "rolimons_best_price": details["best_price"],
        "rolimons_value": details["value"],
        "avg_daily_sales_volume_30_days": roli_data["avg_daily_sales_volume_30_days"],
    }


def get_trade_value(result: dict) -> float:
    if result.get("mode") == "null_value":
        return float(result["quick_value"])
    return float(result["recovery_value"])

def normalize_calc_result(limiteds: list) -> list:
    normalized_limiteds = []

    for limited in limiteds:
        result = limited.get("result", {})

        actual_value = result.get("actual_value")
        your_price = result.get("your_price")
        their_price = result.get("their_price")

        if actual_value is None or your_price is None or their_price is None:
            continue

        normalized_limited = {
            "collectibleItemInstanceId": limited.get("collectibleItemInstanceId"),
            "assetId": result.get("assetId"),
            "item_name": result.get("item_name"),
            "actual_value": actual_value,
            "your_price": your_price,
            "their_price": their_price,
        }
        normalized_limiteds.append(normalized_limited)

    return normalized_limiteds


def find_and_calc_items(roblosecurity_cookie, user_id):
    bot_userids = set()

    with open("bot_userids.txt", "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                bot = eval(line)
                bot_userids.add(int(bot["user_id"]))
            except Exception:
                continue

    if int(user_id) in bot_userids:
        limiteds = get_filtered_collectibles(user_id, roblosecurity_cookie, True)
    else:
        limiteds = get_filtered_collectibles(user_id, roblosecurity_cookie, False)

    op_values = []

    for limited in limiteds:
        try:
            while True:
                proxy = proxy_rotator.get_current_proxy()
                try:
                    item = get_rolimons_item_data(
                        limited.get("assetId"),
                    )
                    break
                except (requests.exceptions.ProxyError,
                        requests.exceptions.ConnectionError,
                        requests.exceptions.Timeout):
                    print(f"Proxy error with {proxy} (rolimons), switching...")
                    proxy_rotator.get_next_proxy()
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429:
                        print(f"Rate limited (HTTP 429) on proxy {proxy} (rolimons), switching...")
                        proxy_rotator.get_next_proxy()
                        continue
                    raise

            result = estimate_from_item(item)

            op_values.append({
                "result": result,
                "collectibleItemInstanceId": limited.get("collectibleItemInstanceId")
            })

        except Exception as e:
            print(f"[WARN] Failed to calculate asset {limited.get('assetId')}: {e}")
            continue

    return normalize_calc_result(op_values)

if __name__ == "__main__":
    print(find_and_calc_items(os.environ.get("ROBLOSECURITY"),1753811655))
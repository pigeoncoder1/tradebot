import itertools
import threading
import requests
import re
import json
import time
from typing import Optional
import os

from proxymanager import request_with_rotating_proxies
from noob_finder import load_proxies, proxy_dict

roli_cookies = {
    "_RoliVerification": os.environ.get("_RoliVerification"),
    "_RoliData": os.environ.get("_RoliData")
}


def get_collectibles(user_id: int, roblosecurity_cookie: str) -> list[dict]:
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json"
    })
    session.cookies.set(".ROBLOSECURITY", roblosecurity_cookie, domain=".roblox.com")

    url = f"https://trades.roblox.com/v2/users/{user_id}/tradableItems"
    collectibles = []
    cursor = None

    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor

        response = request_with_rotating_proxies(
            session,
            "GET",
            url,
            params=params,
            timeout=20,
            max_proxy_switches=10,
            retries_per_proxy=2,
            retry_on_status=(429,),
        )

        if response.status_code == 401:
            print(f"[WARN] 401 Unauthorized for user {user_id}.")
            return []

        data = response.json()

        for item in data.get("items", []):
            item_target = item.get("itemTarget", {})
            if item_target.get("itemType") == "Bundle":
                continue

            item_name = item.get("itemName")
            asset_id_raw = item_target.get("targetId")

            try:
                asset_id = int(asset_id_raw) if asset_id_raw is not None else None
            except (TypeError, ValueError):
                asset_id = None

            for instance in item.get("instances", []):
                collectibles.append({
                    "collectibleItemInstanceId": instance.get("collectibleItemInstanceId"),
                    "assetId": asset_id,
                    "itemName": instance.get("itemName") or item_name,
                    "serialNumber": instance.get("serialNumber"),
                    "originalPrice": instance.get("originalPrice"),
                    "recentAveragePrice": instance.get("recentAveragePrice") or item.get("recentAveragePrice"),
                    "assetStock": instance.get("assetStock") or item.get("assetStock"),
                    "isOnHold": instance.get("isOnHold")
                })

        cursor = data.get("nextPageCursor")
        if not cursor:
            break

    return collectibles


def get_rolimons_item_data(asset_id: int, session: requests.Session) -> Optional[dict]:
    url = f"https://www.rolimons.com/item/{asset_id}"

    response = request_with_rotating_proxies(
        session,
        "GET",
        url,
        timeout=20,
        max_proxy_switches=10,
        retries_per_proxy=2,
        retry_on_status=(403, 429),   # add 403 here
    )

    response.raise_for_status()       # fail immediately on bad final response

    html = response.text

    details_match = re.search(
        r'var\s+item_details_data\s*=\s*(\{.*?\});',
        html,
        re.DOTALL
    )
    if not details_match:
        return None

    volume_match = re.search(
        r'var\s+avg_daily_sales_volume_30_days\s*=\s*([0-9.]+);',
        html
    )
    if not volume_match:
        return None

    item_details = json.loads(details_match.group(1))
    avg_daily_sales_volume_30_days = float(volume_match.group(1))

    # Calculate days since first_timestamp if present
    first_timestamp = item_details.get("first_timestamp")
    days_since_first_timestamp = None
    if first_timestamp is not None:
        try:
            # first_timestamp is assumed to be a Unix timestamp (seconds)
            now = int(time.time())
            days_since_first_timestamp = (now - int(first_timestamp)) // 86400
        except Exception:
            days_since_first_timestamp = None

    return {
        "item_details": item_details,
        "avg_daily_sales_volume_30_days": avg_daily_sales_volume_30_days,
        "days_since_first_timestamp": days_since_first_timestamp
    }

def passes_filters(item: dict, roli_data: dict) -> bool:
    try:
        with open("blacklist.txt", "r") as f:
            blacklist = set(line.strip() for line in f if line.strip())
    except FileNotFoundError:
        blacklist = set()

    asset_id = item.get("assetId")
    if asset_id is None:
        return False

    asset_id_str = str(asset_id)
    if asset_id_str in blacklist:
        return False

    if item.get("isOnHold") is True:
        return False

    item_details = roli_data["item_details"]
    avg_daily_sales_volume_30_days = roli_data["avg_daily_sales_volume_30_days"]
    days_since_first_timestamp = roli_data["days_since_first_timestamp"]

    projected = item_details.get("projected")
    best_price = item_details.get("best_price")
    rap = item_details.get("rap")
    value = item_details.get("value")
    if days_since_first_timestamp < 10:
        return False
    if projected == 1:
        return False

    if best_price is None or rap is None:
        return False

    if best_price < rap * 0.96:
        return False

    if rap < 1000:
        return avg_daily_sales_volume_30_days >= 1.0

    if 1000 <= rap < 2000:
        return avg_daily_sales_volume_30_days >= 0.8

    if 2000 <= rap < 8000:
        return avg_daily_sales_volume_30_days >= 0.8

    if rap >= 8000:
        return value is not None and avg_daily_sales_volume_30_days >= 0.8

    return False


def get_filtered_collectibles(user_id: int, roblosecurity_cookie: str, isowner: bool) -> list[dict]:
    try:
        inventory_items = get_collectibles(user_id, roblosecurity_cookie)
    except requests.exceptions.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status in (401, 403):
            print(f"User {user_id} tradable inventory unavailable (status {status}). Skipping.")
            return []
        raise
    except Exception as e:
        if "Failed to fetch tradableItems" in str(e):
            print(f"User {user_id} tradable inventory unavailable after proxy retries. Skipping.")
            return []
        raise

    roli_session = requests.Session()
    roli_session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml"
    })
    roli_session.cookies.update(roli_cookies)

    filtered_items = []

    for item in inventory_items:
        assetId = item.get("assetId")
        if assetId is None:
            continue
        if item.get("isOnHold") is True:
            continue

        try:
            roli_data = get_rolimons_item_data(assetId, roli_session)
            print(roli_data)
            if roli_data is None:
                continue

            if passes_filters(item, roli_data) or isowner:
                filtered_items.append(item)

            time.sleep(1)

        except Exception as e:
            print(f"Error checking asset {assetId}: {e}")
            continue

    return filtered_items


if __name__ == "__main__":
    user_id = 717414458
    filtered_items = get_filtered_collectibles(
        user_id,
        os.environ["ROBLOSECURITY"],
        True
    )
    print(filtered_items)
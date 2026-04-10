import os
import json
import re
import requests
import numpy as np

from item_filter import (
    get_rolimons_item_data
)

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


def get_item_sales_data(assetId: int, cookies: dict | None = None) -> dict:
    url = f"https://www.rolimons.com/itemsales/{assetId}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    from noob_finder import load_proxies, proxy_dict
    proxies_list = load_proxies()
    max_retries = 1
    tries = 0
    proxy_idx = 0
    while tries < max_retries:
        proxy = proxies_list[proxy_idx % len(proxies_list)] if proxies_list else None
        proxy_cfg = proxy_dict(proxy) if proxy else None
        try:
            response = requests.get(url, headers=headers, cookies=cookies, timeout=20, proxies=proxy_cfg)
            if response.status_code in (403, 429):
                print(f"[WARN] {response.status_code} with proxy {proxy}. Switching proxy...")
                tries += 1
                proxy_idx += 1
                import time; time.sleep(1)
                continue
            response.raise_for_status()
            html = response.text
            match = re.search(r'var\s+item_sales\s*=\s*(\{.*?\});', html, re.DOTALL)
            if not match:
                raise ValueError("Could not find item_sales data")
            return json.loads(match.group(1))
        except requests.RequestException as e:
            print(f"Request error with proxy {proxy}: {e}. Switching proxy...")
            tries += 1
            proxy_idx += 1
            import time; time.sleep(1)
    raise Exception(f"Failed to fetch {url} after {max_retries} attempts with proxies.")


def get_recent_n_sales(
    sale_prices: list[int],
    timestamps: list[int],
    n: int = 200
) -> list[int]:
    if len(sale_prices) != len(timestamps):
        raise ValueError("sale_prices and timestamps must match in length")

    if n <= 0:
        return []

    pairs = sorted(zip(timestamps, sale_prices), key=lambda x: x[0])
    return [price for _, price in pairs[-n:]]


def clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def round_to_1(x: float) -> int:
    return int(round(x))


def round_to_5(x: float) -> int:
    return int(round(x / 5.0) * 5)


def round_to_10(x: float) -> int:
    return int(round(x / 10.0) * 10)


def round_to_50(x: float) -> int:
    return int(round(x / 50.0) * 50)


def round_price(x: float) -> int:
    if x < 100:
        return round_to_1(x)
    if x < 500:
        return round_to_5(x)
    if x < 5000:
        return round_to_10(x)
    return round_to_50(x)


def trim_extreme_outliers(x: np.ndarray) -> np.ndarray:
    if len(x) < 10:
        return x

    q1 = np.percentile(x, 25)
    q3 = np.percentile(x, 75)
    iqr = q3 - q1

    lower_bound = q1 - 2.5 * iqr
    upper_bound = q3 + 2.5 * iqr

    trimmed = x[(x >= lower_bound) & (x <= upper_bound)]

    if len(trimmed) < max(8, int(len(x) * 0.5)):
        lo = np.percentile(x, 1)
        hi = np.percentile(x, 99)
        trimmed = x[(x >= lo) & (x <= hi)]

    return trimmed if len(trimmed) >= 4 else x


def two_cluster_split(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centers = np.array([
        np.percentile(x, 25),
        np.percentile(x, 75),
    ], dtype=float)

    labels = np.zeros(len(x), dtype=int)

    for _ in range(50):
        distances = np.abs(x[:, None] - centers[None, :])
        labels = np.argmin(distances, axis=1)

        new_centers = np.array([
            x[labels == i].mean() if np.any(labels == i) else centers[i]
            for i in range(2)
        ])

        if np.allclose(new_centers, centers):
            break

        centers = new_centers

    low_idx = int(np.argmin(centers))
    high_idx = int(np.argmax(centers))

    low_cluster = x[labels == low_idx]
    high_cluster = x[labels == high_idx]

    return low_cluster, high_cluster


def add_trade_prices(result: dict, actual_value: float) -> dict:
    result["actual_value"] = round_price(actual_value)
    result["your_price"] = round_price(actual_value * 1.03)
    result["their_price"] = round_price(actual_value * 0.97)
    return result


def estimate_null_item_levels(
    x: np.ndarray,
    rap: float,
    best_price: float | None
) -> dict:
    median_price = float(np.median(x))
    q25 = float(np.percentile(x, 25))
    q75 = float(np.percentile(x, 75))
    q90 = float(np.percentile(x, 90))
    q10 = float(np.percentile(x, 10))

    volatility_pct = max(0.0, (q90 - q10) / max(median_price, 1.0))

    if best_price is None:
        best_price = median_price

    best_gap = max(0.0, rap - best_price)
    sales_gap = max(0.0, rap - median_price)
    rap_drift = (best_gap * 0.45) + (sales_gap * 0.25)

    quick_value = (
        0.70 * (rap - rap_drift) +
        0.20 * median_price +
        0.10 * q75
    )

    quick_value = clamp(quick_value, q25, max(rap, q90))

    result = {
        "mode": "null_value",
        "median_price": round(median_price, 2),
        "q25": round(q25, 2),
        "q75": round(q75, 2),
        "q90": round(q90, 2),
        "rap_used": round(rap, 2),
        "best_price_used": round(best_price, 2),
        "best_gap_below_rap": round(best_gap, 2),
        "sales_gap_below_rap": round(sales_gap, 2),
        "estimated_rap_drift": round(rap_drift, 2),
        "quick_value": round(quick_value, 2),
        "volatility_pct": round(volatility_pct * 100, 2),
    }

    return add_trade_prices(result, quick_value)


def estimate_valued_item_levels(
    x: np.ndarray,
    rap: float,
    roli_value: float,
    best_price: float | None
) -> dict:
    low_cluster, high_cluster = two_cluster_split(x)

    min_cluster_size = max(3, int(len(x) * 0.08))
    if len(high_cluster) < min_cluster_size:
        healthy_median = float(np.median(x))
        healthy_q75 = float(np.percentile(x, 75))
        overall_q85 = float(np.percentile(x, 85))
        overall_q15 = float(np.percentile(x, 15))
        low_center = float(np.mean(x))
        high_center = float(np.mean(x))
        lpp_gap_pct = 0.0
        low_count = 0
        high_count = 0
    else:
        low_center = float(np.mean(low_cluster))
        high_center = float(np.mean(high_cluster))
        healthy_median = float(np.median(high_cluster))
        healthy_q75 = float(np.percentile(high_cluster, 75))
        overall_q85 = float(np.percentile(x, 85))
        overall_q15 = float(np.percentile(x, 15))
        lpp_gap_pct = max(0.0, (high_center - low_center) / max(high_center, 1.0))
        low_count = int(len(low_cluster))
        high_count = int(len(high_cluster))

    current_liquidity_value = healthy_median
    volatility_pct = max(0.0, (overall_q85 - overall_q15) / max(healthy_median, 1.0))
    value_discount_pct = max(0.0, (roli_value - healthy_median) / max(roli_value, 1.0))

    market_weight = clamp(0.55 + (lpp_gap_pct * 0.35), 0.55, 0.80)
    value_weight = 1.0 - market_weight

    market_recovery = (
        0.55 * healthy_median +
        0.25 * healthy_q75 +
        0.20 * overall_q85
    )

    actual_value = (market_weight * market_recovery) + (value_weight * roli_value)

    if best_price is not None:
        actual_value = (0.90 * actual_value) + (0.10 * float(best_price))

    result = {
        "mode": "valued_item",
        "low_cluster_center": round(low_center, 2),
        "high_cluster_center": round(high_center, 2),
        "low_cluster_count": low_count,
        "high_cluster_count": high_count,
        "current_liquidity_value": round(current_liquidity_value, 2),
        "market_recovery": round(market_recovery, 2),
        "rap_used": round(rap, 2),
        "roli_value_used": round(roli_value, 2),
        "best_price_used": round(best_price, 2) if best_price is not None else None,
        "lpp_gap_pct": round(lpp_gap_pct * 100, 2),
        "value_discount_pct": round(value_discount_pct * 100, 2),
        "volatility_pct": round(volatility_pct * 100, 2),
        "market_weight": round(market_weight, 4),
        "value_weight": round(value_weight, 4),
    }

    return add_trade_prices(result, actual_value)


def estimate_item_levels(
    prices: list[int],
    item_info: dict,
    sale_rap_list: list[int] | None = None,
) -> dict:
    if len(prices) < 4:
        raise ValueError(f"Need at least 4 sale prices to estimate levels, got {len(prices)}")

    raw_x = np.array(prices, dtype=float)
    x = trim_extreme_outliers(raw_x)

    rap = item_info.get("recentAveragePrice")
    if rap is None and sale_rap_list:
        rap = sale_rap_list[-1]
    if rap is None:
        rap = int(round(np.median(x)))

    roli_value = item_info["item_details"]["value"]
    best_price = item_info["item_details"]["best_price"]

    if roli_value is None:
        result = estimate_null_item_levels(
            x,
            float(rap),
            None if best_price is None else float(best_price)
        )
    else:
        result = estimate_valued_item_levels(
            x=x,
            rap=float(rap),
            roli_value=float(roli_value),
            best_price=None if best_price is None else float(best_price)
        )

    result["raw_sales_count"] = int(len(raw_x))
    result["sales_used_count"] = int(len(x))
    return result


def estimate_from_item(item_info: dict, sales_count: int = 200) -> dict:
    assetId = item_info["item_details"]["item_id"]
    item_sales = get_item_sales_data(assetId, roli_cookies)

    sale_prices = item_sales.get("sale_price_list", [])
    timestamps = item_sales.get("timestamp_list", [])
    sale_rap_list = item_sales.get("sale_rap_list", [])

    num_sales = item_info["avg_daily_sales_volume_30_days"]

    if 1 < num_sales < 2:
        n=20
    elif 2 < num_sales < 4:
        n=40
    elif 4 < num_sales < 10:
        n=80
    elif 10 < num_sales < 25:
        n=120
    else:
        n=200
    recent_sales = get_recent_n_sales(sale_prices, timestamps, n=n)

    if len(recent_sales) < 4:
        raise ValueError(
            f"Need at least 4 sale prices to estimate levels, got {len(recent_sales)}"
        )

    result = estimate_item_levels(
        prices=recent_sales,
        item_info=item_info,
        sale_rap_list=sale_rap_list,
    )

    return {
        "item_name": item_info.get("name"),
        "assetId": assetId,
        "recent_sales_used": recent_sales[-25:],
        "sales_sample_size": len(recent_sales),
        **result
    }


if __name__ == "__main__":
    item = get_rolimons_item_data(151786902, roli_session)
    print(item)
    result = estimate_from_item(item)
    print(result)
from itertools import combinations


def filter_duplicate_items(my_items: list[dict], their_items: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Removes items that are present on both sides (by assetId), unless one side only has one item.
    If either side has only one item, skip removal and return as-is.
    """
    if len(my_items) == 1 or len(their_items) == 1:
        return my_items, their_items

    my_asset_ids = {item.get("assetId") for item in my_items}
    their_asset_ids = {item.get("assetId") for item in their_items}
    duplicates = my_asset_ids & their_asset_ids
    if not duplicates:
        return my_items, their_items

    filtered_my_items = [item for item in my_items if item.get("assetId") not in duplicates]
    filtered_their_items = [item for item in their_items if item.get("assetId") not in duplicates]
    return filtered_my_items, filtered_their_items

def get_my_side_value(item: dict) -> float:
    if "your_price" in item:
        return float(item["your_price"])
    raise ValueError(f"My item missing 'your_price': {item}")


def get_their_side_value(item: dict) -> float:
    if "their_price" in item:
        return float(item["their_price"])
    raise ValueError(f"Their item missing 'their_price': {item}")

def normalize_best_trade(best_trade: dict) -> dict:
    if not best_trade:
        raise ValueError("best_trade is empty")

    keys = set(best_trade.keys())

    # Current format
    if {"offer_items", "request_items", "offer_total", "request_total"}.issubset(keys):
        return {
            "trade_type": best_trade.get("trade_type"),
            "offer_items": best_trade.get("offer_items", []),
            "request_items": best_trade.get("request_items", []),
            "offer_total": best_trade.get("offer_total", 0),
            "request_total": best_trade.get("request_total", 0),
            "overpay_pct": best_trade.get("overpay_pct", 0),
            "score": best_trade.get("score", 0),
        }

    # Older format
    if {"my_items", "their_items", "my_total", "their_total"}.issubset(keys):
        return {
            "trade_type": best_trade.get("trade_type"),
            "offer_items": best_trade.get("my_items", []),
            "request_items": best_trade.get("their_items", []),
            "offer_total": best_trade.get("my_total", 0),
            "request_total": best_trade.get("their_total", 0),
            "overpay_pct": best_trade.get("overpay_pct", 0),
            "score": best_trade.get("score", 0),
        }

    raise ValueError(f"Unsupported best_trade format. Keys found: {list(best_trade.keys())}")

def sum_my_items(items: list[dict]) -> float:
    return sum(get_my_side_value(item) for item in items)


def sum_their_items(items: list[dict]) -> float:
    return sum(get_their_side_value(item) for item in items)


def trade_type(my_items: list[dict], their_items: list[dict]) -> str:
    if len(my_items) > len(their_items):
        return "upgrade"
    if len(my_items) < len(their_items):
        return "downgrade"
    return "neutral"


def is_valid_trade(my_items: list[dict], their_items: list[dict]) -> tuple[bool, dict]:
    my_total = sum_my_items(my_items)
    their_total = sum_their_items(their_items)

    if my_total <= 0 or their_total <= 0:
        return False, {}

    t_type = trade_type(my_items, their_items)

    meta = {
        "trade_type": t_type,
        "my_total": my_total,
        "their_total": their_total,
    }

    # Upgrade: my 2/3/4 for their 1
    if t_type == "upgrade" and len(their_items) == 1 and len(my_items) in (2, 3, 4):
        overpay_pct = (my_total - their_total) / their_total
        meta["overpay_pct"] = overpay_pct

        # Only accept realistic sendable upgrades
        if 0.00 <= overpay_pct <= 0.04:
            return True, meta
        return False, meta

    # Downgrade: my 1 for their 2/3/4
    if t_type == "downgrade" and len(my_items) == 1 and len(their_items) in (2, 3, 4):
        overpay_pct = (their_total - my_total) / my_total
        meta["overpay_pct"] = overpay_pct

        # realistic target windows
        target_ranges = {
            2: (0.08, 0.13),
            3: (0.09, 0.14),
            4: (0.10, 0.15),
        }

        low, high = target_ranges[len(their_items)]
        meta["target_low"] = low
        meta["target_high"] = high

        if low <= overpay_pct <= high:
            return True, meta
        return False, meta

    return False, meta


def score_trade(my_items: list[dict], their_items: list[dict], meta: dict) -> float:
    score = 0.0
    t_type = meta["trade_type"]
    overpay_pct = meta.get("overpay_pct", 0.0)

    if t_type == "upgrade":
        target = 0.04
        score += 100 - abs(overpay_pct - target) * 1000

    elif t_type == "downgrade":
        if len(their_items) == 2:
            target = 0.09
        else:
            target = 0.10
        score += 100 - abs(overpay_pct - target) * 1000

    total_items = len(my_items) + len(their_items)
    score -= total_items * 3

    return score


def make_trade_object(my_items: list[dict], their_items: list[dict], meta: dict, score: float) -> dict:
    return {
        "trade_type": meta["trade_type"],
        "my_items": my_items,
        "their_items": their_items,
        "my_total": round(meta["my_total"], 2),
        "their_total": round(meta["their_total"], 2),
        "overpay_pct": round(meta.get("overpay_pct", 0.0) * 100, 2),
        "score": round(score, 2),
    }


def calculate_trade(my_items: list[dict], their_items: list[dict]) -> dict | None:
    best_trade = None
    best_score = float("-inf")

    # Upgrades: my 2/3/4 for their 1
    for their_item in their_items:
        for r in (2, 3, 4):
            if len(my_items) < r:
                continue

            for my_combo in combinations(my_items, r):
                filtered_my, filtered_their = filter_duplicate_items(list(my_combo), [their_item])
                # If after filtering, one side is empty, skip
                if not filtered_my or not filtered_their:
                    continue
                valid, meta = is_valid_trade(filtered_my, filtered_their)
                if not valid:
                    continue

                score = score_trade(filtered_my, filtered_their, meta)
                if score > best_score:
                    best_score = score
                    best_trade = make_trade_object(filtered_my, filtered_their, meta, score)

    # Downgrades: my 1 for their 2/3/4
    for my_item in my_items:
        for r in (2, 3, 4):
            if len(their_items) < r:
                continue

            for their_combo in combinations(their_items, r):
                filtered_my, filtered_their = filter_duplicate_items([my_item], list(their_combo))
                if not filtered_my or not filtered_their:
                    continue
                valid, meta = is_valid_trade(filtered_my, filtered_their)
                if not valid:
                    continue

                score = score_trade(filtered_my, filtered_their, meta)
                if score > best_score:
                    best_score = score
                    best_trade = make_trade_object(filtered_my, filtered_their, meta, score)

    return best_trade
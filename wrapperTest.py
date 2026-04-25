import sys
sys.path.insert(0, r"c:\Users\rcart\tradebot\rolimons_fork\rolimons.py")
import rolimons
item = rolimons.item(3798248888)
sales_data = item.get_sales_data()

import time

def find_average_sales(sales):
    timestamps = []
    for sale in sales:
        ts = sale.timestamp
        if ts:
            try:
                timestamps.append(ts)
            except Exception:
                pass

    if not timestamps:
        print("No timestamps found.")
        exit(1)

    # Sort timestamps descending (most recent first)
    timestamps.sort(reverse=True)

    # Get the current time (assume timestamps are in seconds)
    now = int(time.time())
    days_ago_30 = now - 30 * 86400

    # Count sales in the last 30 days
    sales_last_30_days = [ts for ts in timestamps if ts >= days_ago_30]
    num_sales = len(sales_last_30_days)
    avg_sales_per_day = num_sales / 30

    return avg_sales_per_day
from noob_finder import load_proxies, proxy_dict
import requests
import time
import threading
import itertools


class ProxyManager:
    def __init__(self):
        self.proxies = load_proxies()
        if not self.proxies:
            raise ValueError("No proxies loaded")
        self.lock = threading.Lock()
        self.cycle = itertools.cycle(self.proxies)

    def next_proxy(self):
        with self.lock:
            return next(self.cycle)


proxy_manager = ProxyManager()


def request_with_rotating_proxies(
    session: requests.Session,
    method: str,
    url: str,
    *,
    params=None,
    timeout=20,
    max_proxy_switches=3,
    retries_per_proxy=1,
    retry_on_status=(429,),
):
    """
    Total attempts per proxy = 1 + retries_per_proxy.
    After that, rotate to next proxy.
    """
    last_error = None

    for proxy_round in range(max_proxy_switches):
        proxy = proxy_manager.next_proxy()
        proxies = proxy_dict(proxy) if proxy else None

        for attempt in range(retries_per_proxy + 1):
            try:
                print(
                    f"[DEBUG] {method} {url} "
                    f"proxy={proxy} attempt={attempt+1}/{retries_per_proxy+1} "
                    f"proxy_round={proxy_round+1}/{max_proxy_switches}"
                )

                response = session.request(
                    method,
                    url,
                    params=params,
                    timeout=timeout,
                    proxies=proxies,
                )

                if response.status_code in retry_on_status:
                    last_error = RuntimeError(
                        f"Retryable status {response.status_code} on proxy {proxy}"
                    )
                    if attempt < retries_per_proxy:
                        print(f"[WARN] Status {response.status_code} on {proxy}, retrying same proxy...")
                        time.sleep(1)
                        continue

                    print(f"[WARN] Status {response.status_code} on {proxy}, rotating proxy...")
                    break

                response.raise_for_status()
                return response

            except (
                requests.exceptions.ProxyError,
                requests.exceptions.ConnectionError,
                requests.exceptions.Timeout,
                requests.exceptions.SSLError,
            ) as e:
                last_error = e
                if attempt < retries_per_proxy:
                    print(f"[WARN] Proxy/network error on {proxy}: {e}. Retrying same proxy...")
                    time.sleep(1)
                    continue

                print(f"[WARN] Proxy/network error on {proxy}: {e}. Rotating proxy...")
                break

            except requests.exceptions.HTTPError as e:
                last_error = e
                raise

    raise Exception(f"Request failed after rotating proxies. Last error: {last_error}")
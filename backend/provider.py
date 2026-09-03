from abc import ABC, abstractmethod
import requests
import time
import random

class GSTDataProvider(ABC):
    @abstractmethod
    def search_by_gstin(self, gstin: str) -> dict:
        pass

    @abstractmethod
    def search_by_company_name(self, company_name: str) -> list:
        pass

class ClearTaxGSTProvider(GSTDataProvider):
    def __init__(self, delay_min: float = 1.5, delay_max: float = 2.5, max_retries: int = 3):
        self.delay_min = delay_min
        self.delay_max = delay_max
        self.max_retries = max_retries
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
        ]

    def search_by_gstin(self, gstin: str) -> dict:
        url = f'https://cleartax.in/f/compliance-report/{gstin}/'
        headers = {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'application/json, text/plain, */*',
            'Referer': f'https://cleartax.in/gst-number-search/{gstin}/'
        }

        for attempt in range(1, self.max_retries + 1):
            time.sleep(random.uniform(self.delay_min, self.delay_max))
            try:
                r = requests.get(url, headers=headers, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    info = data.get('taxpayerInfo', {})
                    if info and info.get('lgnm'):
                        return {
                            'success': True,
                            'gstin': gstin,
                            'legal_name': info.get('lgnm', ''),
                            'trade_name': info.get('tradeNam', ''),
                            'gst_status': info.get('sts', ''),
                            'business_type': info.get('ctb', ''),
                            'provider': 'ClearTax'
                        }
                elif r.status_code == 429:
                    backoff = 30 * attempt
                    print(f"Rate limited (429) for {gstin}. Waiting {backoff}s...")
                    time.sleep(backoff)
            except Exception as e:
                print(f"Error fetching {gstin} (attempt {attempt}): {e}")
                time.sleep(3 * attempt)

        return {
            'success': False,
            'gstin': gstin,
            'error_type': 'Not Found / Rate Limited',
            'error_message': 'GST details unavailable or rate limited by provider'
        }

    def search_by_company_name(self, company_name: str) -> list:
        # ClearTax doesn't have an open API for name search, so we query our indexed local database
        return []

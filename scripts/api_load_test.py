"""
Нагрузочный тест API репрайсера.
Бьёт по реальному API, замеряет RPS и latency, результаты видно в Grafana.

Зависимости (на локальной машине):
    pip install requests

Запуск:
    python scripts/api_load_test.py --url https://qvaz.quasar-x.ru \
        --email your@email.com --password yourpassword \
        --threads 20 --duration 60
"""

import argparse
import time
import threading
import requests
from collections import defaultdict


class LoadTester:
    def __init__(self, base_url: str, token: str, n_threads: int, duration: int):
        self.base_url = base_url
        self.headers = {"Authorization": f"Bearer {token}"}
        self.n_threads = n_threads
        self.duration = duration
        self.results = defaultdict(list)
        self.lock = threading.Lock()
        self.stop_flag = False

    def _worker(self):
        session = requests.Session()
        session.headers.update(self.headers)

        endpoints = [
            ("GET",  "/api/products?page=1&page_size=50"),
            ("GET",  "/api/products?page=2&page_size=50"),
            ("GET",  "/api/repricer/runs?limit=20"),
            ("GET",  "/api/accounts"),
        ]

        idx = 0
        while not self.stop_flag:
            method, path = endpoints[idx % len(endpoints)]
            idx += 1
            try:
                t0 = time.perf_counter()
                r = session.request(method, self.base_url + path, timeout=15)
                elapsed_ms = (time.perf_counter() - t0) * 1000
                with self.lock:
                    self.results[path].append((r.status_code, elapsed_ms))
            except Exception:
                with self.lock:
                    self.results[path].append((0, 0))

    def run(self):
        threads = [threading.Thread(target=self._worker, daemon=True)
                   for _ in range(self.n_threads)]
        for t in threads:
            t.start()

        print(f"\n🚀 Тест запущен: {self.n_threads} потоков, {self.duration}с")
        print(f"   Смотри нагрузку в Grafana → Dashboards → cAdvisor\n")

        start = time.time()
        while time.time() - start < self.duration:
            elapsed = int(time.time() - start)
            total = sum(len(v) for v in self.results.values())
            print(f"   [{elapsed:>3}с] запросов: {total}", end="\r")
            time.sleep(1)

        self.stop_flag = True
        for t in threads:
            t.join(timeout=2)

        self._report()

    def _report(self):
        print("\n\n📊 Результаты:")
        print(f"{'Endpoint':<45} {'Запросов':>9} {'RPS':>6} {'Ср.мс':>8} {'P95мс':>8} {'Ошибок':>8}")
        print("─" * 90)

        for path, data in sorted(self.results.items()):
            total = len(data)
            ok = [(s, ms) for s, ms in data if s == 200]
            errors = total - len(ok)

            if ok:
                times = sorted(ms for _, ms in ok)
                avg = sum(times) / len(times)
                p95 = times[int(len(times) * 0.95)]
                rps = total / self.duration
            else:
                avg = p95 = rps = 0

            print(f"{path:<45} {total:>9} {rps:>6.1f} {avg:>8.1f} {p95:>8.1f} {errors:>8}")

        total_all = sum(len(v) for v in self.results.values())
        total_rps = total_all / self.duration
        print("─" * 90)
        print(f"{'ИТОГО':<45} {total_all:>9} {total_rps:>6.1f}")


def login(base_url: str, email: str, password: str) -> str:
    r = requests.post(
        f"{base_url}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    if r.status_code != 200:
        print(f"❌ Ошибка авторизации: {r.status_code} {r.text}")
        exit(1)
    token = r.json().get("access_token")
    if not token:
        print(f"❌ Токен не получен: {r.text}")
        exit(1)
    return token


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="WB Repricer API Load Test")
    parser.add_argument("--url",      default="https://qvaz.quasar-x.ru", help="Base URL")
    parser.add_argument("--email",    required=True,  help="Email для входа")
    parser.add_argument("--password", required=True,  help="Пароль")
    parser.add_argument("--threads",  type=int, default=10, help="Кол-во потоков (default: 10)")
    parser.add_argument("--duration", type=int, default=60, help="Длительность теста в секундах (default: 60)")
    args = parser.parse_args()

    print(f"🔐 Авторизация на {args.url}...")
    token = login(args.url, args.email, args.password)
    print(f"✅ Авторизован как {args.email}")

    tester = LoadTester(args.url, token, args.threads, args.duration)
    tester.run()

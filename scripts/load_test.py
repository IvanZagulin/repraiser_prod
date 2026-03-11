#!/usr/bin/env python3
"""
Нагрузочный тест WB Repricer.

Создаёт N тестовых товаров в БД через API, запускает прогон репрайсера
в dry_run режиме (без реальных WB API вызовов) и показывает прогресс.

Использование:
    python scripts/load_test.py --count 500 --base-url http://localhost:8000
    python scripts/load_test.py --count 1000 --cleanup
    python scripts/load_test.py --count 100 --no-dry-run   # реальный прогон!

Требования:
    pip install httpx rich
"""
import argparse
import time
import random
import sys

try:
    import httpx
    from rich.console import Console
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeElapsedColumn
    from rich.table import Table
    from rich import print as rprint
except ImportError:
    print("Установи зависимости: pip install httpx rich")
    sys.exit(1)

console = Console()

# --------------------------------------------------------------------------- #
#  Вспомогательные функции
# --------------------------------------------------------------------------- #

def login(client: httpx.Client, base_url: str, email: str, password: str) -> str:
    r = client.post(f"{base_url}/api/auth/login", json={"email": email, "password": password})
    r.raise_for_status()
    token = r.json()["access_token"]
    console.print(f"[green]✓ Авторизован как {email}[/green]")
    return token


def get_or_create_test_account(client: httpx.Client, base_url: str) -> int:
    """Возвращает ID тестового аккаунта (создаёт если нет)."""
    r = client.get(f"{base_url}/api/accounts")
    r.raise_for_status()
    accounts = r.json().get("accounts", r.json()) if isinstance(r.json(), dict) else r.json()

    for acc in accounts:
        if acc.get("name") == "LOAD_TEST_ACCOUNT":
            console.print(f"[cyan]↩ Использую существующий тестовый аккаунт id={acc['id']}[/cyan]")
            return acc["id"]

    import base64, json as _json
    _hdr = base64.urlsafe_b64encode(_json.dumps({"alg":"HS256","typ":"JWT"}).encode()).rstrip(b'=').decode()
    _pay = base64.urlsafe_b64encode(_json.dumps({"sid":"loadtest","sub":"0"}).encode()).rstrip(b'=').decode()
    fake_wb_token = f"{_hdr}.{_pay}.ZmFrZXNpZ25hdHVyZWZvcmxvYWR0ZXN0"

    r = client.post(f"{base_url}/api/accounts", json={
        "name": "LOAD_TEST_ACCOUNT",
        "wb_api_token": fake_wb_token,
    })
    r.raise_for_status()
    acc_id = r.json()["id"]
    console.print(f"[green]✓ Создан тестовый аккаунт id={acc_id}[/green]")
    return acc_id


def create_products_batch(
    client: httpx.Client,
    base_url: str,
    account_id: int,
    count: int,
    offset: int = 0,
) -> list[int]:
    """Создаёт count товаров через API, возвращает список их ID."""
    product_ids = []

    # Загружаем уже существующие товары аккаунта для быстрого lookup по nm_id
    existing = {}
    r0 = client.get(f"{base_url}/api/products", params={"account_id": account_id, "limit": 10000})
    if r0.status_code == 200:
        for p in r0.json().get("products", r0.json() if isinstance(r0.json(), list) else []):
            existing[p.get("nm_id")] = p["id"]

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(f"Создание {count} товаров...", total=count)

        for i in range(count):
            nm_id = 900_000_000 + offset + i
            sku = f"LOADTEST-{offset + i:05d}"
            min_price = random.randint(500, 2000)
            rrp_price = min_price + random.randint(100, 500)

            # Если товар уже есть — просто берём его ID
            if nm_id in existing:
                product_ids.append(existing[nm_id])
                progress.advance(task)
                continue

            # Создаём товар
            r = client.post(f"{base_url}/api/products", json={
                "account_id": account_id,
                "nm_id": nm_id,
                "seller_sku": sku,
                "name": f"Тестовый товар {offset + i}",
            })
            if r.status_code not in (200, 201):
                console.print(f"[yellow]⚠ Пропускаю {sku}: {r.status_code} {r.text[:80]}[/yellow]")
                progress.advance(task)
                continue

            product_id = r.json()["id"]

            # Настраиваем стратегию RRP
            r2 = client.post(f"{base_url}/api/products/{product_id}/config", json={
                "strategy": "rrp",
                "is_active": True,
                "min_price": min_price,
                "max_price": rrp_price + 200,
                "rrp_price": rrp_price,
                "rrp_tolerance": 50,
            })
            if r2.status_code not in (200, 201):
                console.print(f"[yellow]⚠ Конфиг для {sku}: {r2.status_code}[/yellow]")

            product_ids.append(product_id)
            progress.advance(task)

    return product_ids


def cleanup_test_products(client: httpx.Client, base_url: str, account_id: int) -> int:
    """Удаляет все товары тестового аккаунта."""
    r = client.get(f"{base_url}/api/products", params={"account_id": account_id, "limit": 5000})
    r.raise_for_status()
    products = r.json().get("products", [])

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        console=console,
    ) as progress:
        task = progress.add_task("Удаление тестовых товаров...", total=len(products))
        for p in products:
            client.delete(f"{base_url}/api/products/{p['id']}")
            progress.advance(task)

    return len(products)


def run_load_test(
    client: httpx.Client,
    base_url: str,
    product_ids: list[int],
    dry_run: bool,
) -> dict:
    """Запускает прогон репрайсера и возвращает результаты."""
    console.print(f"\n[bold]Запуск репрайсера для {len(product_ids)} товаров "
                  f"({'dry_run' if dry_run else 'РЕАЛЬНЫЙ ПРОГОН'})[/bold]")

    t_start = time.monotonic()

    # Запускаем через /run (Celery async)
    r = client.post(f"{base_url}/api/repricer/run", json={
        "seller_skus": None,  # все товары аккаунта
        "dry_run": dry_run,
    }, timeout=30)
    r.raise_for_status()
    queued = r.json()

    console.print(f"[cyan]✓ Задача поставлена в очередь. Всего товаров: {queued['total_products']}[/cyan]")
    console.print("[dim]Жду завершения... (опрашиваю /api/repricer/runs)[/dim]")

    # Поллинг — ждём появления завершённого прогона
    last_run_id = None
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        wait_task = progress.add_task("Ожидание Celery-воркера...", total=None)

        for attempt in range(300):  # макс 5 минут (1 сек пауза)
            time.sleep(1)
            r2 = client.get(f"{base_url}/api/repricer/runs", params={"limit": 1})
            runs = r2.json().get("runs", [])
            if runs:
                run = runs[0]
                if run["status"] in ("completed", "failed"):
                    last_run_id = run["id"]
                    break
                progress.update(
                    wait_task,
                    description=f"Статус: {run['status']} | "
                                 f"товаров: {run.get('total_products', '?')}",
                )

    elapsed = time.monotonic() - t_start

    if last_run_id is None:
        console.print("[red]✗ Прогон не завершился за 5 минут[/red]")
        return {}

    # Получаем финальный результат
    r3 = client.get(f"{base_url}/api/repricer/runs/{last_run_id}")
    result = r3.json()
    result["wall_time_sec"] = round(elapsed, 1)
    return result


def print_results(result: dict) -> None:
    if not result:
        return

    total = result.get("total_products", 0)
    updated = result.get("updated_count", 0)
    skipped = result.get("skipped_count", 0)
    errors = result.get("error_count", 0)
    wall = result.get("wall_time_sec", 0)
    throughput = round(total / wall, 1) if wall > 0 else 0

    table = Table(title="Результаты нагрузочного теста", show_header=True)
    table.add_column("Метрика", style="bold cyan")
    table.add_column("Значение", style="bold white")

    table.add_row("Run ID", str(result.get("id", "?")))
    table.add_row("Статус", result.get("status", "?"))
    table.add_row("Всего товаров", str(total))
    table.add_row("Обновлено", f"[green]{updated}[/green]")
    table.add_row("Пропущено", f"[yellow]{skipped}[/yellow]")
    table.add_row("Ошибок", f"[red]{errors}[/red]")
    table.add_row("Время стены (сек)", str(wall))
    table.add_row("Throughput (товаров/сек)", str(throughput))

    console.print()
    console.print(table)
    console.print(
        "\n[bold]Grafana → Repricer Load Test дашборд:[/bold] "
        "http://localhost:3000 (или SSH tunnel, см. README)"
    )


# --------------------------------------------------------------------------- #
#  main
# --------------------------------------------------------------------------- #

def main():
    parser = argparse.ArgumentParser(description="WB Repricer — нагрузочный тест")
    parser.add_argument("--count", type=int, default=200, help="Количество тестовых товаров (default: 200)")
    parser.add_argument("--base-url", default="http://localhost:8000", help="URL бэкенда")
    parser.add_argument("--email", default=None, help="Email admin-пользователя")
    parser.add_argument("--password", default=None, help="Пароль")
    parser.add_argument("--no-dry-run", action="store_true", help="Реальный прогон (внимание: вызовет WB API!)")
    parser.add_argument("--cleanup", action="store_true", help="Удалить тестовые товары после теста")
    parser.add_argument("--cleanup-only", action="store_true", help="Только удалить тестовые товары")
    args = parser.parse_args()

    if args.email is None:
        args.email = input("Email: ").strip()
    if args.password is None:
        import getpass
        args.password = getpass.getpass("Пароль: ")

    dry_run = not args.no_dry_run

    with httpx.Client(timeout=60) as client:
        token = login(client, args.base_url, args.email, args.password)
        client.headers["Authorization"] = f"Bearer {token}"

        account_id = get_or_create_test_account(client, args.base_url)

        if args.cleanup_only:
            deleted = cleanup_test_products(client, args.base_url, account_id)
            console.print(f"[green]Удалено {deleted} тестовых товаров[/green]")
            return

        console.print(f"\n[bold]Создаём {args.count} тестовых товаров...[/bold]")
        product_ids = create_products_batch(client, args.base_url, account_id, args.count)
        console.print(f"[green]✓ Создано {len(product_ids)} товаров[/green]")

        result = run_load_test(client, args.base_url, product_ids, dry_run=dry_run)
        print_results(result)

        if args.cleanup:
            console.print("\n[dim]Удаляю тестовые товары...[/dim]")
            deleted = cleanup_test_products(client, args.base_url, account_id)
            console.print(f"[green]✓ Удалено {deleted} товаров[/green]")
        else:
            console.print(
                f"\n[dim]Тестовые товары остались в БД (account_id={account_id}). "
                f"Удалить: python scripts/load_test.py --cleanup-only[/dim]"
            )


if __name__ == "__main__":
    main()

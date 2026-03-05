"""
Скрипт наполнения БД тестовыми данными для нагрузочного теста.

Запуск (внутри backend-контейнера):
    docker exec -it wb_repricer_backend_prod python /app/scripts/seed_load_test.py 5000

Удаление тестовых данных:
    docker exec -it wb_repricer_backend_prod python /app/scripts/seed_load_test.py --clean
"""

import sys
import random
import string
from decimal import Decimal

sys.path.insert(0, '/app')

from app.database import SessionLocal
from app.models.user import User
from app.models.account import Account
from app.models.product import Product
from app.models.product_config import ProductConfig

TEST_EMAIL = "loadtest@internal.test"
TEST_ACCOUNT_NAME = "LoadTest Account"


def hash_password(password: str) -> str:
    from passlib.context import CryptContext
    return CryptContext(schemes=["bcrypt"]).hash(password)


def clean(db):
    user = db.query(User).filter(User.email == TEST_EMAIL).first()
    if not user:
        print("Тестовый пользователь не найден, нечего чистить.")
        return
    db.delete(user)
    db.commit()
    print(f"✅ Тестовый пользователь и все его данные удалены.")


def seed(db, n_products: int):
    # --- Пользователь ---
    user = db.query(User).filter(User.email == TEST_EMAIL).first()
    if not user:
        user = User(
            email=TEST_EMAIL,
            password_hash=hash_password("LoadTest_pass_123"),
            is_active=True,
            is_verified=True,
        )
        db.add(user)
        db.flush()
        print(f"  Создан пользователь: {TEST_EMAIL} (id={user.id})")
    else:
        print(f"  Пользователь уже есть: {TEST_EMAIL} (id={user.id})")

    # --- WB Аккаунт ---
    account = db.query(Account).filter(Account.user_id == user.id).first()
    if not account:
        account = Account(
            user_id=user.id,
            name=TEST_ACCOUNT_NAME,
            wb_api_token="fake_token_load_test_do_not_use",
            is_active=True,
        )
        db.add(account)
        db.flush()
        print(f"  Создан аккаунт: {TEST_ACCOUNT_NAME} (id={account.id})")
    else:
        print(f"  Аккаунт уже есть (id={account.id})")

    # --- Товары ---
    existing = db.query(Product).filter(Product.account_id == account.id).count()
    to_create = max(0, n_products - existing)
    print(f"  Товаров в БД: {existing}, нужно создать: {to_create}")

    batch_size = 500
    created = 0
    for i in range(0, to_create, batch_size):
        batch = min(batch_size, to_create - i)
        for _ in range(batch):
            nm_id = random.randint(100_000_000, 999_999_999)
            sku = "LT-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
            product = Product(
                account_id=account.id,
                nm_id=nm_id,
                seller_sku=sku,
                is_active=True,
            )
            db.add(product)
            db.flush()
            config = ProductConfig(
                product_id=product.id,
                strategy="rrp",
                is_active=True,
                min_price=Decimal("500"),
                max_price=Decimal("5000"),
                rrp_price=Decimal("1500"),
                rrp_tolerance=Decimal("100"),
            )
            db.add(config)
            created += 1

        db.commit()
        print(f"  Создано {created}/{to_create}...", end="\r")

    print(f"\n✅ Готово. Всего товаров в БД: {existing + created}")
    print(f"   user_id={user.id}, account_id={account.id}")


if __name__ == "__main__":
    args = sys.argv[1:]
    db = SessionLocal()
    try:
        if args and args[0] == "--clean":
            clean(db)
        else:
            n = int(args[0]) if args else 5000
            print(f"🚀 Наполнение БД: {n} тестовых товаров...")
            seed(db, n)
    finally:
        db.close()

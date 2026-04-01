#!/usr/bin/env python3
"""Генерация безопасных секретов для .env файла"""

import secrets
import base64

def generate_password(length=32):
    """Генерация безопасного пароля"""
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))

def generate_secret():
    """Генерация JWT secret (32+ символа)"""
    return secrets.token_urlsafe(32)

def generate_fernet_key():
    """Генерация Fernet key (32 байта, base64)"""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()

def generate_redis_password():
    """Генерация пароля для Redis"""
    return secrets.token_urlsafe(24)

if __name__ == '__main__':
    print("=" * 60)
    print("СГЕНЕРИРОВАННЫЕ СЕКРЕТЫ ДЛЯ .env")
    print("=" * 60)
    print()
    print(f"POSTGRES_PASSWORD={generate_password(32)}")
    print(f"JWT_SECRET={generate_secret()}")
    print(f"ADMIN_PASSWORD={generate_password(24)}")
    print(f"REDIS_PASSWORD={generate_redis_password()}")
    print(f"FERNET_KEY={generate_fernet_key()}")
    print()
    print("=" * 60)
    print("Скопируйте эти значения в ваш .env файл")
    print("=" * 60)

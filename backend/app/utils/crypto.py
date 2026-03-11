"""
Утилиты для шифрования чувствительных данных в БД (WB API токены и т.п.)

Использует Fernet (AES-128-CBC + HMAC-SHA256).
Если FERNET_KEY не задан в .env — данные хранятся в открытом виде (backward compatible).

Зашифрованные значения помечаются префиксом "ENC:" — это позволяет:
 1. Безболезненно перейти с незашифрованных на зашифрованные данные.
 2. Корректно расшифровать даже legacy-значения без ключа.
"""

from typing import Optional
from sqlalchemy import TypeDecorator, Text

# Ленивая инициализация — не падаем при импорте если ключ не задан
_fernet = None
_fernet_initialized = False


def _get_fernet():
    """Возвращает Fernet-экземпляр, инициализированный из FERNET_KEY в settings."""
    global _fernet, _fernet_initialized
    if _fernet_initialized:
        return _fernet
    try:
        from app.config import settings
        if settings.FERNET_KEY:
            from cryptography.fernet import Fernet
            _fernet = Fernet(settings.FERNET_KEY.encode())
    except Exception:
        _fernet = None
    _fernet_initialized = True
    return _fernet


def encrypt_field(value: Optional[str]) -> Optional[str]:
    """
    Шифрует строку с помощью Fernet.
    Зашифрованное значение помечается префиксом 'ENC:'.
    Если ключ не задан — возвращает значение как есть (plaintext).
    """
    if value is None:
        return None
    if isinstance(value, str) and value.startswith("ENC:"):
        return value  # Уже зашифровано — не шифруем повторно
    f = _get_fernet()
    if f is None:
        return value  # Нет ключа — храним plaintext
    return "ENC:" + f.encrypt(value.encode()).decode()


def decrypt_field(value: Optional[str]) -> Optional[str]:
    """
    Расшифровывает Fernet-значение.
    Если значение не имеет префикса 'ENC:' — возвращает как есть (legacy plaintext).
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.startswith("ENC:"):
        return value  # Не зашифровано (legacy) — вернуть как есть
    f = _get_fernet()
    if f is None:
        return value  # Нет ключа — не можем расшифровать
    try:
        return f.decrypt(value[4:].encode()).decode()
    except Exception:
        return value  # Fallback при ошибке расшифровки


class EncryptedText(TypeDecorator):
    """
    SQLAlchemy TypeDecorator: прозрачно шифрует/расшифровывает текстовое поле.
    Подключается к любому Column как тип вместо Text/String.
    """
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        """Вызывается при записи в БД — шифруем."""
        return encrypt_field(value)

    def process_result_value(self, value, dialect):
        """Вызывается при чтении из БД — расшифровываем."""
        return decrypt_field(value)

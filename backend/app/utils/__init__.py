# Utility Functions
from fastapi import Request


def get_real_ip(request: Request) -> str:
    """
    Возвращает реальный IP клиента с учётом Nginx/Cloudflare прокси.

    Порядок поиска:
    1. X-Forwarded-For — первый IP в списке (клиентский)
    2. X-Real-IP       — выставляется Nginx через proxy_set_header
    3. request.client.host — сырой сокетный IP (fallback, для локалки)
    """
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        # "client_ip, proxy1_ip, proxy2_ip" → берём первый
        return forwarded_for.split(",")[0].strip()

    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()

    if request.client:
        return request.client.host

    return "unknown"


def get_user_id_key(request: Request) -> str:
    """
    Ключ rate-limiter'а по user_id из JWT Bearer-токена.

    Используется для аутентифицированных эндпоинтов вместо IP —
    один пользователь с нескольких IP не обойдёт лимит.
    Fallback на реальный IP если токен отсутствует или невалиден.
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:].strip()
        try:
            from jose import jwt as jose_jwt
            from jose.exceptions import JWTError
            from app.config import settings
            # verify_exp=False — для rate limiting срок жизни токена не важен
            payload = jose_jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=[settings.JWT_ALGORITHM],
                options={"verify_exp": False},
            )
            uid = payload.get("user_id")
            if uid is not None:
                return f"uid:{uid}"
        except Exception:
            pass
    return get_real_ip(request)

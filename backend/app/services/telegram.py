import logging
from typing import Optional
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Сервис отправки критических уведомлений в Telegram"""
    
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = bool(self.bot_token and self.chat_id)
        
        if not self.enabled:
            logger.warning("Telegram уведомления отключены (не настроены BOT_TOKEN или CHAT_ID)")
    
    def _build_api_url(self, method: str) -> str:
        """Формирование URL Telegram Bot API"""
        return f"https://api.telegram.org/bot{self.bot_token}/{method}"
    
    async def send_message(
        self,
        text: str,
        parse_mode: str = "HTML",
        disable_notification: bool = False
    ) -> bool:
        """
        Отправка сообщения в Telegram
        
        Args:
            text: Текст сообщения
            parse_mode: Режим парсинга (HTML или Markdown)
            disable_notification: Тихое уведомление
            
        Returns:
            True если успешно отправлено
        """
        if not self.enabled:
            logger.debug("Telegram не настроен, пропускаем отправку")
            return False
        
        url = self._build_api_url("sendMessage")
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_notification": disable_notification
        }
        
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(url, json=payload)
                
                if response.status_code == 200:
                    logger.info("✓ Telegram уведомление отправлено")
                    return True
                else:
                    logger.error(f"Ошибка отправки в Telegram: {response.status_code} {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Исключение при отправке в Telegram: {e}")
            return False
    
    async def notify_token_expired(self, account_name: str, account_id: int):
        """Уведомление об истечении токена WB API"""
        text = (
            f"⚠️ <b>КРИТИЧЕСКОЕ: Истек токен WB API</b>\n\n"
            f"Аккаунт: <b>{account_name}</b> (ID: {account_id})\n"
            f"Необходимо обновить токен в настройках аккаунта.\n\n"
            f"Репрайсер для этого аккаунта остановлен."
        )
        await self.send_message(text)
    
    async def notify_token_expiring_soon(self, account_name: str, account_id: int, days_left: int):
        """Уведомление о скором истечении токена"""
        text = (
            f"⚠️ <b>Токен WB API истекает через {days_left} дней</b>\n\n"
            f"Аккаунт: <b>{account_name}</b> (ID: {account_id})\n"
            f"Рекомендуется обновить токен заранее."
        )
        await self.send_message(text)
    
    async def notify_api_failure(self, error_message: str, context: Optional[str] = None):
        """Уведомление о критическом сбое API"""
        text = (
            f"🚨 <b>КРИТИЧЕСКАЯ ОШИБКА WB API</b>\n\n"
            f"Ошибка: {error_message}\n"
        )
        if context:
            text += f"Контекст: {context}\n"
        
        text += "\nТребуется проверка работоспособности сервиса."
        await self.send_message(text)
    
    async def notify_scheduler_error(self, error_message: str):
        """Уведомление об ошибке планировщика"""
        text = (
            f"❌ <b>ОШИБКА ПЛАНИРОВЩИКА</b>\n\n"
            f"Ошибка: {error_message}\n\n"
            f"Автоматические прогоны репрайсера могут быть нарушены."
        )
        await self.send_message(text)
    
    async def notify_all_proxies_dead(self):
        """Уведомление о выходе из строя всех прокси"""
        text = (
            f"🔴 <b>КРИТИЧЕСКОЕ: ВСЕ ПРОКСИ НЕДОСТУПНЫ</b>\n\n"
            f"Парсинг цен конкурентов невозможен.\n"
            f"Необходимо проверить SOCKS5 прокси."
        )
        await self.send_message(text)
    
    async def notify_repricer_completed(
        self,
        run_type: str,
        total: int,
        updated: int,
        errors: int,
        duration_seconds: int
    ):
        """Уведомление о завершении прогона (опционально)"""
        text = (
            f"✅ <b>Прогон репрайсера завершен</b>\n\n"
            f"Тип: {run_type}\n"
            f"Обработано товаров: {total}\n"
            f"Цены изменены: {updated}\n"
            f"Ошибок: {errors}\n"
            f"Время выполнения: {duration_seconds}с"
        )
        # Отправляем без звука
        await self.send_message(text, disable_notification=True)


# Singleton instance
telegram_notifier = TelegramNotifier()

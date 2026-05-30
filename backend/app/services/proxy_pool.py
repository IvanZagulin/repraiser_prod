import asyncio
import logging
from typing import Optional, List
from dataclasses import dataclass
import httpx
from app.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ProxyConfig:
    """Конфигурация прокси"""
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    
    @property
    def url(self) -> str:
        """Формирование URL прокси для httpx"""
        if self.username and self.password:
            return f"socks5://{self.username}:{self.password}@{self.host}:{self.port}"
        return f"socks5://{self.host}:{self.port}"
    
    def __repr__(self):
        return f"<Proxy {self.host}:{self.port}>"


class ProxyPool:
    """Пул SOCKS5 прокси с ротацией и health check"""
    
    def __init__(self):
        self.proxies: List[ProxyConfig] = []
        self.dead_proxies: set = set()
        self.current_index = 0
        self._lock = asyncio.Lock()
        self._initialized = False
    
    def _parse_proxy_string(self, proxy_str: str) -> Optional[ProxyConfig]:
        """
        Парсинг строки прокси формата: ip:port:login:password
        
        Args:
            proxy_str: Строка с данными прокси
            
        Returns:
            ProxyConfig или None если формат некорректный
        """
        try:
            parts = proxy_str.strip().split(':')
            if len(parts) == 2:
                # Формат: ip:port
                return ProxyConfig(host=parts[0], port=int(parts[1]))
            elif len(parts) == 4:
                # Формат: ip:port:login:password
                return ProxyConfig(
                    host=parts[0],
                    port=int(parts[1]),
                    username=parts[2],
                    password=parts[3]
                )
            else:
                logger.error(f"Неверный формат прокси: {proxy_str.split(':')[0]}:***")
                return None
        except (ValueError, IndexError) as e:
            logger.error(f"Ошибка парсинга прокси '{proxy_str.split(':')[0]}:***': {e}")
            return None
    
    def load_from_config(self):
        """Загрузка прокси из настроек"""
        if not settings.SOCKS5_PROXIES:
            logger.warning("SOCKS5_PROXIES не настроены")
            return

        self.proxies = []  # reset — защита от дублей при повторной инициализации
        proxy_strings = settings.SOCKS5_PROXIES.split(';')
        for proxy_str in proxy_strings:
            if not proxy_str.strip():
                continue
            
            proxy = self._parse_proxy_string(proxy_str)
            if proxy:
                self.proxies.append(proxy)
        
        logger.info(f"Загружено {len(self.proxies)} прокси из конфигурации")
    
    # Лёгкий публичный endpoint WB для проверки, что прокси реально проксирует HTTP.
    _CHECK_URL = (
        "https://card.wb.ru/cards/v4/detail"
        "?appType=1&curr=rub&dest=-1257786&nm=181886625"
    )

    async def check_proxy(self, proxy: ProxyConfig, timeout: int = 10) -> bool:
        """
        Проверка работоспособности прокси РЕАЛЬНЫМ HTTP-запросом через него.

        TCP-проверки недостаточно: прокси может принимать TCP-соединение, но не
        проксировать HTTP/SOCKS — тогда парсинг через него молча падает и уходит
        в direct, а пул продолжает считать прокси живым. Делаем настоящий GET.

        Args:
            proxy: Конфигурация прокси
            timeout: Таймаут проверки в секундах

        Returns:
            True если через прокси успешно получен HTTP 200
        """
        try:
            async with httpx.AsyncClient(
                proxy=proxy.url,
                timeout=timeout,
                headers={"User-Agent": "Mozilla/5.0"},
            ) as client:
                resp = await client.get(self._CHECK_URL)
            if resp.status_code == 200:
                logger.debug(f"✓ Прокси {proxy} рабочий (HTTP 200)")
                return True
            logger.warning(f"✗ Прокси {proxy}: HTTP {resp.status_code}")
            return False
        except Exception as e:
            logger.warning(f"✗ Прокси {proxy} недоступен: {type(e).__name__}")
            return False
    
    async def health_check_all(self):
        """Проверка работоспособности всех прокси"""
        if not self.proxies:
            logger.warning("Нет прокси для проверки")
            return
        
        logger.info(f"Начинаем health check для {len(self.proxies)} прокси...")
        tasks = [self.check_proxy(proxy) for proxy in self.proxies]
        results = await asyncio.gather(*tasks)
        
        # Помечаем мертвые прокси
        self.dead_proxies.clear()
        for proxy, is_alive in zip(self.proxies, results):
            if not is_alive:
                self.dead_proxies.add(proxy.url)
        
        alive_count = len(self.proxies) - len(self.dead_proxies)
        logger.info(f"Health check завершен: {alive_count}/{len(self.proxies)} прокси живы")
        
        if alive_count == 0:
            logger.critical("⚠️ ВСЕ ПРОКСИ МЕРТВЫ! Парсинг невозможен!")
    
    async def initialize(self):
        """Инициализация пула прокси (потокобезопасно).

        Под локом с double-check: иначе при параллельном парсинге десятки
        корутин одновременно вызовут load_from_config и продублируют список
        прокси в N раз + запустят сотни лишних health-check.
        """
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:  # другой корутин успел инициализировать
                return
            self.load_from_config()
            await self.health_check_all()
            self._initialized = True
    
    async def get_next_proxy(self) -> Optional[ProxyConfig]:
        """
        Получение следующего рабочего прокси (round-robin)
        
        Returns:
            ProxyConfig или None если нет живых прокси
        """
        if not self._initialized:
            await self.initialize()
        
        if not self.proxies:
            logger.error("Пул прокси пуст")
            return None
        
        async with self._lock:
            # Попытка найти живой прокси (максимум len(proxies) попыток)
            attempts = len(self.proxies)
            for _ in range(attempts):
                proxy = self.proxies[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.proxies)
                
                # Пропускаем мертвые прокси
                if proxy.url not in self.dead_proxies:
                    return proxy
            
            logger.error("Нет доступных живых прокси!")
            return None
    
    def mark_as_dead(self, proxy: ProxyConfig):
        """Пометить прокси как мертвый"""
        self.dead_proxies.add(proxy.url)
        logger.warning(f"Прокси {proxy} помечен как мертвый ({len(self.dead_proxies)}/{len(self.proxies)})")
    
    def get_stats(self) -> dict:
        """Получить статистику пула"""
        total = len(self.proxies)
        dead = len(self.dead_proxies)
        alive = total - dead
        
        return {
            "total": total,
            "alive": alive,
            "dead": dead,
            "health_rate": f"{alive}/{total}" if total > 0 else "0/0"
        }


# Singleton instance
proxy_pool = ProxyPool()

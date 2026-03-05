import { format } from 'date-fns';

/**
 * Форматирует дату в московском времени (UTC+3), независимо от таймзоны браузера.
 *
 * Проблема: date-fns format() рендерит Date в ЛОКАЛЬНОМ времени браузера.
 * Поэтому нельзя просто прибавить +3ч — браузер в UTC+3 добавит ещё +3ч → +6ч.
 *
 * Решение: создаём «фиктивный» Date так, чтобы при форматировании в локальном
 * времени браузера он показал нужное МСК-значение.
 *
 * Формула: displayMs = utcMs + MSK_offset + browser_offset
 *   – UTC+3 браузер: +3h + (–3h) = 0  → format() +3h → 11:06 ✓
 *   – UTC    браузер: +3h +   0  = +3h → format() +0h → 11:06 ✓
 *   – UTC-5  браузер: +3h + +5h = +8h → format() –5h → 11:06 ✓
 *
 * @param {string|Date} dateStr  — ISO-строка или объект Date (UTC)
 * @param {string}      fmt      — формат date-fns (напр. 'dd.MM.yyyy HH:mm')
 * @returns {string}
 */
export const formatMsk = (dateStr, fmt) => {
  const raw = typeof dateStr === 'string' ? dateStr : dateStr.toISOString();
  // Принудительно трактуем строку как UTC
  const utcStr = /[Zz]$|[+-]\d{2}:\d{2}$/.test(raw) ? raw : raw + 'Z';
  const utc = new Date(utcStr);
  // Компенсируем смещение браузера, чтобы format() показал именно МСК
  const displayMs = utc.getTime()
    + 3 * 60 * 60 * 1000              // МСК = UTC+3
    + utc.getTimezoneOffset() * 60 * 1000; // отменяем смещение браузера
  return format(new Date(displayMs), fmt);
};

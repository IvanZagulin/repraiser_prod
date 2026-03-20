import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import api from '../api/client';
import {
  Activity, Filter, Download, ChevronLeft, ChevronRight,
  Loader2, Tag, DollarSign, User, Settings, Zap,
  Pause, Package, Trash2, Play, LogIn,
} from 'lucide-react';
import { formatMsk } from '../utils/date';

const EVENT_CONFIG = {
  price_changed:         { icon: DollarSign, color: '#22c55e', label: 'Цена изменена' },
  strategy_activated:    { icon: Zap,        color: '#00c8ff', label: 'Стратегия включена' },
  strategy_deactivated:  { icon: Pause,      color: '#9ca3af', label: 'Стратегия отключена' },
  account_created:       { icon: User,       color: '#3b82f6', label: 'Аккаунт создан' },
  product_created:       { icon: Package,    color: '#3b82f6', label: 'Товар добавлен' },
  product_deleted:       { icon: Trash2,     color: '#ef4444', label: 'Товар удалён' },
  repricer_run:          { icon: Play,       color: '#00c8ff', label: 'Прогон репрайсера' },
  login:                 { icon: LogIn,      color: '#9ca3af', label: 'Вход в систему' },
};

const EVENT_OPTIONS = [
  { value: '', label: 'Все события' },
  ...Object.entries(EVENT_CONFIG).map(([value, { label }]) => ({ value, label })),
];

const PAGE_SIZE = 25;

function parseDetails(details) {
  if (!details) return null;
  if (typeof details === 'object') return details;
  try {
    return JSON.parse(details);
  } catch {
    return null;
  }
}

function descriptionFromDetails(details) {
  const parsed = parseDetails(details);
  if (!parsed) return null;

  const parts = [];
  if (parsed.product_name) parts.push(parsed.product_name);
  if (parsed.sku) parts.push(`SKU: ${parsed.sku}`);
  if (parsed.old_price != null && parsed.new_price != null) {
    parts.push(`${parsed.old_price} ₽ → ${parsed.new_price} ₽`);
  }
  if (parsed.strategy) parts.push(`Стратегия: ${parsed.strategy}`);
  if (parsed.message) parts.push(parsed.message);
  if (parsed.reason) parts.push(parsed.reason);

  return parts.length > 0 ? parts.join(' · ') : JSON.stringify(parsed);
}

export default function ActivityLogPage() {
  const [items, setItems] = useState([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  // Filters
  const [eventType, setEventType] = useState('');
  const [productId, setProductId] = useState('');
  const [dateFrom, setDateFrom] = useState('');
  const [dateTo, setDateTo] = useState('');

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const fetchActivity = async (p = page) => {
    setLoading(true);
    try {
      const params = { page: p, page_size: PAGE_SIZE };
      if (eventType) params.event_type = eventType;
      if (productId) params.product_id = productId;
      if (dateFrom) params.date_from = dateFrom;
      if (dateTo) params.date_to = dateTo;

      const { data } = await api.get('/api/activity', { params });
      setItems(data.items || []);
      setTotal(data.total || 0);
      setPage(data.page || p);
    } catch {
      toast.error('Ошибка загрузки журнала');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchActivity(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventType, dateFrom, dateTo]);

  useEffect(() => {
    fetchActivity(page);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [page]);

  const handleProductSearch = (e) => {
    if (e.key === 'Enter') {
      setPage(1);
      fetchActivity(1);
    }
  };

  const handleExport = async () => {
    setExporting(true);
    try {
      const { data } = await api.get('/api/activity/export', { responseType: 'blob' });
      const url = window.URL.createObjectURL(new Blob([data]));
      const link = document.createElement('a');
      link.href = url;
      link.setAttribute('download', 'activity_log.xlsx');
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.URL.revokeObjectURL(url);
      toast.success('Файл загружен');
    } catch {
      toast.error('Ошибка экспорта');
    } finally {
      setExporting(false);
    }
  };

  const renderEventIcon = (eventType) => {
    const cfg = EVENT_CONFIG[eventType] || { icon: Activity, color: '#9ca3af', label: eventType };
    const Icon = cfg.icon;
    return (
      <div
        className="flex items-center justify-center w-8 h-8 rounded-full shrink-0"
        style={{ backgroundColor: cfg.color + '1a', color: cfg.color }}
      >
        <Icon className="w-4 h-4" />
      </div>
    );
  };

  const renderBadge = (eventType) => {
    const cfg = EVENT_CONFIG[eventType] || { label: eventType, color: '#9ca3af' };
    return (
      <span
        className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium"
        style={{ backgroundColor: cfg.color + '1a', color: cfg.color }}
      >
        {cfg.label}
      </span>
    );
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-h1">Журнал действий</h1>
          <p className="text-sm mt-1 text-muted-foreground">
            Аудит-лог всех действий в системе
          </p>
        </div>
        <button
          onClick={handleExport}
          disabled={exporting}
          className="btn btn-secondary gap-2"
        >
          {exporting ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Download className="w-4 h-4" />
          )}
          Экспорт
        </button>
      </div>

      {/* Filter bar */}
      <div className="card mb-6">
        <div className="flex flex-wrap items-end gap-4 p-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">Тип события</label>
            <select
              className="input input-sm min-w-[200px]"
              value={eventType}
              onChange={(e) => { setEventType(e.target.value); setPage(1); }}
            >
              {EVENT_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">Дата от</label>
            <input
              type="date"
              className="input input-sm"
              value={dateFrom}
              onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">Дата до</label>
            <input
              type="date"
              className="input input-sm"
              value={dateTo}
              onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-medium text-muted-foreground">ID товара</label>
            <input
              type="text"
              className="input input-sm"
              placeholder="Поиск по товару…"
              value={productId}
              onChange={(e) => setProductId(e.target.value)}
              onKeyDown={handleProductSearch}
            />
          </div>

          <button
            onClick={() => { setPage(1); fetchActivity(1); }}
            className="btn btn-primary btn-sm gap-1.5"
          >
            <Filter className="w-3.5 h-3.5" />
            Применить
          </button>
        </div>
      </div>

      {/* Timeline list */}
      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="spinner" />
        </div>
      ) : items.length === 0 ? (
        <div className="card text-center py-16">
          <Activity className="w-10 h-10 mx-auto mb-3" style={{ color: '#e5e7eb' }} />
          <p style={{ color: 'var(--text-3)' }}>Нет записей</p>
        </div>
      ) : (
        <div className="card p-6">
          <div className="relative">
            {/* Vertical timeline line */}
            <div
              className="absolute left-4 top-0 bottom-0 w-px"
              style={{ backgroundColor: 'rgba(0, 200, 255, 0.15)' }}
            />

            <div className="space-y-0">
              {items.map((item, idx) => {
                const desc = descriptionFromDetails(item.details);
                const cfg = EVENT_CONFIG[item.event_type] || {
                  label: item.event_type,
                  color: '#9ca3af',
                };
                return (
                  <div key={item.id || idx} className="relative flex items-start gap-4 py-3 pl-0">
                    {/* Icon on the timeline */}
                    <div className="relative z-10">
                      {renderEventIcon(item.event_type)}
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2 mb-0.5">
                        {renderBadge(item.event_type)}
                        {item.user_email && (
                          <span className="text-xs text-muted-foreground">
                            {item.user_email}
                          </span>
                        )}
                      </div>
                      {desc && (
                        <p className="text-sm text-foreground mt-0.5 truncate">
                          {desc}
                        </p>
                      )}
                    </div>

                    {/* Timestamp */}
                    <span className="text-xs text-muted-foreground whitespace-nowrap shrink-0">
                      {formatMsk(item.created_at || item.timestamp, 'dd.MM.yyyy HH:mm:ss')}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between mt-4">
          <button
            className="btn btn-secondary btn-sm gap-1.5"
            disabled={page <= 1}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="w-4 h-4" />
            Назад
          </button>

          <span className="text-sm text-muted-foreground">
            Страница {page} из {totalPages} · Всего: {total}
          </span>

          <button
            className="btn btn-secondary btn-sm gap-1.5"
            disabled={page >= totalPages}
            onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
          >
            Далее
            <ChevronRight className="w-4 h-4" />
          </button>
        </div>
      )}
    </div>
  );
}

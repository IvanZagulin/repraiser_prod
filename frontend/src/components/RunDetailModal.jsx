import { useEffect, useState } from 'react';
import { X, TrendingUp, TrendingDown, Minus, Download } from 'lucide-react';
import { getRepricerRun, exportRun } from '../api/client';
import { formatMsk } from '../utils/date';
import toast from 'react-hot-toast';

const REASON_MAP = {
  UPDATED:          { label: 'Обновлено',         color: '#16a34a', bg: '#dcfce7' },
  CLAMPED_TO_MIN:   { label: 'Ограничено мин.',   color: '#d97706', bg: '#fef3c7' },
  CLAMPED_TO_MAX:   { label: 'Ограничено макс.',  color: '#d97706', bg: '#fef3c7' },
  NO_CHANGE:        { label: 'Без изменений',     color: '#6b7280', bg: '#f3f4f6' },
  SKIPPED_INACTIVE: { label: 'Не активен',        color: '#9ca3af', bg: '#f9fafb' },
  SKIPPED_PROMO:    { label: 'Пропущено',          color: '#9ca3af', bg: '#f3f4f6' },
  COMPETITOR_OOS:   { label: 'Конкурент не в наличии', color: '#8b5cf6', bg: '#f5f3ff' },
  PARSE_ERROR:      { label: 'Ошибка парсинга',   color: '#ef4444', bg: '#fef2f2' },
  WB_API_ERROR:     { label: 'Ошибка WB API',     color: '#ef4444', bg: '#fef2f2' },
};

const ReasonBadge = ({ code }) => {
  const r = REASON_MAP[code] || { label: code, color: '#6b7280', bg: '#f3f4f6' };
  return (
    <span
      className="text-xs font-semibold px-2 py-0.5 rounded-full"
      style={{ color: r.color, background: r.bg }}
    >
      {r.label}
    </span>
  );
};

const PriceDiff = ({ oldP, newP }) => {
  if (!newP || oldP === newP) return <Minus className="w-3.5 h-3.5 text-muted-foreground" />;
  const diff = newP - oldP;
  const pct = ((diff / oldP) * 100).toFixed(1);
  return (
    <div className={`flex items-center gap-1 text-xs font-semibold ${diff > 0 ? 'text-red-500' : 'text-green-600'}`}>
      {diff > 0 ? <TrendingUp className="w-3.5 h-3.5" /> : <TrendingDown className="w-3.5 h-3.5" />}
      {diff > 0 ? '+' : ''}{diff.toLocaleString('ru')}₽
      <span className="text-muted-foreground font-normal">({diff > 0 ? '+' : ''}{pct}%)</span>
    </div>
  );
};

export default function RunDetailModal({ run, onClose }) {
  const [detail, setDetail] = useState(null);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    getRepricerRun(run.id, true)
      .then((res) => setDetail(res.data))
      .catch(() => toast.error('Ошибка загрузки деталей'))
      .finally(() => setLoading(false));
  }, [run.id]);

  const handleExport = async () => {
    try {
      const res = await exportRun(run.id);
      const url = window.URL.createObjectURL(new Blob([res.data]));
      const a = document.createElement('a');
      a.href = url;
      a.setAttribute('download', `run_${run.id}.xlsx`);
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast.success('Файл загружен');
    } catch {
      toast.error('Ошибка экспорта');
    }
  };

  const changes = detail?.changes || [];
  const filtered = filter === 'all' ? changes : changes.filter((c) => c.reason_code === filter || (filter === 'updated' && ['UPDATED', 'CLAMPED_TO_MIN', 'CLAMPED_TO_MAX'].includes(c.reason_code)) || (filter === 'errors' && ['PARSE_ERROR', 'WB_API_ERROR'].includes(c.reason_code)));

  // Unique reason codes for filter tabs
  const reasonGroups = [
    { key: 'all', label: 'Все' },
    { key: 'updated', label: 'Обновлено' },
    { key: 'NO_CHANGE', label: 'Без изменений' },
    { key: 'errors', label: 'Ошибки' },
  ];

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ maxWidth: 900, width: '98%', padding: '1.5rem' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text)' }}>
              Детали запуска #{run.id}
            </h2>
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
              {run.run_type === 'scheduled' ? '🔄 Автоматический' : '👆 Ручной'} ·{' '}
              {formatMsk(run.started_at, 'dd.MM.yyyy HH:mm')}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button onClick={handleExport} className="btn btn-secondary btn-sm gap-1.5">
              <Download className="w-3.5 h-3.5" />
              Excel
            </button>
            <button onClick={onClose} className="btn btn-ghost btn-sm p-1.5">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Stats row */}
        {detail && (
          <div className="grid grid-cols-4 gap-3 mb-5">
            {[
              { label: 'Всего', value: detail.total_products, color: '#7c3aed' },
              { label: 'Обновлено', value: detail.updated_count, color: '#16a34a' },
              { label: 'Пропущено', value: detail.skipped_count, color: '#d97706' },
              { label: 'Ошибок', value: detail.error_count, color: detail.error_count > 0 ? '#ef4444' : '#9ca3af' },
            ].map((s) => (
              <div
                key={s.label}
                className="rounded-xl p-3 text-center bg-muted/50 border border-border"
              >
                <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>{s.label}</p>
                <p className="text-xl font-bold" style={{ color: s.color }}>{s.value}</p>
              </div>
            ))}
          </div>
        )}

        {/* Filter tabs */}
        {!loading && changes.length > 0 && (
          <div className="flex items-center gap-1.5 mb-4 flex-wrap">
            {reasonGroups.map((g) => (
              <button
                key={g.key}
                onClick={() => setFilter(g.key)}
                className="text-xs font-semibold px-3 py-1 rounded-full transition-all"
                style={{
                  background: filter === g.key ? 'hsl(var(--primary))' : 'hsl(var(--muted))',
                  color: filter === g.key ? 'hsl(var(--primary-foreground))' : 'var(--text-2)',
                }}
              >
                {g.label}
              </button>
            ))}
          </div>
        )}

        {/* Table */}
        {loading ? (
          <div className="flex items-center justify-center h-48">
            <div className="spinner" />
          </div>
        ) : changes.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных об изменениях</p>
          </div>
        ) : (
          <div className="overflow-auto border border-border rounded-xl" style={{ maxHeight: 420 }}>
            <table className="dark-table" style={{ minWidth: 640 }}>
              <thead style={{ position: 'sticky', top: 0, zIndex: 1 }}>
                <tr>
                  <th>Артикул</th>
                  <th>Название</th>
                  <th>Старая цена</th>
                  <th>Новая цена</th>
                  <th>Изменение</th>
                  <th>Статус</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((c) => (
                  <tr key={c.id}>
                    <td>
                      <span className="font-medium text-xs" style={{ color: 'var(--text)' }}>
                        {c.seller_sku || `#${c.product_id}`}
                      </span>
                    </td>
                    <td>
                      <span className="text-xs line-clamp-1" style={{ color: 'var(--text-2)', maxWidth: 180, display: 'block' }}>
                        {c.product_name || '—'}
                      </span>
                    </td>
                    <td>
                      <span className="text-xs font-medium" style={{ color: 'var(--text-2)' }}>
                        {Number(c.old_seller_price).toLocaleString('ru')}₽
                      </span>
                    </td>
                    <td>
                      {c.new_seller_price ? (
                        <span className="text-xs font-bold" style={{ color: 'var(--text)' }}>
                          {Number(c.new_seller_price).toLocaleString('ru')}₽
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-3)' }}>—</span>
                      )}
                    </td>
                    <td>
                      <PriceDiff
                        oldP={Number(c.old_seller_price)}
                        newP={c.new_seller_price ? Number(c.new_seller_price) : null}
                      />
                    </td>
                    <td>
                      <div>
                        <ReasonBadge code={c.reason_code} />
                        {c.reason_message && (
                          <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)', maxWidth: 200 }}>
                            {c.reason_message}
                          </p>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {filtered.length !== changes.length && (
          <p className="text-xs mt-2 text-right" style={{ color: 'var(--text-3)' }}>
            Показано {filtered.length} из {changes.length}
          </p>
        )}
      </div>
    </div>
  );
}

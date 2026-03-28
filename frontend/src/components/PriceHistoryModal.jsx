import { useEffect, useState } from 'react';
import { X, TrendingUp, TrendingDown, Minus, Tag } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ReferenceDot,
} from 'recharts';
import { getProductHistory } from '../api/client';
import { formatMsk } from '../utils/date';

const CustomTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  return (
    <div style={{
      background: 'hsl(var(--card))',
      border: '1px solid hsl(var(--border))',
      borderRadius: 10,
      padding: '10px 14px',
      fontSize: 12,
      boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
    }}>
      <p className="font-semibold mb-1.5" style={{ color: 'var(--text)' }}>{label}</p>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 mb-0.5">
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.stroke, display: 'inline-block' }} />
          <span style={{ color: 'var(--text-2)' }}>{p.name}:</span>
          <span className="font-semibold" style={{ color: p.stroke }}>
            {Number(p.value).toLocaleString('ru')}₽
          </span>
        </div>
      ))}
      {d?.competitor_nm_id && (
        <div className="mt-0.5" style={{ color: 'var(--text-3)' }}>
          Конкурент: #{d.competitor_nm_id}
        </div>
      )}
      {d?.promo && (
        <div className="mt-1.5 flex items-center gap-1 text-orange-500">
          <Tag className="w-3 h-3" />
          <span>В акции</span>
        </div>
      )}
      {d?.discount != null && d.discount > 0 && (
        <div className="mt-0.5" style={{ color: 'var(--text-3)' }}>
          Скидка: {d.discount}%
        </div>
      )}
      {d?.spp != null && d.spp > 0 && (
        <div className="mt-0.5" style={{ color: 'var(--text-3)' }}>
          СПП: {d.spp}%
        </div>
      )}
      <div className="mt-0.5" style={{ color: d?.stock ? '#16a34a' : '#ef4444', fontWeight: 500 }}>
        {d?.stock ? 'В наличии' : 'Нет в наличии'}
      </div>
    </div>
  );
};

export default function PriceHistoryModal({ product, onClose }) {
  const [history, setHistory] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getProductHistory(product.id, 200)
      .then((res) => {
        const data = [...res.data.snapshots].reverse().map((s) => ({
          date: formatMsk(s.snapshot_at, 'dd.MM HH:mm'),
          seller: Math.round(Number(s.seller_price)),
          final: Math.round(Number(s.final_price)),
          competitor: s.competitor_price != null ? Math.round(Number(s.competitor_price)) : null,
          competitor_nm_id: s.competitor_nm_id,
          spp: s.spp != null ? Math.round(Number(s.spp)) : null,
          discount: s.discount != null ? Math.round(Number(s.discount)) : null,
          promo: s.is_in_promo,
          stock: s.in_stock,
        }));
        setHistory(data);
      })
      .catch(() => toast.error('Ошибка загрузки истории цен'))
      .finally(() => setLoading(false));
  }, [product.id]);

  const latest = history[history.length - 1];
  const first = history[0];
  const priceDiff = latest && first ? latest.final - first.final : 0;
  const promoCount = history.filter((h) => h.promo).length;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ maxWidth: 800, width: '95%', padding: '1.5rem' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text)' }}>
              История цен
            </h2>
            <p className="text-sm mt-0.5 font-medium text-primary">
              {product.seller_sku || `#${product.nm_id}`}
            </p>
            {product.name && (
              <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
                {product.name}
              </p>
            )}
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1.5">
            <X className="w-4 h-4" />
          </button>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <div className="spinner" />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Загрузка истории...</p>
          </div>
        ) : history.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <TrendingUp className="w-10 h-10 opacity-10" style={{ color: 'var(--text)' }} />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных по истории цен</p>
            <p className="text-xs" style={{ color: 'var(--text-3)' }}>
              Запустите репрайсер для этого товара
            </p>
          </div>
        ) : (
          <>
            {/* Summary cards */}
            <div className="grid grid-cols-4 gap-3 mb-5">
              {[
                {
                  label: 'Цена (СПП)',
                  value: `${latest?.final?.toLocaleString('ru')}₽`,
                  color: '#1d4ed8',
                },
                {
                  label: 'Цена продавца',
                  value: `${latest?.seller?.toLocaleString('ru')}₽`,
                  color: '#7c3aed',
                },
                {
                  label: 'Изменение',
                  value: `${priceDiff > 0 ? '+' : ''}${priceDiff.toLocaleString('ru')}₽`,
                  color: priceDiff > 0 ? '#ef4444' : priceDiff < 0 ? '#16a34a' : '#9ca3af',
                  icon:
                    priceDiff > 0 ? (
                      <TrendingUp className="w-3.5 h-3.5" />
                    ) : priceDiff < 0 ? (
                      <TrendingDown className="w-3.5 h-3.5" />
                    ) : (
                      <Minus className="w-3.5 h-3.5" />
                    ),
                },
                {
                  label: 'Снимков / акций',
                  value: `${history.length} / ${promoCount}`,
                  color: promoCount > 0 ? '#d97706' : '#9ca3af',
                },
              ].map((s, i) => (
                <div
                  key={i}
                  className="rounded-xl p-3 bg-muted/50 border border-border"
                >
                  <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>
                    {s.label}
                  </p>
                  <p
                    className="text-lg font-bold flex items-center gap-1"
                    style={{ color: s.color }}
                  >
                    {s.icon}
                    {s.value}
                  </p>
                </div>
              ))}
            </div>

            {/* Chart */}
            <ResponsiveContainer width="100%" height={270}>
              <LineChart data={history} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.15)" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: 'var(--text-3)' }}
                  tickLine={false}
                  interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fontSize: 10, fill: 'var(--text-3)' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v) => `${v.toLocaleString('ru')}₽`}
                  width={72}
                />
                <Tooltip content={<CustomTooltip />} />
                <Legend
                  formatter={(v) =>
                    v === 'seller' ? 'Цена продавца' :
                    v === 'final' ? 'Цена покупателя (СПП)' :
                    'Конкурент'
                  }
                  wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
                />
                {/* Promo markers */}
                {history
                  .filter((h) => h.promo)
                  .map((h, i) => (
                    <ReferenceDot
                      key={i}
                      x={h.date}
                      y={h.seller}
                      r={4}
                      fill="#f97316"
                      stroke="#fff"
                      strokeWidth={1.5}
                    />
                  ))}
                <Line
                  type="monotone"
                  dataKey="seller"
                  name="seller"
                  stroke="#7c3aed"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                />
                <Line
                  type="monotone"
                  dataKey="final"
                  name="final"
                  stroke="#16a34a"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  strokeDasharray="5 3"
                />
                <Line
                  type="monotone"
                  dataKey="competitor"
                  name="competitor"
                  stroke="#f97316"
                  strokeWidth={1.5}
                  dot={false}
                  activeDot={{ r: 4, strokeWidth: 0 }}
                  strokeDasharray="4 3"
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>

            {promoCount > 0 && (
              <p className="text-xs mt-2" style={{ color: '#f97316' }}>
                🟠 Оранжевые точки — участие в акции WB
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}

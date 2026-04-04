import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ArrowLeft, Play, Settings, ExternalLink, TrendingUp, TrendingDown,
  Minus, Package, Shield, Clock, BarChart3, AlertTriangle,
  CheckCircle, XCircle, ArrowUpRight, ArrowDownRight, Copy, Edit3, Store,
  Trash2,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area,
} from 'recharts';
import toast from 'react-hot-toast';
import { getProductDetail, getProductsBySku, runRepricer, updateProductConfig, createProductConfig, getSettings, getProductAuditLog, updateProductCost, deleteProduct } from '../api/client';
import { formatMsk } from '../utils/date';
import ConfigModal from '../components/ConfigModal';

const getWBImageUrl = (nmId) => {
  const nm = parseInt(nmId);
  if (!nm || isNaN(nm)) return null;
  const vol = Math.floor(nm / 100000);
  const part = Math.floor(nm / 1000);
  const baskets = [
    [143,'01'],[287,'02'],[431,'03'],[719,'04'],[1007,'05'],
    [1061,'06'],[1115,'07'],[1169,'08'],[1313,'09'],[1601,'10'],
    [1655,'11'],[1919,'12'],[2045,'13'],[2189,'14'],[2405,'15'],
    [2621,'16'],[2837,'17'],[3053,'18'],[3261,'19'],[3473,'20'],
    [3721,'21'],[4001,'22'],[4281,'23'],[4601,'24'],[5001,'25'],
  ];
  const basket = (baskets.find(([v]) => vol <= v) ?? [0,'26'])[1];
  return `https://basket-${basket}.wbbasket.ru/vol${vol}/part${part}/${nm}/images/c246x328/1.webp`;
};

const fmt = (n) => n != null ? Number(n).toLocaleString('ru') : '—';

const ChartTooltip = ({ active, payload, label }) => {
  if (!active || !payload?.length) return null;
  const d = payload[0]?.payload;
  return (
    <div style={{
      background: 'hsl(var(--card))', border: '1px solid hsl(var(--border))',
      borderRadius: 10, padding: '10px 14px', fontSize: 12,
      boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
    }}>
      <p className="font-semibold mb-1.5" style={{ color: 'var(--text)' }}>{label}</p>
      {payload.map((p) => (
        <div key={p.dataKey} className="flex items-center gap-2 mb-0.5">
          <span style={{ width: 8, height: 8, borderRadius: 2, background: p.stroke || p.fill, display: 'inline-block' }} />
          <span style={{ color: 'var(--text-2)' }}>{p.name}:</span>
          <span className="font-semibold" style={{ color: p.stroke || p.fill }}>
            {Number(p.value).toLocaleString('ru')}{p.dataKey === 'spp' ? '%' : '₽'}
          </span>
        </div>
      ))}
    </div>
  );
};

const ReasonBadge = ({ code }) => {
  const map = {
    UPDATED: { label: 'Обновлена', color: '#16a34a', bg: 'rgba(22,163,74,0.1)' },
    CLAMPED_TO_MIN: { label: 'Мин. цена', color: '#d97706', bg: 'rgba(217,119,6,0.1)' },
    CLAMPED_TO_MAX: { label: 'Макс. цена', color: '#d97706', bg: 'rgba(217,119,6,0.1)' },
    CLAMPED_TO_MARGIN: { label: 'Margin Guard', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
    SKIPPED: { label: 'Пропущена', color: '#9ca3af', bg: 'rgba(156,163,175,0.1)' },
    ERROR: { label: 'Ошибка', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
  };
  const info = map[code] || { label: code, color: '#9ca3af', bg: 'rgba(156,163,175,0.1)' };
  return (
    <span style={{
      fontSize: 11, fontWeight: 600, color: info.color,
      background: info.bg, padding: '2px 8px', borderRadius: 6,
    }}>
      {info.label}
    </span>
  );
};

export default function ProductDetailPage() {
  const { productId } = useParams();
  const navigate = useNavigate();
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [showConfig, setShowConfig] = useState(false);
  const [running, setRunning] = useState(false);
  const [chartTab, setChartTab] = useState('price'); // price | spp
  const [related, setRelated] = useState([]);
  const [auditLog, setAuditLog] = useState([]);
  const [editingCost, setEditingCost] = useState(false);
  const [costInput, setCostInput] = useState('');
  const [savingCost, setSavingCost] = useState(false);

  const load = async () => {
    try {
      const res = await getProductDetail(productId);
      setData(res.data);
      // Load related products (same SKU, other accounts)
      if (res.data?.product?.seller_sku) {
        try {
          const relRes = await getProductsBySku(res.data.product.seller_sku);
          setRelated(relRes.data?.products || []);
        } catch {}
      }
      try {
        const auditRes = await getProductAuditLog(productId);
        setAuditLog(auditRes.data?.logs || auditRes.data || []);
      } catch {}
    } catch {
      toast.error('Ошибка загрузки товара');
      navigate('/products');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [productId]);

  const handleRun = async () => {
    if (!data?.product?.seller_sku) return;
    setRunning(true);
    try {
      toast.loading('Запуск репрайсера...', { id: 'run' });
      await runRepricer([data.product.seller_sku]);
      toast.success('Репрайсер завершён', { id: 'run' });
      setTimeout(load, 2000);
    } catch {
      toast.error('Ошибка запуска', { id: 'run' });
    } finally {
      setRunning(false);
    }
  };

  const copyNmId = () => {
    navigator.clipboard.writeText(String(data.product.nm_id));
    toast.success('nmID скопирован');
  };

  const handleDelete = async () => {
    if (!confirm(`Удалить товар ${product.seller_sku || product.nm_id}?`)) return;
    try {
      await deleteProduct(productId);
      toast.success('Товар удален');
      navigate('/products');
    } catch {
      toast.error('Ошибка удаления товара');
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="spinner" />
      </div>
    );
  }

  if (!data) return null;

  const { product, config, current_price, margin, stats, snapshots, changes } = data;
  const imgUrl = product.image_url || getWBImageUrl(product.nm_id);

  // Chart data
  const chartData = snapshots.map((s) => ({
    date: formatMsk(s.snapshot_at, 'dd.MM HH:mm'),
    seller: Math.round(s.seller_price),
    final: Math.round(s.final_price),
    competitor: s.competitor_price ? Math.round(s.competitor_price) : null,
    spp: s.spp != null ? Math.round(Number(s.spp)) : null,
    discount: s.discount != null ? Math.round(Number(s.discount)) : null,
    stock: s.in_stock,
  }));

  const getMarginColor = (m) => {
    if (m === null || m === undefined) return 'var(--text-3)';
    if (m >= 30) return '#16a34a';
    if (m >= 10) return '#d97706';
    return '#dc2626';
  };

  const priceChange = changes.length > 0 ? changes[0] : null;
  const lastChangeDir = priceChange
    ? (priceChange.new_seller_price || 0) > priceChange.old_seller_price ? 'up' : 'down'
    : null;

  // For the config modal - build a product-like object
  const productForModal = {
    id: product.id,
    nm_id: product.nm_id,
    seller_sku: product.seller_sku,
    name: product.name,
    config: config,
  };

  return (
    <div>
      {/* Back button + header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate('/products')}
          className="btn btn-secondary p-2"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1">
          <h1 className="page-h1">{product.name || product.seller_sku}</h1>
          <div className="flex items-center gap-3 mt-1">
            <span className="text-sm" style={{ color: 'var(--text-3)' }}>
              Артикул: <strong style={{ color: 'var(--text-2)' }}>{product.seller_sku}</strong>
            </span>
            <button
              onClick={copyNmId}
              className="flex items-center gap-1 text-sm hover:text-primary transition-colors"
              style={{ color: 'var(--text-3)' }}
            >
              nmID: <strong style={{ color: 'var(--text-2)' }}>{product.nm_id}</strong>
              <Copy className="w-3 h-3" />
            </button>
            <a
              href={`https://www.wildberries.ru/catalog/${product.nm_id}/detail.aspx`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1 text-sm text-primary hover:underline"
            >
              WB <ExternalLink className="w-3 h-3" />
            </a>
          </div>
        </div>
        <button
          onClick={handleDelete}
          className="btn btn-danger gap-2"
          title="Удалить товар"
        >
          <Trash2 className="w-4 h-4" />
          Удалить
        </button>
        <button
          onClick={handleRun}
          disabled={running}
          className="btn btn-primary gap-2"
        >
          <Play className={`w-4 h-4 ${running ? 'animate-pulse' : ''}`} />
          {running ? 'Запуск...' : 'Запустить'}
        </button>
      </div>

      {/* Top info row: image + price cards + strategy */}
      <div className="grid grid-cols-12 gap-4 mb-4">
        {/* Product image */}
        <div className="col-span-2">
          <div className="card p-0 overflow-hidden" style={{ aspectRatio: '3/4' }}>
            {imgUrl ? (
              <img
                src={imgUrl}
                alt={product.name}
                className="w-full h-full object-cover"
                onError={(e) => { e.target.style.display = 'none'; }}
              />
            ) : (
              <div className="w-full h-full flex items-center justify-center bg-muted">
                <Package className="w-10 h-10" style={{ color: 'var(--text-3)' }} />
              </div>
            )}
          </div>
        </div>

        {/* Price & status cards */}
        <div className="col-span-6 grid grid-cols-3 gap-3 content-start">
          {/* Seller price */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Цена продавца</p>
            <p className="text-2xl font-bold" style={{ color: 'var(--text)' }}>
              {current_price ? `${fmt(current_price.seller_price)}₽` : '—'}
            </p>
            {lastChangeDir && (
              <div className="flex items-center gap-1 mt-1" style={{
                color: lastChangeDir === 'up' ? '#ef4444' : '#16a34a', fontSize: 12
              }}>
                {lastChangeDir === 'up' ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
                {priceChange.new_seller_price
                  ? `${lastChangeDir === 'up' ? '+' : ''}${fmt(priceChange.new_seller_price - priceChange.old_seller_price)}₽`
                  : ''}
              </div>
            )}
          </div>

          {/* Final price */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Цена СПП</p>
            <p className="text-2xl font-bold" style={{ color: '#7c3aed' }}>
              {current_price ? `${fmt(current_price.final_price)}₽` : '—'}
            </p>
            {current_price?.discount > 0 && (
              <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                Скидка прод.: {Math.round(current_price.discount)}%
              </p>
            )}
            {current_price?.spp > 0 && (
              <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                СПП: {Math.round(current_price.spp)}%
              </p>
            )}
          </div>

          {/* Margin */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Маржа</p>
            <p className="text-2xl font-bold" style={{ color: getMarginColor(margin.value) }}>
              {margin.value !== null ? `${margin.value}%` : '—'}
            </p>
            {editingCost ? (
              <div className="flex items-center gap-1 mt-1">
                <input
                  type="number"
                  value={costInput}
                  onChange={(e) => setCostInput(e.target.value)}
                  className="input text-xs px-1.5 py-0.5 w-24"
                  placeholder="₽"
                  min="0"
                  autoFocus
                />
                <button
                  className="btn btn-xs btn-primary"
                  disabled={savingCost}
                  onClick={async () => {
                    setSavingCost(true);
                    try {
                      // Заменяем запятую на точку для дробных чисел
                      const normalized = costInput.replace(',', '.');
                      const cost = parseFloat(normalized);
                      if (isNaN(cost) || cost <= 0) {
                        toast.error('Введите корректное число > 0');
                        setSavingCost(false);
                        return;
                      }
                      if (import.meta.env.DEV) console.log('Sending cost:', cost, 'for product:', product.id);
                      const res = await updateProductCost(product.id, cost);
                      if (import.meta.env.DEV) console.log('Response:', res);
                      toast.success('Себестоимость сохранена');
                      setEditingCost(false);
                      load();
                    } catch (err) {
                      console.error('Save cost error:', err);
                      const msg = err.response?.data?.detail || err.message || 'Ошибка сохранения';
                      toast.error(msg);
                    }
                    finally { setSavingCost(false); }
                  }}
                >
                  ОК
                </button>
                <button className="btn btn-xs btn-secondary" onClick={() => setEditingCost(false)}>✕</button>
              </div>
            ) : (
              <p
                className="text-xs mt-1 cursor-pointer hover:text-primary transition-colors"
                style={{ color: 'var(--text-3)' }}
                onClick={() => { setCostInput(margin.cost_price || ''); setEditingCost(true); }}
                title="Нажмите для редактирования"
              >
                {margin.cost_price ? `Себестоимость: ${fmt(margin.cost_price)}₽` : '+ Добавить себестоимость'}
                {margin.fees_total ? ` | Расходы: ${fmt(margin.fees_total)}₽` : ''}
                <Edit3 className="inline w-3 h-3 ml-1 opacity-50" />
              </p>
            )}
          </div>

          {/* Competitor price */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Конкурент</p>
            <p className="text-xl font-bold" style={{ color: '#f97316' }}>
              {current_price?.competitor_price ? `${fmt(current_price.competitor_price)}₽` : '—'}
            </p>
            {current_price?.competitor_nm_id && (
              <a
                href={`https://www.wildberries.ru/catalog/${current_price.competitor_nm_id}/detail.aspx`}
                target="_blank"
                rel="noopener noreferrer"
                className="text-xs text-primary hover:underline mt-1 inline-flex items-center gap-1"
              >
                #{current_price.competitor_nm_id} <ExternalLink className="w-3 h-3" />
              </a>
            )}
          </div>

          {/* Price range 30d */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Диапазон 30д</p>
            <p className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              {stats.price_min_30d ? `${fmt(stats.price_min_30d)}₽ — ${fmt(stats.price_max_30d)}₽` : '—'}
            </p>
            {stats.price_min_30d && stats.price_max_30d && (
              <div className="mt-2 h-1.5 rounded-full bg-muted overflow-hidden">
                <div
                  className="h-full rounded-full"
                  style={{
                    width: current_price?.seller_price
                      ? `${Math.min(100, Math.max(5, ((current_price.seller_price - stats.price_min_30d) / (stats.price_max_30d - stats.price_min_30d)) * 100))}%`
                      : '50%',
                    background: 'linear-gradient(90deg, #16a34a, #d97706, #ef4444)',
                  }}
                />
              </div>
            )}
          </div>

          {/* Changes stats */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Изменений цен</p>
            <p className="text-xl font-bold" style={{ color: 'var(--text)' }}>
              {stats.total_changes}
            </p>
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
              За 7 дней: <strong style={{ color: stats.changes_7d > 0 ? '#7c3aed' : 'var(--text-3)' }}>{stats.changes_7d}</strong>
            </p>
          </div>
        </div>

        {/* Strategy card */}
        <div className="col-span-4">
          <div className="card h-full">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                Стратегия
              </h3>
              <button onClick={() => setShowConfig(true)} className="btn btn-xs btn-secondary gap-1">
                <Edit3 className="w-3 h-3" /> Изменить
              </button>
            </div>

            {!config ? (
              <div className="flex flex-col items-center justify-center py-8 gap-3">
                <AlertTriangle className="w-8 h-8" style={{ color: '#d97706' }} />
                <p className="text-sm" style={{ color: 'var(--text-3)' }}>Стратегия не настроена</p>
                <button onClick={() => setShowConfig(true)} className="btn btn-sm btn-primary">
                  Настроить
                </button>
              </div>
            ) : (
              <div className="space-y-3">
                {/* Status */}
                <div className="flex items-center gap-2">
                  {config.is_active ? (
                    <CheckCircle className="w-4 h-4 text-green-500" />
                  ) : (
                    <XCircle className="w-4 h-4 text-red-500" />
                  )}
                  <span className="text-sm font-medium" style={{
                    color: config.is_active ? '#16a34a' : '#ef4444'
                  }}>
                    {config.is_active ? 'Активен' : 'Отключён'}
                  </span>
                </div>

                {/* Strategy type */}
                <div className="p-3 rounded-lg bg-muted/50 border border-border">
                  <div className="flex items-center gap-2 mb-2">
                    <span className="badge badge-info">
                      {config.strategy === 'rrp' ? 'РРЦ' : 'Конкурент'}
                    </span>
                  </div>
                  {config.strategy === 'rrp' ? (
                    <div className="space-y-1 text-sm">
                      <div className="flex justify-between">
                        <span style={{ color: 'var(--text-3)' }}>РРЦ:</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>{fmt(config.rrp_price)}₽</span>
                      </div>
                      <div className="flex justify-between">
                        <span style={{ color: 'var(--text-3)' }}>Допуск:</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>±{fmt(config.rrp_tolerance)}₽</span>
                      </div>
                      <div className="flex justify-between">
                        <span style={{ color: 'var(--text-3)' }}>Кошелёк WB:</span>
                        <span className="font-medium" style={{ color: config.rrp_consider_wallet ? '#16a34a' : 'var(--text-3)' }}>
                          {config.rrp_consider_wallet ? 'Учитывается' : 'Нет'}
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="space-y-1 text-sm">
                      {config.competitors?.length > 0 && (
                        <div>
                          <span style={{ color: 'var(--text-3)' }}>Конкуренты:</span>
                          <div className="mt-1 space-y-1">
                            {config.competitors.map((c, i) => (
                              <a
                                key={i}
                                href={`https://www.wildberries.ru/catalog/${c.nm_id}/detail.aspx`}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="flex items-center gap-1 text-primary hover:underline text-xs"
                              >
                                #{c.nm_id} {c.alias && `(${c.alias})`} <ExternalLink className="w-3 h-3" />
                              </a>
                            ))}
                          </div>
                        </div>
                      )}
                      <div className="flex justify-between">
                        <span style={{ color: 'var(--text-3)' }}>Смещение:</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>
                          {config.competitor_offset_direction === 'below' ? '−' : '+'}{config.competitor_offset_value}
                          {config.competitor_offset_type === 'pct' ? '%' : '₽'}
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span style={{ color: 'var(--text-3)' }}>Правило:</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>
                          {config.competitor_select_rule === 'min_price' ? 'Минимальная' :
                           config.competitor_select_rule === 'avg_price' ? 'Средняя' : 'Мин. в наличии'}
                        </span>
                      </div>
                    </div>
                  )}
                </div>

                {/* Price limits */}
                <div className="flex gap-3 text-sm">
                  <div className="flex-1 p-2 rounded bg-muted/50 text-center">
                    <p className="text-xs" style={{ color: 'var(--text-3)' }}>Мин.</p>
                    <p className="font-semibold" style={{ color: 'var(--text)' }}>{fmt(config.min_price)}₽</p>
                  </div>
                  <div className="flex-1 p-2 rounded bg-muted/50 text-center">
                    <p className="text-xs" style={{ color: 'var(--text-3)' }}>Макс.</p>
                    <p className="font-semibold" style={{ color: 'var(--text)' }}>
                      {config.max_price ? `${fmt(config.max_price)}₽` : '∞'}
                    </p>
                  </div>
                </div>

                {config.updated_at && (
                  <p className="text-xs" style={{ color: 'var(--text-3)' }}>
                    Обновлено: {formatMsk(config.updated_at, 'dd.MM.yyyy HH:mm')}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      </div>


      {/* Related accounts block — same SKU across different accounts */}
      {related.length > 1 && (
        <div className="card mb-4">
          <div className="flex items-center gap-2 mb-3">
            <Store className="w-4 h-4" style={{ color: 'var(--text-3)' }} />
            <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Магазины
              <span className="font-normal ml-2 text-xs" style={{ color: 'var(--text-3)' }}>
                {related.length} аккаунта с артикулом {product.seller_sku}
              </span>
            </h3>
          </div>
          <div className="flex flex-wrap gap-3">
            {related.map((r) => (
              <div
                key={r.id}
                onClick={() => r.id !== product.id && navigate(`/products/${r.id}`)}
                className="flex-1 min-w-48 rounded-xl border p-3 transition-all"
                style={{
                  borderColor: r.id === product.id ? 'var(--primary)' : 'var(--border)',
                  background: r.id === product.id ? 'rgba(var(--primary-rgb),0.06)' : 'var(--muted)',
                  cursor: r.id !== product.id ? 'pointer' : 'default',
                  opacity: r.is_active === false ? 0.6 : 1,
                }}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                    {r.account_name || 'Аккаунт'}
                  </span>
                  <div className="flex items-center gap-1.5">
                    {r.is_active === true && (
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#16a34a', display: 'inline-block' }} />
                    )}
                    {r.is_active === false && (
                      <span style={{ width: 7, height: 7, borderRadius: '50%', background: '#9ca3af', display: 'inline-block' }} />
                    )}
                    {r.strategy && (
                      <span className="badge badge-info" style={{ fontSize: 10, padding: '1px 6px' }}>
                        {r.strategy === 'rrp' ? 'РРЦ' : 'Конкурент'}
                      </span>
                    )}
                  </div>
                </div>
                <div className="flex items-baseline gap-1 mb-1">
                  <span className="text-lg font-bold" style={{ color: 'var(--text)' }}>
                    {r.current_seller_price ? `${Math.round(r.current_seller_price).toLocaleString('ru')}₽` : '—'}
                  </span>
                  {r.current_final_price && (
                    <span className="text-xs" style={{ color: '#7c3aed' }}>
                      / {Math.round(r.current_final_price).toLocaleString('ru')}₽ СПП
                    </span>
                  )}
                </div>
                <div className="flex gap-2 text-xs" style={{ color: 'var(--text-3)' }}>
                  <span>Мин: {r.min_price ? `${Math.round(r.min_price).toLocaleString('ru')}₽` : '—'}</span>
                  <span>·</span>
                  <span>Макс: {r.max_price ? `${Math.round(r.max_price).toLocaleString('ru')}₽` : '∞'}</span>
                </div>
                <div className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                  nmID: {r.nm_id}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Status badges row */}
      {current_price && (
        <div className="flex gap-2 mb-4">
          <span className={`badge ${current_price.in_stock ? 'badge-success' : 'badge-warning'}`}>
            {current_price.in_stock ? 'В наличии' : 'Нет в наличии'}
          </span>
          {config?.is_active && (
            <span className="badge badge-success">Репрайсер активен</span>
          )}
          {current_price.snapshot_at && (
            <span className="badge" style={{ background: 'var(--muted)', color: 'var(--text-3)' }}>
              Обновлено: {formatMsk(current_price.snapshot_at, 'dd.MM HH:mm')}
            </span>
          )}
        </div>
      )}

      {/* Price chart */}
      <div className="card mb-4">
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            Динамика цен — 30 дней
          </h3>
          <div className="flex rounded-lg overflow-hidden border border-border">
            {[
              { id: 'price', label: 'Цены' },
              { id: 'spp', label: 'СПП %' },
            ].map((t) => (
              <button
                key={t.id}
                onClick={() => setChartTab(t.id)}
                className={`px-3 py-1 text-xs font-medium transition-colors ${
                  chartTab === t.id
                    ? 'bg-primary/10 text-primary'
                    : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>

        {chartData.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <BarChart3 className="w-8 h-8" style={{ color: 'var(--text-3)', opacity: 0.3 }} />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных за последние 30 дней</p>
          </div>
        ) : chartTab === 'price' ? (
          <ResponsiveContainer width="100%" height={320}>
            <LineChart data={chartData} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.15)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v.toLocaleString('ru')}₽`} width={72} />
              <Tooltip content={<ChartTooltip />} />
              <Legend formatter={(v) => v === 'seller' ? 'Цена продавца' : v === 'final' ? 'Цена покупателя' : 'Конкурент'} wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
              <Line type="monotone" dataKey="seller" name="seller" stroke="#7c3aed" strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} />
              <Line type="monotone" dataKey="final" name="final" stroke="#16a34a" strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} strokeDasharray="5 3" />
              <Line type="monotone" dataKey="competitor" name="competitor" stroke="#f97316" strokeWidth={1.5} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} strokeDasharray="4 3" connectNulls={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <ResponsiveContainer width="100%" height={320}>
            <AreaChart data={chartData} margin={{ top: 5, right: 16, left: 0, bottom: 5 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.15)" />
              <XAxis dataKey="date" tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }} tickLine={false} axisLine={false} tickFormatter={(v) => `${v}%`} width={48} />
              <Tooltip content={<ChartTooltip />} />
              <Area type="monotone" dataKey="spp" name="СПП" stroke="#7c3aed" fill="rgba(124,58,237,0.1)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Price changes history */}
      <div className="card">
        <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>
          История изменений цен
          <span className="font-normal ml-2" style={{ color: 'var(--text-3)' }}>
            (последние 10)
          </span>
        </h3>

        {changes.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-8 gap-2">
            <Clock className="w-8 h-8" style={{ color: 'var(--text-3)', opacity: 0.3 }} />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Изменений пока нет</p>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="dark-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Старая цена</th>
                  <th>Новая цена</th>
                  <th>Изменение</th>
                  <th>Причина</th>
                  <th>Комментарий</th>
                </tr>
              </thead>
              <tbody>
                {changes.map((c) => {
                  const diff = c.new_seller_price
                    ? c.new_seller_price - c.old_seller_price
                    : null;
                  return (
                    <tr key={c.id}>
                      <td className="text-xs whitespace-nowrap">
                        {formatMsk(c.changed_at, 'dd.MM.yyyy HH:mm')}
                      </td>
                      <td className="font-medium">{fmt(c.old_seller_price)}₽</td>
                      <td className="font-medium">
                        {c.new_seller_price ? `${fmt(c.new_seller_price)}₽` : '—'}
                      </td>
                      <td>
                        {diff !== null ? (
                          <span className="flex items-center gap-1 font-semibold text-sm" style={{
                            color: diff > 0 ? '#ef4444' : diff < 0 ? '#16a34a' : 'var(--text-3)'
                          }}>
                            {diff > 0 ? <TrendingUp className="w-3.5 h-3.5" /> : diff < 0 ? <TrendingDown className="w-3.5 h-3.5" /> : <Minus className="w-3.5 h-3.5" />}
                            {diff > 0 ? '+' : ''}{fmt(diff)}₽
                          </span>
                        ) : '—'}
                      </td>
                      <td><ReasonBadge code={c.reason_code} /></td>
                      <td className="text-xs" style={{ color: 'var(--text-3)', maxWidth: 200 }}>
                        <span className="line-clamp-2">{c.reason_message || '—'}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Audit log — история изменений конфигурации */}
      {auditLog.length > 0 && (
        <div className="card mt-4">
          <h3 className="text-sm font-semibold mb-4" style={{ color: 'var(--text)' }}>
            История изменений настроек
            <span className="font-normal ml-2" style={{ color: 'var(--text-3)' }}>
              (последние {auditLog.length})
            </span>
          </h3>
          <div className="overflow-x-auto">
            <table className="dark-table">
              <thead>
                <tr>
                  <th>Дата</th>
                  <th>Поле</th>
                  <th>Было</th>
                  <th>Стало</th>
                </tr>
              </thead>
              <tbody>
                {auditLog.map((entry, i) => (
                  <tr key={i}>
                    <td className="text-xs whitespace-nowrap">
                      {formatMsk(entry.changed_at || entry.created_at, 'dd.MM.yyyy HH:mm')}
                    </td>
                    <td className="font-medium text-xs" style={{ color: '#7c3aed' }}>
                      {entry.field_name || entry.event_type || '—'}
                    </td>
                    <td className="text-xs" style={{ color: 'var(--text-3)' }}>
                      {entry.old_value != null ? String(entry.old_value) : '—'}
                    </td>
                    <td className="text-xs font-medium" style={{ color: 'var(--text)' }}>
                      {entry.new_value != null ? String(entry.new_value) : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {showConfig && (
        <ConfigModal
          product={productForModal}
          onClose={() => { setShowConfig(false); load(); }}
        />
      )}
    </div>
  );
}

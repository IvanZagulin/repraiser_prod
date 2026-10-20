import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { productsListUrl } from '../utils/productsNav';
import {
  ArrowLeft, Play, Settings, ExternalLink, TrendingUp, TrendingDown,
  Minus, Package, Shield, Clock, BarChart3, AlertTriangle,
  CheckCircle, XCircle, ArrowUpRight, ArrowDownRight, Copy, Edit3, Store,
  Trash2, Moon,
} from 'lucide-react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, AreaChart, Area,
} from 'recharts';
import toast from 'react-hot-toast';
import { getProductDetail, getProductsBySku, runRepricer, updateProductConfig, createProductConfig, getSettings, getProductAuditLog, updateProductCost, deleteProduct, toggleStrategyBySku } from '../api/client';
import { formatMsk } from '../utils/date';
import { configCompetitors } from '../utils/competitors';
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
const fmtCommission = (type, value) => {
  if (value == null) return '—';
  return type === 'pct' ? `${Number(value).toLocaleString('ru')}%` : `${fmt(value)}₽`;
};

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
    CLAMPED_TO_MARGIN: { label: 'Защита маржи', color: '#ef4444', bg: 'rgba(239,68,68,0.1)' },
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
  const [toggling, setToggling] = useState(false);
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
      navigate(productsListUrl());
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
    toast.success(data.product.account_marketplace === 'ozon' ? 'Ozon product_id скопирован' : 'Артикул WB скопирован');
  };

  const handleDelete = async () => {
    if (!confirm(`Удалить товар ${product.seller_sku || product.nm_id}?`)) return;
    try {
      await deleteProduct(productId);
      toast.success('Товар удален');
      navigate(productsListUrl());
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

  const { product, config, current_price, margin, stats, snapshots, changes, ozon_live } = data;
  const isOzon = product.account_marketplace === 'ozon';
  const competitors = configCompetitors(config);
  const imgUrl = product.image_url || (isOzon ? null : getWBImageUrl(product.nm_id));

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
    account_marketplace: product.account_marketplace,
    config: config,
  };

  return (
    <div>
      {/* Back button + header */}
      <div className="flex items-center gap-3 mb-6">
        <button
          onClick={() => navigate(productsListUrl())}
          className="btn btn-secondary p-2"
        >
          <ArrowLeft className="w-4 h-4" />
        </button>
        <div className="flex-1">
          <h1 className="page-h1">{product.name || product.seller_sku}</h1>
          {product.brand && (
            <p className="text-sm mt-0.5" style={{ color: 'var(--text-3)' }}>{product.brand}</p>
          )}
          <div className="flex items-center gap-3 mt-1">
            <span className="text-sm" style={{ color: 'var(--text-3)' }}>
              Артикул: <strong style={{ color: 'var(--text-2)' }}>{product.seller_sku}</strong>
            </span>
            {product.subject_name && (
              <span className="text-sm" style={{ color: 'var(--text-3)' }}>
                Предмет: <strong style={{ color: 'var(--text-2)' }}>{product.subject_name}</strong>
              </span>
            )}
            <button
              onClick={copyNmId}
              className="flex items-center gap-1 text-sm hover:text-primary transition-colors"
              style={{ color: 'var(--text-3)' }}
            >
              {isOzon ? 'Ozon product_id' : 'Арт. WB'}: <strong style={{ color: 'var(--text-2)' }}>{product.nm_id}</strong>
              <Copy className="w-3 h-3" />
            </button>
            {!isOzon && (
              <a
                href={`https://www.wildberries.ru/catalog/${product.nm_id}/detail.aspx`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-sm text-primary hover:underline"
              >
                WB <ExternalLink className="w-3 h-3" />
              </a>
            )}
            {/* В адресе карточки Ozon стоит публичный SKU, а не product_id из
                nm_id: по product_id откроется чужой товар. Пока SKU не
                подтянулся прогоном, ссылку не показываем. */}
            {isOzon && product.ozon_sku && (
              <a
                href={`https://www.ozon.ru/product/${product.ozon_sku}/`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-1 text-sm text-primary hover:underline"
              >
                Ozon <ExternalLink className="w-3 h-3" />
              </a>
            )}
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
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>
              {isOzon ? 'Маркетинговая цена' : 'Цена СПП'}
            </p>
            <p className="text-2xl font-bold" style={{ color: '#7c3aed' }}>
              {current_price ? `${fmt(current_price.final_price)}₽` : '—'}
            </p>
            {current_price?.discount > 0 && (
              <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                {isOzon ? 'Соинвест.:' : 'Скидка прод.:'} {Math.round(current_price.discount)}%
              </p>
            )}
            {!isOzon && current_price?.spp > 0 && (
              <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                СПП: {Math.round(current_price.spp)}%
              </p>
            )}
            {isOzon && current_price?.ozon_price_index > 0 && (
              <p className="text-xs mt-1" style={{
                color: current_price.ozon_color_index === 'GREEN' ? '#16a34a'
                  : current_price.ozon_color_index === 'RED' ? '#ef4444'
                  : 'var(--text-3)'
              }}>
                Индекс: {current_price.ozon_price_index.toFixed(2)}
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
                      const normalized = String(costInput).replace(',', '.');
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
                onClick={() => { setCostInput(String(margin.cost_price ?? '')); setEditingCost(true); }}
                title="Нажмите для редактирования"
              >
                {margin.cost_price ? `Себестоимость: ${fmt(margin.cost_price)}₽` : '+ Добавить себестоимость'}
                {margin.fees_total ? ` | Расходы: ${fmt(margin.fees_total)}₽` : ''}
                <Edit3 className="inline w-3 h-3 ml-1 opacity-50" />
              </p>
            )}
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
              Комиссия: {fmtCommission(margin.commission_type, margin.commission_value)}
              {margin.commission_source === 'subject' ? ' · по предмету' : ' · из настроек'}
            </p>
          </div>

          {/* Ozon live pricing */}
          {isOzon && ozon_live && (
            <div className="card col-span-3">
              <p className="text-xs font-medium mb-2" style={{ color: 'var(--text-3)' }}>Ozon ценообразование (live)</p>
              <div className="grid grid-cols-4 gap-3 text-sm">
                <div>
                  <p className="text-xs" style={{ color: 'var(--text-3)' }}>Ваша цена</p>
                  <p className="font-semibold">{fmt(ozon_live.seller_price)}₽</p>
                </div>
                <div>
                  <p className="text-xs" style={{ color: 'var(--text-3)' }}>Маркетинговая</p>
                  <p className="font-semibold" style={{ color: ozon_live.marketing_seller_price < ozon_live.seller_price ? '#f97316' : 'var(--text)' }}>
                    {fmt(ozon_live.marketing_seller_price)}₽
                    {ozon_live.marketing_seller_price < ozon_live.seller_price && (
                      <span className="text-xs ml-1">(-{Math.round((1 - ozon_live.marketing_seller_price / ozon_live.seller_price) * 100)}%)</span>
                    )}
                  </p>
                </div>
                <div>
                  <p className="text-xs" style={{ color: 'var(--text-3)' }}>Цена до скидки</p>
                  <p className="font-semibold">{fmt(ozon_live.old_price)}₽</p>
                </div>
                <div>
                  <p className="text-xs" style={{ color: 'var(--text-3)' }}>Мин. цена</p>
                  <p className="font-semibold">{fmt(ozon_live.min_price)}₽</p>
                </div>
              </div>
              {ozon_live.price_index > 0 && (
                <div className="mt-2 flex items-center gap-2">
                  <span className="text-xs" style={{ color: 'var(--text-3)' }}>Индекс цены:</span>
                  <span className="text-xs font-bold px-1.5 py-0.5 rounded" style={{
                    background: (ozon_live.color_index || '').includes('GREEN') ? '#dcfce7'
                      : (ozon_live.color_index || '').includes('RED') ? '#fee2e2'
                      : '#fef9c3',
                    color: (ozon_live.color_index || '').includes('GREEN') ? '#16a34a'
                      : (ozon_live.color_index || '').includes('RED') ? '#ef4444'
                      : '#ca8a04',
                  }}>
                    {ozon_live.price_index.toFixed(2)}
                  </span>
                  {ozon_live.ozon_min_price > 0 && (
                    <span className="text-xs" style={{ color: 'var(--text-3)' }}>
                      Мин. на Ozon: {fmt(ozon_live.ozon_min_price)}₽
                    </span>
                  )}
                  {ozon_live.external_min_price > 0 && (
                    <span className="text-xs" style={{ color: 'var(--text-3)' }}>
                      Мин. внешн.: {fmt(ozon_live.external_min_price)}₽
                    </span>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Competitor price — only for competitor strategy */}
          {config?.strategy === 'competitor' && (
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
          )}

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

          {/* Stock */}
          <div className="card">
            <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Остаток</p>
            {current_price?.total_quantity != null ? (
              <>
                <p className="text-2xl font-bold" style={{ color: current_price.total_quantity > 0 ? '#22c55e' : '#ef4444' }}>
                  {current_price.total_quantity > 0 ? current_price.total_quantity : '0'}
                </p>
                <p className="text-xs mt-1" style={{ color: current_price.total_quantity > 0 ? '#22c55e' : '#ef4444' }}>
                  {current_price.total_quantity > 0 ? 'шт. в наличии' : 'Нет в наличии'}
                </p>
              </>
            ) : (
              <p className="text-2xl font-bold" style={{ color: 'var(--text-3)' }}>—</p>
            )}
          </div>
        </div>

        {/* Strategy card */}
        <div className="col-span-4 self-start">
          <div className="card p-0 overflow-hidden">

            {/* Header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-border">
              <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>Стратегия</h3>
              <button onClick={() => setShowConfig(true)} className="btn btn-xs btn-secondary gap-1">
                <Edit3 className="w-3 h-3" /> Изменить
              </button>
            </div>

            {!config ? (
              <div className="flex flex-col items-center justify-center py-10 gap-3 px-4">
                <AlertTriangle className="w-8 h-8" style={{ color: '#d97706' }} />
                <p className="text-sm" style={{ color: 'var(--text-3)' }}>Стратегия не настроена</p>
                <button onClick={() => setShowConfig(true)} className="btn btn-sm btn-primary">Настроить</button>
              </div>
            ) : (
              <div className="px-4 py-3 space-y-3">

                {/* Status + type badges */}
                <div className="flex items-center gap-2">
                  <span className={`badge ${config.is_active ? 'badge-success' : 'badge-error'} flex items-center gap-1`}>
                    {config.is_active
                      ? <CheckCircle className="w-3 h-3" />
                      : <XCircle className="w-3 h-3" />}
                    {config.is_active ? 'Активен' : 'Отключён'}
                  </span>
                  <span className="badge badge-info">
                    {config.strategy === 'rrp' ? 'РРЦ' : 'Конкурент'}
                  </span>
                </div>

                {/* Strategy details */}
                <div className="space-y-2 text-sm">
                  {config.strategy === 'rrp' ? (
                    <>
                      <div className="flex justify-between items-center">
                        <span style={{ color: 'var(--text-3)' }}>РРЦ</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>
                          {config.rrp_price ? fmt(config.rrp_price) + '₽' : '—'}
                        </span>
                      </div>
                      {config.rrp_price && (
                        <div className="flex justify-between items-center">
                          <span style={{ color: 'var(--text-3)' }} title="Ниже РРЦ цена не опускается; выше коридора репрайсер возвращает её к РРЦ">Коридор</span>
                          <span className="font-medium" style={{ color: 'var(--text)' }}>
                            {fmt(config.rrp_price)}₽ … +{fmt(Math.max(Number(config.rrp_tolerance) || 0, 3))}₽
                          </span>
                        </div>
                      )}
                      {config.rrp_consider_wallet && (
                        <div className="flex justify-between items-center">
                          <span style={{ color: 'var(--text-3)' }}>Кошелёк WB</span>
                          <span className="text-xs font-medium text-green-600">Учитывается</span>
                        </div>
                      )}
                    </>
                  ) : (
                    <>
                      {competitors.length > 0 && (
                        <div className="space-y-1">
                          {competitors.map((comp, i) => (
                            <a key={i}
                              href={`https://www.wildberries.ru/catalog/${comp.nm_id}/detail.aspx`}
                              target="_blank" rel="noopener noreferrer"
                              className="flex items-center gap-1 text-primary hover:underline text-xs">
                              #{comp.nm_id}{comp.alias && ` (${comp.alias})`}
                              <ExternalLink className="w-3 h-3" />
                            </a>
                          ))}
                        </div>
                      )}
                      <div className="flex justify-between items-center">
                        <span style={{ color: 'var(--text-3)' }}>Смещение</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>
                          {config.competitor_offset_value == null || Number(config.competitor_offset_value) === 0
                            ? 'Как у конкурента'
                            : `${config.competitor_offset_direction === 'below' ? '−' : '+'}${config.competitor_offset_value}${config.competitor_offset_type === 'pct' ? '%' : '₽'}`}
                        </span>
                      </div>
                      <div className="flex justify-between items-center">
                        <span style={{ color: 'var(--text-3)' }}>Правило</span>
                        <span className="font-medium" style={{ color: 'var(--text)' }}>
                          {config.competitor_select_rule === 'min_price' ? 'Мин. цена' :
                           config.competitor_select_rule === 'avg_price' ? 'Средняя' : 'Мин. в наличии'}
                        </span>
                      </div>
                      {config.rrp_price > 0 && (
                        <div className="flex justify-between items-center">
                          <span style={{ color: "var(--text-3)" }}>РРЦ (при OOS)</span>
                          <span className="font-medium" style={{ color: "var(--text)" }}>{fmt(config.rrp_price)}₽</span>
                        </div>
                      )}
                    </>
                  )}
                </div>

                {/* Price limits */}
                <div className="grid grid-cols-2 gap-2 pt-3 border-t border-border/60">
                  <div className="rounded-lg p-2.5 text-center" style={{ background: 'hsl(var(--border) / 0.5)' }}>
                    <p className="text-xs mb-0.5" style={{ color: 'var(--text-3)' }}>Мин. цена</p>
                    <p className="text-sm font-semibold" style={{ color: config.min_price ? 'var(--text)' : 'var(--text-3)' }}>
                      {config.min_price ? fmt(config.min_price) + '₽' : 'Откл.'}
                    </p>
                  </div>
                  <div className="rounded-lg p-2.5 text-center" style={{ background: 'hsl(var(--border) / 0.5)' }}>
                    <p className="text-xs mb-0.5" style={{ color: 'var(--text-3)' }}>Макс. цена</p>
                    <p className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                      {config.max_price ? fmt(config.max_price) + '₽' : '∞'}
                    </p>
                  </div>
                </div>

                {/* SPP tiers */}
                {config.rrp_spp_tiers?.length > 0 && (
                  <div className="pt-3 border-t border-border/60 space-y-2">
                    <div className="flex items-center gap-1.5">
                      <TrendingUp className="w-3.5 h-3.5 text-amber-500" />
                      <span className="text-xs font-semibold" style={{ color: 'var(--text)' }}>Ступени по СПП</span>
                      <span className="ml-auto text-xs px-1.5 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300 font-medium">
                        {config.rrp_spp_tiers.length} шт.
                      </span>
                    </div>
                    <div className="space-y-1">
                      {config.rrp_spp_tiers.map((t, i) => (
                        <div key={i} className="flex items-center justify-between text-xs">
                          <span className="font-mono px-2 py-0.5 rounded-md" style={{ background: 'hsl(var(--border) / 0.5)', color: 'var(--text-2)' }}>
                            {t.spp_min}%{t.spp_max != null ? `→${t.spp_max}%` : '→∞'}
                          </span>
                          <span className="font-semibold" style={{ color: 'var(--text)' }}>
                            {t.seller_price != null ? fmt(t.seller_price) + '₽' : 'РРЦ ' + fmt(t.rrp_price) + '₽'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Extra options */}
                {(config.skip_margin_check || config.night_price_enabled) && (
                  <div className="flex flex-wrap gap-1.5 pt-3 border-t border-border/60">
                    {config.skip_margin_check && (
                      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-amber-100 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-800" style={{ color: '#b45309' }}>
                        <Shield className="w-3 h-3" /> Без защиты маржи
                      </span>
                    )}
                    {config.night_price_enabled && (
                      <span className="inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full bg-indigo-100 dark:bg-indigo-900/30 border border-indigo-200 dark:border-indigo-800" style={{ color: '#4f46e5' }}>
                        <Moon className="w-3 h-3" />
                        {config.night_start_msk}–{config.night_end_msk} → {fmt(config.night_seller_price)}₽
                      </span>
                    )}
                  </div>
                )}

                {/* Updated at */}
                {config.updated_at && (
                  <p className="text-xs pt-3 border-t border-border/40" style={{ color: 'var(--text-3)' }}>
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
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Store className="w-4 h-4" style={{ color: 'var(--text-3)' }} />
              <h3 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                Магазины
                <span className="font-normal ml-2 text-xs" style={{ color: 'var(--text-3)' }}>
                  {related.length} аккаунта с артикулом {product.seller_sku}
                </span>
              </h3>
            </div>
            {(() => {
              const allActive = related.every(r => r.is_active !== false);
              return (
                <button
                  disabled={toggling}
                  onClick={async () => {
                    setToggling(true);
                    try {
                      await toggleStrategyBySku(product.seller_sku, !allActive);
                      toast.success(allActive ? 'Стратегия отключена на всех ЛК' : 'Стратегия включена на всех ЛК');
                      await load();
                    } catch { toast.error('Ошибка переключения'); }
                    finally { setToggling(false); }
                  }}
                  className={`btn btn-xs gap-1.5 ${allActive ? 'btn-secondary' : 'btn-primary'}`}
                >
                  {allActive ? <XCircle className="w-3 h-3" /> : <CheckCircle className="w-3 h-3" />}
                  {toggling ? '...' : allActive ? 'Отключить все' : 'Включить все'}
                </button>
              );
            })()}
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
                  Арт. WB: {r.nm_id}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Status badges row */}
      {current_price && (
        <div className="flex gap-2 mb-4">
          <span className={`badge ${current_price.in_stock ? 'badge-success' : 'badge-danger'}`}
            style={!current_price.in_stock ? { background: 'rgba(239,68,68,.12)', color: '#ef4444', border: '1px solid rgba(239,68,68,.3)' } : {}}>
            {current_price.in_stock
              ? `В наличии${current_price.total_quantity != null ? ` · ${current_price.total_quantity} шт.` : ''}`
              : 'Нет в наличии'}
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
              {config?.strategy === 'competitor' && (
                <Line type="monotone" dataKey="competitor" name="competitor" stroke="#f97316" strokeWidth={1.5} dot={false} activeDot={{ r: 4, strokeWidth: 0 }} strokeDasharray="4 3" connectNulls={false} />
              )}
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

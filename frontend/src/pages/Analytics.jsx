import { useRef, useState, useCallback } from 'react';
import { Search, TrendingUp, Loader2, X, BarChart2, Percent, ArrowUpDown, ExternalLink } from 'lucide-react';
import { getProducts, getPriceDynamics } from '../api/client';
import { useNavigate } from 'react-router-dom';
import { formatMsk } from '../utils/date';
import {
  ComposedChart, Line, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import toast from 'react-hot-toast';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';

const DAYS_OPTIONS = [
  { value: 7,   label: '7 дней' },
  { value: 14,  label: '14 дней' },
  { value: 30,  label: '30 дней' },
  { value: 90,  label: '90 дней' },
  { value: 180, label: '180 дней' },
  { value: 365, label: '365 дней' },
];

const tooltipStyle = {
  border: '1px solid rgba(0,200,255,.2)',
  borderRadius: 10,
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
  background: 'var(--card, #fff)',
};

function StatCard({ label, value, sub, color }) {
  return (
    <div
      className="rounded-xl p-4 border"
      style={{ borderColor: `${color}30`, background: `${color}08` }}
    >
      <p className="text-[10px] uppercase tracking-widest font-semibold mb-1" style={{ color: '#9ca3af' }}>
        {label}
      </p>
      <p className="text-xl font-extrabold" style={{ color }}>{value}</p>
      {sub && <p className="text-[11px] mt-0.5" style={{ color: 'var(--text-3)' }}>{sub}</p>}
    </div>
  );
}

export default function Analytics() {
  const [productSearch, setProductSearch] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const [showDropdown, setShowDropdown] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [days, setDays] = useState(30);
  const [chartData, setChartData] = useState([]);
  const [loading, setLoading] = useState(false);
  const searchTimerRef = useRef(null);

  const handleSearch = (val) => {
    setProductSearch(val);
    setShowDropdown(true);
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (!val.trim()) { setSearchResults([]); return; }
    searchTimerRef.current = setTimeout(() => {
      setSearchLoading(true);
      getProducts({ search: val.trim(), page: 1, page_size: 50 })
        .then((res) => setSearchResults(res.data.products || []))
        .catch(() => setSearchResults([]))
        .finally(() => setSearchLoading(false));
    }, 300);
  };

  const selectProduct = (p) => {
    setSelectedProduct(p);
    setProductSearch(p.name || p.seller_sku || `ID ${p.id}`);
    setShowDropdown(false);
    loadDynamics(p.id, days);
  };

  const clearProduct = () => {
    setSelectedProduct(null);
    setProductSearch('');
    setSearchResults([]);
    setChartData([]);
  };

  const loadDynamics = (productId, d) => {
    setLoading(true);
    getPriceDynamics(productId, d)
      .then((res) => {
        const raw = res.data.points || res.data || [];
        setChartData(
          raw.map((p) => ({
            date: p.snapshot_at ? formatMsk(p.snapshot_at, 'dd.MM HH:mm') : '',
            seller_price: p.seller_price ?? null,
            final_price: p.final_price ?? null,
            spp: p.spp ?? null,
            discount: p.discount ?? null,
          }))
        );
      })
      .catch(() => {
        toast.error('Ошибка загрузки данных');
        setChartData([]);
      })
      .finally(() => setLoading(false));
  };

  const changeDays = (d) => {
    setDays(d);
    if (selectedProduct) loadDynamics(selectedProduct.id, d);
  };

  /* ── Margin analysis state ── */
  const [marginOp, setMarginOp] = useState('lt');
  const [marginVal, setMarginVal] = useState('');
  const [marginSort, setMarginSort] = useState('asc');
  const [marginStockFilter, setMarginStockFilter] = useState('all');
  const [marginProducts, setMarginProducts] = useState([]);
  const [marginTotal, setMarginTotal] = useState(0);
  const [marginLoading, setMarginLoading] = useState(false);
  const [marginPage, setMarginPage] = useState(1);
  const navigate = useNavigate();

  const loadMarginProducts = useCallback((op, val, sort, stockFilter = 'all', p = 1) => {
    if (val === '' || isNaN(Number(val))) return;
    setMarginLoading(true);
    const params = { page: p, page_size: 50, margin_sort: sort };
    if (op === 'gt') params.margin_min = Number(val);
    else params.margin_max = Number(val);
    if (stockFilter === 'in_stock' || stockFilter === 'out_of_stock') {
      params.stock_filter = stockFilter;
    }
    getProducts(params)
      .then((res) => {
        setMarginProducts(res.data.products || []);
        setMarginTotal(res.data.total || 0);
        setMarginPage(p);
      })
      .catch(() => {})
      .finally(() => setMarginLoading(false));
  }, []);

  const applyMarginFilter = () => loadMarginProducts(marginOp, marginVal, marginSort, marginStockFilter, 1);

  const getMarginColor = (m) => {
    if (m == null) return '#9ca3af';
    if (m < 0) return '#ef4444';
    if (m < 15) return '#f59e0b';
    if (m < 30) return '#22c55e';
    return '#00c8ff';
  };

  /* ── Stats ── */
  const prices = chartData.map((p) => p.seller_price).filter(Boolean);
  const spps   = chartData.map((p) => p.spp).filter((v) => v != null);
  const minPrice = prices.length ? Math.min(...prices) : null;
  const maxPrice = prices.length ? Math.max(...prices) : null;
  const avgSpp   = spps.length ? (spps.reduce((a, b) => a + b, 0) / spps.length).toFixed(1) : null;
  const lastSpp  = spps.length ? spps[spps.length - 1] : null;
  const priceChanges = prices.length
    ? prices.filter((v, i) => i > 0 && v !== prices[i - 1]).length
    : 0;

  return (
    <div className="space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold neon-gradient">Аналитика товара</h1>
        <p className="text-sm mt-1.5" style={{ color: 'var(--text-3)' }}>
          Динамика цены и СПП за выбранный период
        </p>
      </div>

      {/* Search + Period */}
      <Card className="border" style={{ borderColor: 'rgba(0,200,255,.15)' }}>
        <CardContent className="pt-5 pb-5">
          <div className="flex flex-col sm:flex-row gap-4">

            {/* Search */}
            <div className="relative flex-1" style={{ maxWidth: 440 }}>
              <Search
                className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none"
                style={{ color: '#9ca3af' }}
              />
              <input
                type="text"
                placeholder="Артикул или название товара..."
                value={productSearch}
                onChange={(e) => handleSearch(e.target.value)}
                onFocus={() => productSearch && setShowDropdown(true)}
                className="w-full pl-9 pr-8 py-2.5 rounded-xl text-sm border focus:outline-none focus:ring-2"
                style={{
                  background: 'hsl(var(--muted))',
                  borderColor: selectedProduct ? '#00c8ff' : 'var(--border, #e5e7eb)',
                  color: 'var(--text)',
                  focusRingColor: '#00c8ff',
                }}
              />
              {(productSearch || selectedProduct) && (
                <button
                  className="absolute right-2.5 top-1/2 -translate-y-1/2"
                  onClick={clearProduct}
                >
                  <X className="w-3.5 h-3.5" style={{ color: '#9ca3af' }} />
                </button>
              )}

              {/* Dropdown */}
              {showDropdown && productSearch && (
                <div
                  className="absolute z-50 w-full mt-1 rounded-xl border overflow-y-auto"
                  style={{
                    maxHeight: 260,
                    background: 'var(--card, #fff)',
                    borderColor: 'var(--border, #e5e7eb)',
                    boxShadow: '0 8px 24px rgba(0,0,0,0.12)',
                  }}
                  onMouseDown={(e) => e.preventDefault()}
                >
                  {searchLoading && (
                    <div className="flex items-center gap-2 px-3 py-2.5 text-sm" style={{ color: 'var(--text-3)' }}>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" /> Поиск...
                    </div>
                  )}
                  {!searchLoading && searchResults.length === 0 && (
                    <div className="px-3 py-2.5 text-sm" style={{ color: 'var(--text-3)' }}>Ничего не найдено</div>
                  )}
                  {searchResults.map((p) => (
                    <div
                      key={p.id}
                      className="px-3 py-2.5 cursor-pointer hover:bg-primary/10 transition-colors"
                      onClick={() => selectProduct(p)}
                    >
                      <span className="text-xs font-semibold" style={{ color: '#00c8ff' }}>{p.seller_sku}</span>
                      {p.name && (
                        <span className="ml-2 text-xs truncate" style={{ color: 'var(--text-2)' }}>{p.name}</span>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Period */}
            <div className="flex items-center gap-2 flex-wrap">
              {DAYS_OPTIONS.map((opt) => (
                <Button
                  key={opt.value}
                  size="sm"
                  variant={days === opt.value ? 'default' : 'outline'}
                  className="text-xs h-9 px-3"
                  style={days === opt.value ? { background: '#00c8ff', color: '#fff', borderColor: '#00c8ff' } : {}}
                  onClick={() => changeDays(opt.value)}
                >
                  {opt.label}
                </Button>
              ))}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Empty state */}
      {!selectedProduct && (
        <div
          className="flex flex-col items-center justify-center rounded-2xl border-2 border-dashed"
          style={{ height: 320, borderColor: 'rgba(0,200,255,.2)' }}
        >
          <TrendingUp className="w-10 h-10 mb-3" style={{ color: 'rgba(0,200,255,.3)' }} />
          <p className="text-base font-semibold" style={{ color: 'var(--text-2)' }}>Выберите товар</p>
          <p className="text-sm mt-1" style={{ color: 'var(--text-3)' }}>
            Найдите товар по артикулу или названию выше
          </p>
        </div>
      )}

      {/* Content */}
      {selectedProduct && (
        <>
          {/* Product info bar */}
          <div
            className="flex items-center gap-3 rounded-xl px-4 py-3 border"
            style={{ borderColor: 'rgba(0,200,255,.2)', background: 'rgba(0,200,255,.04)' }}
          >
            <div className="w-2 h-2 rounded-full" style={{ background: '#00c8ff', flexShrink: 0 }} />
            <span className="text-xs font-semibold" style={{ color: '#00c8ff' }}>
              {selectedProduct.seller_sku}
            </span>
            {selectedProduct.name && (
              <span className="text-sm truncate" style={{ color: 'var(--text-2)' }}>{selectedProduct.name}</span>
            )}
            <span className="ml-auto text-xs" style={{ color: 'var(--text-3)' }}>
              {chartData.length} точек за {days} дн.
            </span>
          </div>

          {/* Stat cards */}
          {chartData.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
              <StatCard
                label="Мин. цена"
                value={minPrice != null ? `${minPrice.toLocaleString('ru')} ₽` : '—'}
                color="#22c55e"
              />
              <StatCard
                label="Макс. цена"
                value={maxPrice != null ? `${maxPrice.toLocaleString('ru')} ₽` : '—'}
                color="#ef4444"
              />
              <StatCard
                label="Средний СПП"
                value={avgSpp != null ? `${avgSpp}%` : '—'}
                color="#8b5cf6"
              />
              <StatCard
                label="Изменений цены"
                value={priceChanges}
                sub={`за ${days} дней`}
                color="#00c8ff"
              />
            </div>
          )}

          {/* Charts */}
          {loading ? (
            <div className="flex items-center justify-center h-64 gap-2">
              <Loader2 className="w-6 h-6 animate-spin" style={{ color: '#00c8ff' }} />
              <span className="text-sm" style={{ color: 'var(--text-3)' }}>Загрузка данных...</span>
            </div>
          ) : chartData.length === 0 ? (
            <div
              className="flex items-center justify-center rounded-2xl"
              style={{ height: 220, background: 'hsl(var(--muted))' }}
            >
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет снимков за этот период</p>
            </div>
          ) : (
            <div className="space-y-5">

              {/* Price chart */}
              <Card className="border" style={{ borderColor: 'rgba(0,200,255,.12)' }}>
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-4 h-4" style={{ color: '#00c8ff' }} />
                    <CardTitle className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                      Динамика цены
                    </CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <ResponsiveContainer width="100%" height={280}>
                    <ComposedChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" />
                      <XAxis
                        dataKey="date"
                        tick={{ fontSize: 10, fill: '#9ca3af' }}
                        tickLine={false}
                        interval="preserveStartEnd"
                      />
                      <YAxis
                        yAxisId="price"
                        tick={{ fontSize: 10, fill: '#9ca3af' }}
                        tickLine={false}
                        axisLine={false}
                        width={56}
                        tickFormatter={(v) => v.toLocaleString('ru')}
                      />
                      <Tooltip
                        contentStyle={tooltipStyle}
                        formatter={(v, name) => {
                          const labels = {
                            seller_price: 'Цена продавца',
                            final_price: 'Цена покупателя',
                          };
                          return [`${v?.toLocaleString('ru')} ₽`, labels[name] || name];
                        }}
                      />
                      <Legend
                        wrapperStyle={{ fontSize: 11 }}
                        formatter={(v) => ({ seller_price: 'Цена продавца', final_price: 'Цена покупателя' }[v] || v)}
                      />
                      <Line
                        yAxisId="price"
                        type="monotone"
                        dataKey="seller_price"
                        stroke="#00c8ff"
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4 }}
                        connectNulls
                      />
                      <Line
                        yAxisId="price"
                        type="monotone"
                        dataKey="final_price"
                        stroke="#0066ff"
                        strokeWidth={2}
                        strokeDasharray="5 3"
                        dot={false}
                        activeDot={{ r: 4 }}
                        connectNulls
                      />
                    </ComposedChart>
                  </ResponsiveContainer>
                </CardContent>
              </Card>

              {/* SPP chart */}
              <Card className="border" style={{ borderColor: 'rgba(139,92,246,.12)' }}>
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <Percent className="w-4 h-4" style={{ color: '#8b5cf6' }} />
                    <CardTitle className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                      СПП (скидка постоянного покупателя)
                    </CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  {spps.length === 0 ? (
                    <div
                      className="flex items-center justify-center rounded-xl"
                      style={{ height: 160, background: 'hsl(var(--muted))' }}
                    >
                      <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных по СПП за этот период</p>
                    </div>
                  ) : (
                    <ResponsiveContainer width="100%" height={200}>
                      <ComposedChart data={chartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" />
                        <XAxis
                          dataKey="date"
                          tick={{ fontSize: 10, fill: '#9ca3af' }}
                          tickLine={false}
                          interval="preserveStartEnd"
                        />
                        <YAxis
                          tick={{ fontSize: 10, fill: '#9ca3af' }}
                          tickLine={false}
                          axisLine={false}
                          width={36}
                          tickFormatter={(v) => `${v}%`}
                          domain={[0, 'auto']}
                        />
                        <Tooltip
                          contentStyle={tooltipStyle}
                          formatter={(v) => [`${v}%`, 'СПП']}
                        />
                        <Bar
                          dataKey="spp"
                          name="СПП"
                          fill="#8b5cf6"
                          opacity={0.7}
                          radius={[3, 3, 0, 0]}
                          maxBarSize={20}
                        />
                        <Line
                          type="monotone"
                          dataKey="spp"
                          stroke="#8b5cf6"
                          strokeWidth={2}
                          dot={false}
                          activeDot={{ r: 4 }}
                          connectNulls
                          legendType="none"
                        />
                      </ComposedChart>
                    </ResponsiveContainer>
                  )}
                </CardContent>
              </Card>

              {/* Data table */}
              <Card className="border" style={{ borderColor: 'rgba(0,200,255,.1)' }}>
                <CardHeader className="pb-2">
                  <div className="flex items-center gap-2">
                    <BarChart2 className="w-4 h-4" style={{ color: '#00c8ff' }} />
                    <CardTitle className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
                      История снимков
                    </CardTitle>
                    <span className="text-xs ml-auto" style={{ color: 'var(--text-3)' }}>
                      Последние {Math.min(chartData.length, 100)} из {chartData.length}
                    </span>
                  </div>
                </CardHeader>
                <CardContent className="p-0">
                  <div className="overflow-x-auto">
                    <table className="w-full text-xs">
                      <thead>
                        <tr style={{ borderBottom: '1px solid var(--border, #e5e7eb)' }}>
                          {['Дата', 'Цена продавца', 'Цена покупателя', 'Скидка', 'СПП'].map((h) => (
                            <th
                              key={h}
                              className="px-4 py-2.5 text-left font-semibold uppercase tracking-wider"
                              style={{ color: '#9ca3af' }}
                            >
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {[...chartData].reverse().slice(0, 100).map((row, i) => {
                          const prevRow = [...chartData].reverse()[i + 1];
                          const priceChanged = prevRow && prevRow.seller_price !== row.seller_price;
                          return (
                            <tr
                              key={i}
                              style={{
                                borderBottom: '1px solid var(--border, #f0f0f4)',
                                background: priceChanged ? 'rgba(0,200,255,.04)' : undefined,
                              }}
                            >
                              <td className="px-4 py-2" style={{ color: 'var(--text-2)' }}>{row.date}</td>
                              <td className="px-4 py-2 font-semibold" style={{ color: priceChanged ? '#00c8ff' : 'var(--text)' }}>
                                {row.seller_price != null ? `${row.seller_price.toLocaleString('ru')} ₽` : '—'}
                                {priceChanged && <span className="ml-1 text-[10px]" style={{ color: '#00c8ff' }}>↑</span>}
                              </td>
                              <td className="px-4 py-2" style={{ color: 'var(--text)' }}>
                                {row.final_price != null ? `${row.final_price.toLocaleString('ru')} ₽` : '—'}
                              </td>
                              <td className="px-4 py-2" style={{ color: 'var(--text-2)' }}>
                                {row.discount != null ? `${row.discount}%` : '—'}
                              </td>
                              <td className="px-4 py-2">
                                {row.spp != null ? (
                                  <span
                                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-semibold"
                                    style={{ background: 'rgba(139,92,246,.12)', color: '#8b5cf6' }}
                                  >
                                    {row.spp}%
                                  </span>
                                ) : '—'}
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                </CardContent>
              </Card>

            </div>
          )}
        </>
      )}

      {/* ═══ Margin analysis ═══ */}
      <Card className="border" style={{ borderColor: 'rgba(245,158,11,.2)' }}>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'rgba(245,158,11,.1)' }}>
              <ArrowUpDown className="w-4 h-4" style={{ color: '#f59e0b' }} />
            </div>
            <CardTitle className="text-base font-semibold" style={{ color: 'var(--text)' }}>
              Анализ маржи
            </CardTitle>
          </div>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-3 mb-5 flex-wrap">
            <span className="text-sm" style={{ color: 'var(--text-2)' }}>Показать товары где маржа</span>
            <select
              value={marginOp}
              onChange={(e) => setMarginOp(e.target.value)}
              className="input w-24 text-sm"
            >
              <option value="lt">меньше</option>
              <option value="gt">больше</option>
            </select>
            <div className="flex items-center gap-1">
              <input
                type="number"
                value={marginVal}
                onChange={(e) => setMarginVal(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && applyMarginFilter()}
                placeholder="0"
                className="input w-24 text-sm"
              />
              <span className="text-sm font-semibold" style={{ color: 'var(--text-2)' }}>%</span>
            </div>
            <span className="text-sm" style={{ color: 'var(--text-2)' }}>и остаток</span>
            <select
              value={marginStockFilter}
              onChange={(e) => setMarginStockFilter(e.target.value)}
              className="input w-44 text-sm"
            >
              <option value="all">любой</option>
              <option value="in_stock">в наличии</option>
              <option value="out_of_stock">нет в наличии</option>
            </select>
            <div className="flex items-center gap-1 rounded-lg border overflow-hidden" style={{ borderColor: 'var(--border)' }}>
              <button
                onClick={() => { setMarginSort('asc'); if (marginProducts.length) loadMarginProducts(marginOp, marginVal, 'asc', marginStockFilter, 1); }}
                className="px-3 py-2 text-xs font-semibold transition-colors"
                style={{
                  background: marginSort === 'asc' ? 'rgba(245,158,11,.15)' : 'transparent',
                  color: marginSort === 'asc' ? '#f59e0b' : 'var(--text-3)',
                }}
              >↑ По возр.</button>
              <button
                onClick={() => { setMarginSort('desc'); if (marginProducts.length) loadMarginProducts(marginOp, marginVal, 'desc', marginStockFilter, 1); }}
                className="px-3 py-2 text-xs font-semibold transition-colors"
                style={{
                  background: marginSort === 'desc' ? 'rgba(245,158,11,.15)' : 'transparent',
                  color: marginSort === 'desc' ? '#f59e0b' : 'var(--text-3)',
                }}
              >↓ По убыв.</button>
            </div>
            <button
              onClick={applyMarginFilter}
              disabled={marginVal === '' || marginLoading}
              className="px-4 py-2 rounded-lg text-sm font-semibold transition-all"
              style={{
                background: marginVal !== '' ? '#f59e0b' : 'hsl(var(--muted))',
                color: marginVal !== '' ? '#fff' : 'var(--text-3)',
                cursor: marginVal !== '' ? 'pointer' : 'default',
              }}
            >
              {marginLoading ? 'Поиск...' : 'Найти'}
            </button>
            {marginTotal > 0 && (
              <span className="text-sm" style={{ color: 'var(--text-3)' }}>
                Найдено: <strong style={{ color: 'var(--text)' }}>{marginTotal}</strong>
              </span>
            )}
          </div>

          {marginLoading ? (
            <div className="flex items-center justify-center h-32 gap-2">
              <Loader2 className="w-5 h-5 animate-spin" style={{ color: '#f59e0b' }} />
              <span className="text-sm" style={{ color: 'var(--text-3)' }}>Загрузка...</span>
            </div>
          ) : marginProducts.length > 0 ? (
            <>
              <div className="overflow-x-auto rounded-xl border" style={{ borderColor: 'var(--border)' }}>
                <table className="w-full text-sm">
                  <thead>
                    <tr style={{ background: 'hsl(var(--muted))' }}>
                      {['Артикул', 'Название', 'Стратегия', 'Цена продавца', '', ''].map((h, i) => (
                        <th key={i} className="px-4 py-2.5 text-left text-xs font-semibold uppercase tracking-wider"
                          style={{ color: '#9ca3af' }}>
                          {i === 4 ? (
                            <button
                              className="flex items-center gap-1 hover:text-yellow-500 transition-colors"
                              onClick={() => {
                                const next = marginSort === 'asc' ? 'desc' : 'asc';
                                setMarginSort(next);
                                loadMarginProducts(marginOp, marginVal, next, marginStockFilter, 1);
                              }}
                            >
                              Маржа {marginSort === 'asc' ? '↑' : '↓'}
                            </button>
                          ) : h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {marginProducts.map((p, i) => (
                      <tr key={p.id}
                        style={{ borderTop: i > 0 ? '1px solid var(--border)' : undefined }}
                        className="hover:bg-primary/5 transition-colors"
                      >
                        <td className="px-4 py-2.5">
                          <span className="font-mono text-xs font-semibold" style={{ color: '#00c8ff' }}>
                            {p.seller_sku}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 max-w-xs">
                          <span className="truncate block text-xs" style={{ color: 'var(--text-2)' }}>
                            {p.name || '—'}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          {p.config?.strategy ? (
                            <span className="text-xs px-2 py-0.5 rounded-full font-semibold"
                              style={{
                                background: p.config.strategy === 'rrp' ? 'rgba(0,200,255,.1)' : 'rgba(0,102,255,.1)',
                                color: p.config.strategy === 'rrp' ? '#00c8ff' : '#0066ff',
                              }}>
                              {p.config.strategy === 'rrp' ? 'РРЦ' : 'Конкурент'}
                            </span>
                          ) : <span style={{ color: 'var(--text-3)' }}>—</span>}
                        </td>
                        <td className="px-4 py-2.5 font-semibold text-xs" style={{ color: 'var(--text)' }}>
                          {p.current_seller_price ? `${Number(p.current_seller_price).toLocaleString('ru')} ₽` : '—'}
                        </td>
                        <td className="px-4 py-2.5">
                          <span className="font-bold text-sm" style={{ color: getMarginColor(p.margin) }}>
                            {p.margin != null
                              ? `${Number(p.margin).toLocaleString('ru-RU', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}%`
                              : '—'}
                          </span>
                        </td>
                        <td className="px-4 py-2.5">
                          <button
                            onClick={() => navigate(`/products/${p.id}`)}
                            className="text-xs flex items-center gap-1 hover:underline"
                            style={{ color: '#00c8ff' }}
                          >
                            Открыть <ExternalLink className="w-3 h-3" />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {marginTotal > 50 && (
                <div className="flex items-center justify-center gap-2 mt-4">
                  <button
                    disabled={marginPage === 1}
                    onClick={() => loadMarginProducts(marginOp, marginVal, marginSort, marginStockFilter, marginPage - 1)}
                    className="px-3 py-1.5 rounded-lg text-xs border disabled:opacity-40"
                    style={{ borderColor: 'var(--border)' }}
                  >← Назад</button>
                  <span className="text-xs" style={{ color: 'var(--text-3)' }}>
                    Стр. {marginPage} из {Math.ceil(marginTotal / 50)}
                  </span>
                  <button
                    disabled={marginPage >= Math.ceil(marginTotal / 50)}
                    onClick={() => loadMarginProducts(marginOp, marginVal, marginSort, marginStockFilter, marginPage + 1)}
                    className="px-3 py-1.5 rounded-lg text-xs border disabled:opacity-40"
                    style={{ borderColor: 'var(--border)' }}
                  >Вперёд →</button>
                </div>
              )}
            </>
          ) : marginVal !== '' && !marginLoading ? (
            <div className="flex items-center justify-center h-20 rounded-xl" style={{ background: 'hsl(var(--muted))' }}>
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>Товаров не найдено</p>
            </div>
          ) : (
            <div className="flex items-center justify-center h-20 rounded-xl border-2 border-dashed"
              style={{ borderColor: 'rgba(245,158,11,.2)' }}>
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>
                Введите значение маржи и нажмите «Найти»
              </p>
            </div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}

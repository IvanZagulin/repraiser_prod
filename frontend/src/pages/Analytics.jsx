import { useEffect, useRef, useState } from 'react';
import { BarChart2, TrendingUp, CheckCircle2, RefreshCw, Activity, Loader2 } from 'lucide-react';
import { getRepricerRuns, getProducts, getPriceDynamics, getCompetitorTrends, getProductStats } from '../api/client';
import { formatMsk } from '../utils/date';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import toast from 'react-hot-toast';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';

const PIE_COLORS = { rrp: '#00c8ff', competitor: '#0066ff', none: '#d1d5db' };
const PIE_LABELS = { rrp: 'РРЦ', competitor: 'Конкурент', none: 'Без настройки' };

const DAYS_OPTIONS = [7, 14, 30, 90];

const CustomPieLabel = ({ cx, cy, midAngle, innerRadius, outerRadius, percent }) => {
  if (percent < 0.05) return null;
  const RADIAN = Math.PI / 180;
  const r = innerRadius + (outerRadius - innerRadius) * 0.55;
  const x = cx + r * Math.cos(-midAngle * RADIAN);
  const y = cy + r * Math.sin(-midAngle * RADIAN);
  return (
    <text x={x} y={y} fill="#fff" textAnchor="middle" dominantBaseline="central"
      style={{ fontSize: 11, fontWeight: 700 }}>
      {`${(percent * 100).toFixed(0)}%`}
    </text>
  );
};

const tooltipStyle = {
  border: '1px solid #e8eaf0',
  borderRadius: 10,
  fontSize: 12,
  boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
};

export default function Analytics() {
  const [loading, setLoading] = useState(true);
  const [runs, setRuns] = useState([]);
  const [competitorTrends, setCompetitorTrends] = useState([]);

  // Price dynamics state
  const [selectedProductId, setSelectedProductId] = useState('');
  const [productSearch, setProductSearch] = useState('');
  const [showProductDropdown, setShowProductDropdown] = useState(false);
  const [priceDays, setPriceDays] = useState(30);
  const [priceDynamics, setPriceDynamics] = useState([]);
  const [priceLoading, setPriceLoading] = useState(false);
  const [searchResults, setSearchResults] = useState([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchTimerRef = useRef(null);

  const [runStats, setRunStats] = useState({ total: 0, total_updated: 0, total_completed: 0 });
  const [productStats, setProductStats] = useState(null);

  const fetchMainData = () =>
    Promise.all([
      getRepricerRuns({ limit: 100 }).catch(() => ({ data: { runs: [], total: 0, total_updated: 0, total_completed: 0 } })),
      getCompetitorTrends(30).catch(() => ({ data: { trends: [] } })),
      getProductStats().catch(() => ({ data: null })),
    ]).then(([runsRes, trendsRes, statsRes]) => {
      setRuns(runsRes.data.runs || []);
      setRunStats({
        total: runsRes.data.total ?? 0,
        total_updated: runsRes.data.total_updated ?? 0,
        total_completed: runsRes.data.total_completed ?? 0,
      });
      setCompetitorTrends(trendsRes.data.trends || trendsRes.data || []);
      setProductStats(statsRes.data);
    });

  // Load on mount + auto-refresh every 60s
  useEffect(() => {
    fetchMainData()
      .catch(() => toast.error('Ошибка загрузки аналитики'))
      .finally(() => setLoading(false));

    const interval = setInterval(() => {
      fetchMainData().catch(() => {});
    }, 60000);
    return () => clearInterval(interval);
  }, []);

  // Fetch price dynamics when product or days change
  useEffect(() => {
    if (!selectedProductId) {
      setPriceDynamics([]);
      return;
    }
    setPriceLoading(true);
    getPriceDynamics(selectedProductId, priceDays)
      .then((res) => {
        setPriceDynamics(res.data.points || res.data || []);
      })
      .catch(() => {
        toast.error('Ошибка загрузки динамики цен');
        setPriceDynamics([]);
      })
      .finally(() => setPriceLoading(false));
  }, [selectedProductId, priceDays]);

  const handleProductSearchChange = (val) => {
    setProductSearch(val);
    setShowProductDropdown(true);
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

  /* ── Derived data ── */

  // Runs activity chart (last 30, chronological)
  const runsChart = [...runs]
    .slice(0, 30)
    .reverse()
    .map((r) => ({
      label: formatMsk(r.started_at, 'dd.MM HH:mm'),
      updated: r.updated_count,
      total: r.total_products,
      status: r.status,
    }));

  // Updates per day (bar chart)
  const byDay = {};
  runs.forEach((r) => {
    const day = formatMsk(r.started_at, 'dd.MM');
    byDay[day] = (byDay[day] || 0) + r.updated_count;
  });
  const byDayChart = Object.entries(byDay)
    .slice(0, 14)
    .reverse()
    .map(([day, count]) => ({ day, count }));

  // Strategy distribution (pie) — use server-side stats for accuracy with large catalogs
  const stratMap = productStats
    ? { rrp: productStats.rrp ?? 0, competitor: productStats.competitor ?? 0, none: productStats.no_config ?? 0 }
    : { rrp: 0, competitor: 0, none: 0 };
  const pieData = Object.entries(stratMap)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({ name: PIE_LABELS[key], value, key }));

  // Summary stats (server-side aggregates over all runs)
  const totalRuns = runStats.total;
  const completedRuns = runStats.total_completed;
  const totalUpdated = runStats.total_updated;
  const successRate = totalRuns ? Math.round((completedRuns / totalRuns) * 100) : 0;

  // Price dynamics chart data
  const priceChartData = priceDynamics.map((p) => ({
    date: p.snapshot_at ? formatMsk(p.snapshot_at, 'dd.MM') : p.label || '',
    seller_price: p.seller_price,
    final_price: p.final_price,
    competitor_price: p.competitor_price,
  }));

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <div className="spinner" />
        <p className="text-sm animate-pulse" style={{ color: 'var(--text-3)' }}>Загрузка аналитики...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">

      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold neon-gradient">
          Аналитика
        </h1>
        <p className="text-sm mt-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-3)' }}>
          <Activity className="w-3.5 h-3.5" />
          Статистика и графики по работе репрайсера
        </p>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 lg:grid-cols-3 gap-4">
        {[
          {
            label: 'Всего запусков',
            value: totalRuns,
            icon: RefreshCw,
            color: '#00c8ff',
            bg: 'linear-gradient(135deg, rgba(0,200,255,.08), rgba(0,102,255,.08))',
            border: 'rgba(0,200,255,.2)',
          },
          {
            label: 'Успешных',
            value: `${completedRuns} (${successRate}%)`,
            icon: CheckCircle2,
            color: '#22c55e',
            bg: 'linear-gradient(135deg, rgba(34,197,94,.08), rgba(34,197,94,.04))',
            border: 'rgba(34,197,94,.2)',
          },
          {
            label: 'Обновлений всего',
            value: totalUpdated.toLocaleString('ru'),
            icon: TrendingUp,
            color: '#0066ff',
            bg: 'linear-gradient(135deg, rgba(0,102,255,.08), rgba(108,92,231,.06))',
            border: 'rgba(0,102,255,.2)',
          },
        ].map((s, i) => (
          <div
            key={i}
            className="stat-card"
            style={{ borderColor: s.border }}
          >
            <div className="flex items-center justify-between mb-3">
              <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: '#9ca3af' }}>
                {s.label}
              </p>
              <div className="icon-box" style={{ background: s.bg }}>
                <s.icon className="w-4 h-4" style={{ color: s.color }} />
              </div>
            </div>
            <p className="text-2xl font-extrabold" style={{ color: s.color }}>
              {s.value}
            </p>
          </div>
        ))}
      </div>

      {/* ═══ Price Dynamics — HERO section ═══ */}
      <Card className="border" style={{ borderColor: 'rgba(0,200,255,.15)' }}>
        <CardHeader className="pb-3">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'rgba(0,200,255,.1)' }}>
                <TrendingUp className="w-4 h-4" style={{ color: '#00c8ff' }} />
              </div>
              <CardTitle className="text-base font-semibold" style={{ color: 'var(--text)' }}>
                Динамика цен
              </CardTitle>
            </div>
            <div className="flex items-center gap-2 flex-wrap">
              {!selectedProductId && (
                <span className="text-[10px]" style={{ color: 'var(--text-3)' }}>
                  Выберите товар↓
                </span>
              )}
              {DAYS_OPTIONS.map((d) => (
                <Button
                  key={d}
                  size="sm"
                  variant={priceDays === d ? 'default' : 'outline'}
                  className="text-xs h-7 px-3"
                  style={priceDays === d ? { background: '#00c8ff', color: '#fff', borderColor: '#00c8ff' } : {}}
                  onClick={() => {
                    setPriceDays(d);
                    if (!selectedProductId) toast('Сначала выберите товар', { icon: '👆' });
                  }}
                >
                  {d} дн.
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* Product selector with search */}
          <div className="mb-4 relative" style={{ maxWidth: 400 }}>
            <input
              type="text"
              placeholder="Поиск по артикулу или названию..."
              value={productSearch}
              onChange={(e) => handleProductSearchChange(e.target.value)}
              onFocus={() => setShowProductDropdown(true)}
              className="w-full px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
              style={{
                background: 'hsl(var(--muted))',
                borderColor: selectedProductId ? '#00c8ff' : 'var(--border, #e5e7eb)',
                color: 'var(--text, #1a1a2e)',
              }}
            />
            {selectedProductId && (
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 text-xs px-2 py-0.5 rounded"
                style={{ background: 'rgba(0,200,255,0.1)', color: '#00c8ff' }}
                onClick={() => { setSelectedProductId(''); setProductSearch(''); }}
              >
                ✕
              </button>
            )}
            {showProductDropdown && productSearch && (
              <div
                className="absolute z-50 w-full mt-1 rounded-lg border overflow-y-auto"
                style={{
                  maxHeight: 240,
                  background: 'var(--card, #fff)',
                  borderColor: 'var(--border, #e5e7eb)',
                  boxShadow: '0 4px 16px rgba(0,0,0,0.1)',
                }}
                onMouseDown={(e) => e.preventDefault()}
              >
                {searchLoading ? (
                  <div className="px-3 py-2 text-sm" style={{ color: 'var(--text-3)' }}>Поиск...</div>
                ) : null}
                {searchResults
                  .map((p) => (
                    <div
                      key={p.id}
                      className="px-3 py-2 text-sm cursor-pointer hover:bg-primary/10"
                      style={{ color: 'var(--text, #1a1a2e)' }}
                      onClick={() => {
                        setSelectedProductId(String(p.id));
                        setProductSearch(p.name || p.seller_sku || `ID ${p.id}`);
                        setShowProductDropdown(false);
                      }}
                    >
                      <span className="font-medium" style={{ color: '#00c8ff' }}>{p.seller_sku}</span>
                      {p.name && <span className="ml-2 text-xs" style={{ color: 'var(--text-3)' }}>{p.name}</span>}
                    </div>
                  ))}
                {!searchLoading && searchResults.length === 0 && productSearch.trim() && (
                  <div className="px-3 py-2 text-sm" style={{ color: 'var(--text-3)' }}>Ничего не найдено</div>
                )}
              </div>
            )}
          </div>

          {/* Chart */}
          {!selectedProductId ? (
            <div className="flex items-center justify-center h-56 rounded-lg" style={{ background: 'hsl(var(--muted))' }}>
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>
                Выберите товар для отображения динамики цен
              </p>
            </div>
          ) : priceLoading ? (
            <div className="flex items-center justify-center h-56 gap-2">
              <Loader2 className="w-5 h-5 animate-spin" style={{ color: '#00c8ff' }} />
              <span className="text-sm" style={{ color: 'var(--text-3)' }}>Загрузка...</span>
            </div>
          ) : priceChartData.length === 0 ? (
            <div className="flex items-center justify-center h-56 rounded-lg" style={{ background: 'hsl(var(--muted))' }}>
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных за выбранный период</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={priceChartData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
                  width={52}
                  tickFormatter={(v) => v.toLocaleString('ru')}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v, name) => {
                    const labels = {
                      seller_price: 'Цена продавца',
                      final_price: 'Итоговая цена',
                      competitor_price: 'Цена конкурента',
                    };
                    return [v?.toLocaleString('ru') + ' \u20BD', labels[name] || name];
                  }}
                />
                <Legend
                  formatter={(v) => {
                    const labels = {
                      seller_price: 'Цена продавца',
                      final_price: 'Итоговая цена',
                      competitor_price: 'Цена конкурента',
                    };
                    return labels[v] || v;
                  }}
                  wrapperStyle={{ fontSize: 11 }}
                />
                <Line
                  type="monotone"
                  dataKey="seller_price"
                  stroke="#00c8ff"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
                <Line
                  type="monotone"
                  dataKey="final_price"
                  stroke="#0066ff"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4 }}
                />
                <Line
                  type="monotone"
                  dataKey="competitor_price"
                  stroke="#f59e0b"
                  strokeWidth={2}
                  strokeDasharray="6 3"
                  dot={false}
                  activeDot={{ r: 4 }}
                />
              </LineChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      {/* ═══ Runs activity area chart ═══ */}
      <div className="card">
        <div className="flex items-center gap-2.5 mb-5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(0,200,255,.1)' }}>
            <BarChart2 className="w-3.5 h-3.5" style={{ color: '#00c8ff' }} />
          </div>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            Активность запусков
          </h2>
          <span className="text-xs ml-auto" style={{ color: 'var(--text-3)' }}>
            До 30 последних запусков
          </span>
        </div>
        {runsChart.length === 0 ? (
          <div className="flex items-center justify-center h-48">
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных</p>
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={220}>
            <AreaChart data={runsChart} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="analyticsTotal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#00c8ff" stopOpacity={0.15} />
                  <stop offset="95%" stopColor="#00c8ff" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="analyticsUpdated" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#16a34a" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#16a34a" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" />
              <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} interval="preserveStartEnd" />
              <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} axisLine={false} width={32} allowDecimals={false} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v, name) => [v, name === 'total' ? 'Всего товаров' : 'Обновлено']}
              />
              <Legend formatter={(v) => (v === 'total' ? 'Всего товаров' : 'Обновлено')} wrapperStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="total" stroke="#00c8ff" strokeWidth={1.5} fill="url(#analyticsTotal)" dot={false} activeDot={{ r: 3 }} />
              <Area type="monotone" dataKey="updated" stroke="#16a34a" strokeWidth={2} fill="url(#analyticsUpdated)" dot={false} activeDot={{ r: 3 }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* ═══ Bottom row: bar chart + pie ═══ */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Updates per day bar chart */}
        <div className="card">
          <div className="flex items-center gap-2.5 mb-5">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(0,102,255,.1)' }}>
              <TrendingUp className="w-3.5 h-3.5" style={{ color: '#0066ff' }} />
            </div>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Обновления по дням
            </h2>
          </div>
          {byDayChart.length === 0 ? (
            <div className="flex items-center justify-center h-48">
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет данных</p>
            </div>
          ) : (
            <ResponsiveContainer width="100%" height={200}>
              <BarChart data={byDayChart} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" vertical={false} />
                <XAxis dataKey="day" tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} />
                <YAxis tick={{ fontSize: 10, fill: '#9ca3af' }} tickLine={false} axisLine={false} width={28} allowDecimals={false} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v) => [v, 'Обновлений']}
                />
                <Bar dataKey="count" name="Обновлений" fill="#00c8ff" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>

        {/* Strategy pie */}
        <div className="card">
          <div className="flex items-center gap-2.5 mb-5">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(34,197,94,.1)' }}>
              <Activity className="w-3.5 h-3.5" style={{ color: '#16a34a' }} />
            </div>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Товары по стратегиям
            </h2>
          </div>
          {pieData.length === 0 ? (
            <div className="flex items-center justify-center h-48">
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет товаров</p>
            </div>
          ) : (
            <div className="flex items-center gap-4">
              <ResponsiveContainer width="55%" height={180}>
                <PieChart>
                  <Pie
                    data={pieData}
                    cx="50%"
                    cy="50%"
                    innerRadius={45}
                    outerRadius={80}
                    dataKey="value"
                    labelLine={false}
                    label={<CustomPieLabel />}
                  >
                    {pieData.map((entry) => (
                      <Cell key={entry.key} fill={PIE_COLORS[entry.key] || '#9ca3af'} />
                    ))}
                  </Pie>
                  <Tooltip
                    contentStyle={tooltipStyle}
                    formatter={(v, name) => [v, name]}
                  />
                </PieChart>
              </ResponsiveContainer>
              <div className="flex flex-col gap-3 flex-1">
                {pieData.map((d) => (
                  <div key={d.key} className="flex items-center gap-2.5">
                    <span
                      style={{
                        width: 10, height: 10, borderRadius: 3,
                        background: PIE_COLORS[d.key] || '#9ca3af',
                        flexShrink: 0,
                      }}
                    />
                    <span className="text-xs" style={{ color: 'var(--text-2)' }}>{d.name}</span>
                    <span className="text-xs font-bold ml-auto" style={{ color: 'var(--text)' }}>
                      {d.value}
                    </span>
                  </div>
                ))}
                <div className="mt-1 pt-2" style={{ borderTop: '1px solid #f0f0f4' }}>
                  <div className="flex items-center justify-between">
                    <span className="text-xs" style={{ color: 'var(--text-3)' }}>Всего</span>
                    <span className="text-sm font-bold" style={{ color: 'var(--text)' }}>
                      {productStats?.total ?? 0}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ═══ Competitor Trends ═══ */}
      <Card className="border" style={{ borderColor: 'rgba(245,158,11,.15)' }}>
        <CardHeader className="pb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: 'rgba(245,158,11,.1)' }}>
              <BarChart2 className="w-4 h-4" style={{ color: '#f59e0b' }} />
            </div>
            <CardTitle className="text-base font-semibold" style={{ color: 'var(--text)' }}>
              Тренды конкурентов
            </CardTitle>
            <Badge variant="outline" className="ml-2 text-[10px]" style={{ borderColor: 'rgba(245,158,11,.3)', color: '#f59e0b' }}>
              30 дней
            </Badge>
            {Array.isArray(competitorTrends) && competitorTrends.length > 5 && (
              <span className="text-[10px] ml-auto" style={{ color: 'var(--text-3)' }}>
                Топ-5 из {competitorTrends.length}
              </span>
            )}
          </div>
        </CardHeader>
        <CardContent>
          {Array.isArray(competitorTrends) && competitorTrends.length > 0 && competitorTrends[0]?.data ? (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart margin={{ top: 8, right: 16, left: 0, bottom: 40 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 10, fill: '#9ca3af' }}
                  tickLine={false}
                  interval="preserveStartEnd"
                  allowDuplicatedCategory={false}
                />
                <YAxis
                  tick={{ fontSize: 10, fill: '#9ca3af' }}
                  tickLine={false}
                  axisLine={false}
                  width={52}
                  tickFormatter={(v) => v.toLocaleString('ru')}
                />
                <Tooltip
                  contentStyle={tooltipStyle}
                  formatter={(v, name) => [v?.toLocaleString('ru') + ' \u20BD', name]}
                />
                <Legend
                  wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
                  formatter={(name) => name.length > 25 ? name.slice(0, 25) + '…' : name}
                />
                {competitorTrends.slice(0, 5).map((ct, idx) => {
                  const colors = ['#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#10b981'];
                  const shortName = (ct.product_name || `Конкурент ${idx + 1}`).slice(0, 30);
                  return (
                    <Line
                      key={ct.product_id || idx}
                      data={ct.data.map((p) => ({
                        date: p.snapshot_at ? formatMsk(p.snapshot_at, 'dd.MM') : '',
                        [shortName]: p.competitor_price,
                      }))}
                      type="monotone"
                      dataKey={shortName}
                      name={shortName}
                      stroke={colors[idx % colors.length]}
                      strokeWidth={2}
                      dot={false}
                      activeDot={{ r: 3 }}
                    />
                  );
                })}
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <div className="flex items-center justify-center h-40 rounded-lg" style={{ background: 'hsl(var(--muted))' }}>
              <p className="text-sm" style={{ color: 'var(--text-3)' }}>
                Нет данных о трендах конкурентов
              </p>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { BarChart2, TrendingUp, CheckCircle2, XCircle, RefreshCw, Activity, DollarSign, Loader2 } from 'lucide-react';
import { getRepricerRuns, getProducts, getPriceDynamics, getSavings, getCompetitorTrends, getAnalyticsSummary } from '../api/client';
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
  const [products, setProducts] = useState([]);
  const [savings, setSavings] = useState(null);
  const [competitorTrends, setCompetitorTrends] = useState([]);

  // Price dynamics state
  const [selectedProductId, setSelectedProductId] = useState('');
  const [priceDays, setPriceDays] = useState(30);
  const [priceDynamics, setPriceDynamics] = useState([]);
  const [priceLoading, setPriceLoading] = useState(false);

  // Load all data in parallel on mount
  useEffect(() => {
    Promise.all([
      getRepricerRuns({ limit: 100 }).catch(() => ({ data: { runs: [] } })),
      getProducts({ page: 1, page_size: 1000 }).catch(() => ({ data: { products: [] } })),
      getSavings().catch(() => ({ data: { total_savings_rub: 0 } })),
      getCompetitorTrends(30).catch(() => ({ data: { trends: [] } })),
    ])
      .then(([runsRes, prodRes, savingsRes, trendsRes]) => {
        setRuns(runsRes.data.runs || []);
        setProducts(prodRes.data.products || []);
        setSavings(savingsRes.data);
        setCompetitorTrends(trendsRes.data.trends || trendsRes.data || []);
      })
      .catch(() => toast.error('Ошибка загрузки аналитики'))
      .finally(() => setLoading(false));
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
    .slice(-14)
    .map(([day, count]) => ({ day, count }));

  // Strategy distribution (pie)
  const stratMap = { rrp: 0, competitor: 0, none: 0 };
  products.forEach((p) => {
    const s = p.config?.strategy;
    if (s === 'rrp') stratMap.rrp++;
    else if (s === 'competitor') stratMap.competitor++;
    else stratMap.none++;
  });
  const pieData = Object.entries(stratMap)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({ name: PIE_LABELS[key], value, key }));

  // Summary stats
  const totalRuns = runs.length;
  const completedRuns = runs.filter((r) => r.status === 'completed').length;
  const failedRuns = runs.filter((r) => r.status === 'failed').length;
  const totalUpdated = runs.reduce((a, r) => a + r.updated_count, 0);
  const avgUpdated = totalRuns ? Math.round(totalUpdated / totalRuns) : 0;
  const successRate = totalRuns ? Math.round((completedRuns / totalRuns) * 100) : 0;
  const totalSavings = savings?.total_savings_rub ?? savings?.total_savings ?? 0;

  // Price dynamics chart data
  const priceChartData = priceDynamics.map((p) => ({
    date: p.date ? formatMsk(p.date, 'dd.MM') : p.label || '',
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
      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
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
          {
            label: 'Ошибок / ср. обновлений',
            value: `${failedRuns} / ${avgUpdated}`,
            icon: XCircle,
            color: failedRuns > 0 ? '#f59e0b' : '#9ca3bc',
            bg: 'linear-gradient(135deg, rgba(245,158,11,.08), rgba(245,158,11,.04))',
            border: 'rgba(245,158,11,.2)',
          },
          {
            label: 'Экономия',
            value: `${totalSavings.toLocaleString('ru')} \u20BD`,
            icon: DollarSign,
            color: '#16a34a',
            bg: 'linear-gradient(135deg, rgba(22,163,74,.10), rgba(34,197,94,.06))',
            border: 'rgba(22,163,74,.25)',
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
              {DAYS_OPTIONS.map((d) => (
                <Button
                  key={d}
                  size="sm"
                  variant={priceDays === d ? 'default' : 'outline'}
                  className="text-xs h-7 px-3"
                  style={priceDays === d ? { background: '#00c8ff', color: '#fff', borderColor: '#00c8ff' } : {}}
                  onClick={() => setPriceDays(d)}
                >
                  {d} дн.
                </Button>
              ))}
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {/* Product selector */}
          <div className="mb-4">
            <select
              className="w-full sm:w-80 px-3 py-2 rounded-lg text-sm border focus:outline-none focus:ring-2"
              style={{
                background: 'var(--bg-2, #f8f9fb)',
                borderColor: 'var(--border, #e5e7eb)',
                color: 'var(--text, #1a1a2e)',
                '--tw-ring-color': '#00c8ff',
              }}
              value={selectedProductId}
              onChange={(e) => setSelectedProductId(e.target.value)}
            >
              <option value="">Выберите товар...</option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name || p.seller_sku || `ID ${p.id}`}
                </option>
              ))}
            </select>
          </div>

          {/* Chart */}
          {!selectedProductId ? (
            <div className="flex items-center justify-center h-56 rounded-lg" style={{ background: 'var(--bg-2, #f8f9fb)' }}>
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
            <div className="flex items-center justify-center h-56 rounded-lg" style={{ background: 'var(--bg-2, #f8f9fb)' }}>
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
                      {products.length}
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
          </div>
        </CardHeader>
        <CardContent>
          {Array.isArray(competitorTrends) && competitorTrends.length > 0 ? (
            <div className="space-y-5">
              {/* If trends have chart-ready data, render a multi-line chart */}
              {competitorTrends[0]?.points ? (
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
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
                    <Tooltip contentStyle={tooltipStyle} />
                    <Legend wrapperStyle={{ fontSize: 11 }} />
                    {competitorTrends.map((ct, idx) => {
                      const colors = ['#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#10b981'];
                      return (
                        <Line
                          key={ct.product_name || idx}
                          data={ct.points.map((p) => ({
                            date: p.date ? formatMsk(p.date, 'dd.MM') : p.label || '',
                            [ct.product_name || `competitor_${idx}`]: p.price,
                          }))}
                          type="monotone"
                          dataKey={ct.product_name || `competitor_${idx}`}
                          name={ct.product_name || `Конкурент ${idx + 1}`}
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
                /* Fallback: render mini-cards for each competitor entry */
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                  {competitorTrends.map((ct, idx) => (
                    <div
                      key={idx}
                      className="rounded-xl p-4 border"
                      style={{
                        background: 'var(--bg-2, #f8f9fb)',
                        borderColor: 'var(--border, #e5e7eb)',
                      }}
                    >
                      <p className="text-xs font-semibold truncate mb-2" style={{ color: 'var(--text)' }}>
                        {ct.product_name || ct.name || `Конкурент ${idx + 1}`}
                      </p>
                      <div className="flex items-end justify-between">
                        <div>
                          <p className="text-[10px] uppercase tracking-wider mb-0.5" style={{ color: '#9ca3af' }}>
                            Текущая цена
                          </p>
                          <p className="text-lg font-bold" style={{ color: '#f59e0b' }}>
                            {(ct.current_price ?? ct.price ?? 0).toLocaleString('ru')} {'\u20BD'}
                          </p>
                        </div>
                        {ct.change_percent != null && (
                          <Badge
                            variant="outline"
                            className="text-[10px] font-semibold"
                            style={{
                              borderColor: ct.change_percent >= 0 ? 'rgba(239,68,68,.3)' : 'rgba(34,197,94,.3)',
                              color: ct.change_percent >= 0 ? '#ef4444' : '#16a34a',
                            }}
                          >
                            {ct.change_percent >= 0 ? '+' : ''}{ct.change_percent.toFixed(1)}%
                          </Badge>
                        )}
                      </div>
                      {ct.min_price != null && ct.max_price != null && (
                        <p className="text-[10px] mt-2" style={{ color: 'var(--text-3)' }}>
                          Диапазон: {ct.min_price.toLocaleString('ru')} — {ct.max_price.toLocaleString('ru')} {'\u20BD'}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="flex items-center justify-center h-40 rounded-lg" style={{ background: 'var(--bg-2, #f8f9fb)' }}>
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

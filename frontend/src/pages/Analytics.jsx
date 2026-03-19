import { useEffect, useState } from 'react';
import { BarChart2, TrendingUp, CheckCircle2, XCircle, RefreshCw, Activity } from 'lucide-react';
import { getRepricerRuns, getProducts } from '../api/client';
import { formatMsk } from '../utils/date';
import {
  AreaChart, Area, BarChart, Bar, PieChart, Pie, Cell,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer,
} from 'recharts';
import toast from 'react-hot-toast';

const PIE_COLORS = { rrp: '#00c8ff', competitor: '#0066ff', none: '#d1d5db' };
const PIE_LABELS = { rrp: 'РРЦ', competitor: 'Конкурент', none: 'Без настройки' };

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

export default function Analytics() {
  const [loading, setLoading] = useState(true);
  const [runs, setRuns] = useState([]);
  const [products, setProducts] = useState([]);

  useEffect(() => {
    Promise.all([
      getRepricerRuns({ limit: 100 }),
      getProducts({ page: 1, page_size: 1000 }),
    ])
      .then(([runsRes, prodRes]) => {
        setRuns(runsRes.data.runs);
        setProducts(prodRes.data.products);
      })
      .catch(() => toast.error('Ошибка загрузки аналитики'))
      .finally(() => setLoading(false));
  }, []);

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
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {[
          {
            label: 'Всего прогонов',
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

      {/* Runs activity area chart */}
      <div className="card">
        <div className="flex items-center gap-2.5 mb-5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: 'rgba(0,200,255,.1)' }}>
            <BarChart2 className="w-3.5 h-3.5" style={{ color: '#00c8ff' }} />
          </div>
          <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
            Активность прогонов
          </h2>
          <span className="text-xs ml-auto" style={{ color: 'var(--text-3)' }}>
            До 30 последних прогонов
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
                contentStyle={{ border: '1px solid #e8eaf0', borderRadius: 10, fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.08)' }}
                formatter={(v, name) => [v, name === 'total' ? 'Всего товаров' : 'Обновлено']}
              />
              <Legend formatter={(v) => (v === 'total' ? 'Всего товаров' : 'Обновлено')} wrapperStyle={{ fontSize: 11 }} />
              <Area type="monotone" dataKey="total" stroke="#00c8ff" strokeWidth={1.5} fill="url(#analyticsTotal)" dot={false} activeDot={{ r: 3 }} />
              <Area type="monotone" dataKey="updated" stroke="#16a34a" strokeWidth={2} fill="url(#analyticsUpdated)" dot={false} activeDot={{ r: 3 }} />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </div>

      {/* Bottom row: bar chart + pie */}
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
                  contentStyle={{ border: '1px solid #e8eaf0', borderRadius: 10, fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.08)' }}
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
                    contentStyle={{ border: '1px solid #e8eaf0', borderRadius: 10, fontSize: 12, boxShadow: '0 4px 16px rgba(0,0,0,0.08)' }}
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
    </div>
  );
}

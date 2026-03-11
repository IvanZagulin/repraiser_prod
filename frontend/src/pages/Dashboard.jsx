import { useEffect, useState } from 'react';
import { TrendingUp, Package, PlayCircle, Users, Zap, ChevronRight, Activity, BarChart2 } from 'lucide-react';
import { getProducts, getRepricerRuns, getAccounts, runRepricer } from '../api/client';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { formatMsk } from '../utils/date';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';

export default function Dashboard() {
  const navigate = useNavigate();
  const [stats, setStats] = useState({
    totalAccounts: 0,
    totalProducts: 0,
    activeProducts: 0,
    inactiveProducts: 0,
  });
  const [recentRuns, setRecentRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const [runsChartData, setRunsChartData] = useState([]);

  useEffect(() => { loadStats(); }, []);

  const loadStats = async () => {
    try {
      const [accountsRes, productsRes, runsRes] = await Promise.all([
        getAccounts(),
        getProducts({ page: 1, page_size: 1000 }),
        getRepricerRuns({ limit: 5 }),
      ]);
      const accounts = accountsRes.data.accounts;
      const products = productsRes.data.products;
      const runs     = runsRes.data.runs;
      const chartRuns = [...runs].reverse().map((r) => ({
        label: formatMsk(r.started_at, 'dd.MM HH:mm'),
        updated: r.updated_count,
        total: r.total_products,
        failed: r.status === 'failed' ? 1 : 0,
      }));
      setRunsChartData(chartRuns);
      setStats({
        totalAccounts:   accounts.length,
        totalProducts:   products.length,
        activeProducts:  products.filter((p) => p.config?.is_active).length,
        inactiveProducts: products.filter((p) => p.config && !p.config.is_active).length,
      });
      setRecentRuns(runs);
    } catch {
      toast.error('Ошибка загрузки статистики');
    } finally {
      setLoading(false);
    }
  };

  const handleRunAll = async () => {
    setRunning(true);
    try {
      toast.loading('Запуск репрайсера...', { id: 'run-all' });
      await runRepricer(null);
      toast.success('Репрайсер завершен', { id: 'run-all' });
      loadStats();
    } catch {
      toast.error('Ошибка запуска', { id: 'run-all' });
    } finally {
      setRunning(false);
    }
  };

  const getStatusBadge = (status) => {
    if (status === 'completed') return <span className="badge badge-success">Завершен</span>;
    if (status === 'running')   return <span className="badge badge-info">Выполняется</span>;
    if (status === 'failed')    return <span className="badge badge-error">Ошибка</span>;
    return <span className="badge">{status}</span>;
  };

  const statCards = [
    {
      title: 'Аккаунты',
      value: stats.totalAccounts,
      icon: Users,
      accent: '#6366f1',
      numColor: '#4338ca',
      iconColor: '#6366f1',
      iconBg: '#eef2ff',
    },
    {
      title: 'Всего товаров',
      value: stats.totalProducts,
      icon: Package,
      accent: '#3b82f6',
      numColor: '#1d4ed8',
      iconColor: '#3b82f6',
      iconBg: '#eff6ff',
    },
    {
      title: 'Активных',
      value: stats.activeProducts,
      icon: TrendingUp,
      accent: '#22c55e',
      numColor: '#15803d',
      iconColor: '#16a34a',
      iconBg: '#f0fdf4',
    },
    {
      title: 'Неактивных',
      value: stats.inactiveProducts,
      icon: PlayCircle,
      accent: '#f59e0b',
      numColor: '#b45309',
      iconColor: '#d97706',
      iconBg: '#fffbeb',
    },
  ];

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <div className="spinner" />
        <p className="text-sm animate-pulse" style={{ color: 'var(--text-3)' }}>Загрузка данных...</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">

      {/* ─── HEADER ─── */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-bold page-h1">
            Главная
          </h1>
          <p className="text-sm mt-1.5 flex items-center gap-1.5" style={{ color: 'var(--text-3)' }}>
            <Activity className="w-3.5 h-3.5" />
            Обзор системы управления ценами
          </p>
        </div>

        <button
          onClick={handleRunAll}
          disabled={running}
          className="btn btn-primary gap-2"
        >
          {running ? (
            <>
              <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              Запуск...
            </>
          ) : (
            <>
              <Zap className="w-4 h-4" />
              Запустить репрайсер
            </>
          )}
        </button>
      </div>

      {/* ─── STAT CARDS ─── */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map((stat, idx) => (
          <div
            key={idx}
            className="stat-card"
            style={{ borderLeft: `4px solid ${stat.accent}` }}
          >
            <div className="flex items-center justify-between mb-3">
              <p className="text-[11px] font-semibold uppercase tracking-widest" style={{ color: '#9ca3af' }}>
                {stat.title}
              </p>
              <div className="w-8 h-8 rounded-lg flex items-center justify-center" style={{ background: stat.iconBg }}>
                <stat.icon className="w-4 h-4" style={{ color: stat.iconColor }} />
              </div>
            </div>
            <p className="text-4xl font-extrabold stat-number" style={{ color: stat.numColor }}>
              {stat.value}
            </p>
          </div>
        ))}
      </div>

      {/* ─── RUNS CHART ─── */}
      {runsChartData.length > 1 && (
        <div className="card">
          <div className="flex items-center gap-2.5 mb-5">
            <div className="w-7 h-7 rounded-lg flex items-center justify-center" style={{ background: '#eef2ff' }}>
              <BarChart2 className="w-3.5 h-3.5" style={{ color: '#6366f1' }} />
            </div>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Активность прогонов
            </h2>
            <span className="text-xs ml-auto" style={{ color: 'var(--text-3)' }}>
              Последние {runsChartData.length} прогонов
            </span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <AreaChart data={runsChartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
              <defs>
                <linearGradient id="gradTotal" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#6366f1" stopOpacity={0.15} />
                  <stop offset="95%" stopColor="#6366f1" stopOpacity={0} />
                </linearGradient>
                <linearGradient id="gradUpdated" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#16a34a" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#16a34a" stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f4" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 10, fill: '#9ca3af' }}
                tickLine={false}
                interval="preserveStartEnd"
              />
              <YAxis
                tick={{ fontSize: 10, fill: '#9ca3af' }}
                tickLine={false}
                axisLine={false}
                width={32}
                allowDecimals={false}
              />
              <Tooltip
                contentStyle={{
                  border: '1px solid #e8eaf0',
                  borderRadius: 10,
                  fontSize: 12,
                  boxShadow: '0 4px 16px rgba(0,0,0,0.08)',
                }}
                formatter={(v, name) => [v, name === 'total' ? 'Всего товаров' : 'Обновлено']}
              />
              <Legend
                formatter={(v) => (v === 'total' ? 'Всего товаров' : 'Обновлено')}
                wrapperStyle={{ fontSize: 11 }}
              />
              <Area
                type="monotone"
                dataKey="total"
                stroke="#6366f1"
                strokeWidth={1.5}
                fill="url(#gradTotal)"
                dot={false}
                activeDot={{ r: 3 }}
              />
              <Area
                type="monotone"
                dataKey="updated"
                stroke="#16a34a"
                strokeWidth={2}
                fill="url(#gradUpdated)"
                dot={false}
                activeDot={{ r: 3 }}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ─── RECENT RUNS ─── */}
      <div className="card">
        {/* card header */}
        <div className="flex items-center justify-between mb-5">
          <div className="flex items-center gap-2.5">
            <div
              className="w-7 h-7 rounded-lg flex items-center justify-center"
              style={{ background: '#eef2ff' }}
            >
              <PlayCircle className="w-3.5 h-3.5" style={{ color: '#6366f1' }} />
            </div>
            <h2 className="text-sm font-semibold" style={{ color: 'var(--text)' }}>
              Последние прогоны
            </h2>
          </div>
          <button
            onClick={() => navigate('/runs')}
            className="btn btn-ghost btn-sm gap-1.5 text-xs"
          >
            Все прогоны
            <ChevronRight className="w-3.5 h-3.5" />
          </button>
        </div>

        {recentRuns.length === 0 ? (
          <div className="text-center py-10">
            <PlayCircle className="w-10 h-10 mx-auto mb-3 opacity-10" style={{ color: 'var(--text)' }} />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Прогоны ещё не выполнялись</p>
          </div>
        ) : (
          <div className="space-y-2">
            {recentRuns.map((run, i) => (
              <div
                key={run.id}
                className="run-row"
                style={{ animationDelay: `${i * 0.06}s` }}
              >
                <div className="flex items-center gap-3">
                  <span
                    className="text-xs font-mono tabular-nums w-7 text-right"
                    style={{ color: 'var(--text-3)' }}
                  >
                    #{run.id}
                  </span>
                  {getStatusBadge(run.status)}
                  <span className="text-xs" style={{ color: 'var(--text-3)' }}>
                    {run.run_type === 'scheduled' ? '🔄 Авто' : '👆 Ручной'}
                  </span>
                </div>

                <div className="flex items-center gap-5 text-xs">
                  <span style={{ color: 'var(--text-2)' }}>
                    <span className="font-semibold" style={{ color: '#16a34a' }}>
                      {run.updated_count}
                    </span>
                    <span style={{ color: 'var(--text-3)' }}>/{run.total_products}</span>
                    <span style={{ color: 'var(--text-3)' }}> товаров</span>
                  </span>
                  <span style={{ color: 'var(--text-3)' }}>
                    {formatMsk(run.started_at, 'dd.MM HH:mm')}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}

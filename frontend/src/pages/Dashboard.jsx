import { useEffect, useState } from 'react';
import { TrendingUp, Package, PlayCircle, Users, Zap, ChevronRight, Activity, BarChart2, Loader2 } from 'lucide-react';
import { getProducts, getRepricerRuns, getAccounts, runRepricer } from '../api/client';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { formatMsk } from '../utils/date';
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

const StatusBadge = ({ status }) => {
  if (status === 'completed') return <Badge variant="success">Завершён</Badge>;
  if (status === 'running')   return <Badge variant="info">Выполняется</Badge>;
  if (status === 'failed')    return <Badge variant="destructive">Ошибка</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
};

export default function Dashboard() {
  const navigate  = useNavigate();
  const [stats, setStats] = useState({
    totalAccounts: 0, totalProducts: 0,
    activeProducts: 0, inactiveProducts: 0,
  });
  const [recentRuns,    setRecentRuns]    = useState([]);
  const [runsChartData, setRunsChartData] = useState([]);
  const [loading,  setLoading]  = useState(true);
  const [running,  setRunning]  = useState(false);

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
      setRunsChartData([...runs].reverse().map((r) => ({
        label:   formatMsk(r.started_at, 'dd.MM HH:mm'),
        updated: r.updated_count,
        total:   r.total_products,
      })));
      setStats({
        totalAccounts:    accounts.length,
        totalProducts:    products.length,
        activeProducts:   products.filter((p) => p.config?.is_active).length,
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
      toast.success('Репрайсер завершён', { id: 'run-all' });
      loadStats();
    } catch {
      toast.error('Ошибка запуска', { id: 'run-all' });
    } finally {
      setRunning(false);
    }
  };

  const statCards = [
    { title: 'Аккаунты',   value: stats.totalAccounts,    icon: Users,      colorClass: 'text-blue-600 dark:text-blue-400' },
    { title: 'Товаров',    value: stats.totalProducts,    icon: Package,    colorClass: 'text-violet-600 dark:text-violet-400' },
    { title: 'Активных',   value: stats.activeProducts,   icon: TrendingUp, colorClass: 'text-emerald-600 dark:text-emerald-400' },
    { title: 'Неактивных', value: stats.inactiveProducts, icon: PlayCircle, colorClass: 'text-amber-600 dark:text-amber-400' },
  ];

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center gap-3">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        <span className="text-sm text-muted-foreground">Загрузка...</span>
      </div>
    );
  }

  return (
    <div className="space-y-6">

      {/* ── Header ── */}
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Главная</h1>
          <p className="mt-1 flex items-center gap-1.5 text-sm text-muted-foreground">
            <Activity className="h-3.5 w-3.5" />
            Обзор системы управления ценами
          </p>
        </div>
        <Button onClick={handleRunAll} disabled={running}>
          {running
            ? <><Loader2 className="h-4 w-4 animate-spin" /> Запуск...</>
            : <><Zap className="h-4 w-4" /> Запустить репрайсер</>
          }
        </Button>
      </div>

      {/* ── Stat cards ── */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {statCards.map((s, i) => (
          <Card key={i} className="stat-card">
            <CardContent className="p-5">
              <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  {s.title}
                </p>
                <s.icon className={`h-4 w-4 ${s.colorClass}`} />
              </div>
              <p className="stat-number text-3xl font-bold">{s.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ── Activity chart ── */}
      {runsChartData.length > 1 && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <BarChart2 className="h-4 w-4 text-muted-foreground" />
                Активность прогонов
              </CardTitle>
              <span className="text-xs text-muted-foreground">
                Последние {runsChartData.length} прогонов
              </span>
            </div>
          </CardHeader>
          <CardContent>
            <ResponsiveContainer width="100%" height={200}>
              <AreaChart data={runsChartData} margin={{ top: 4, right: 16, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="gTotal" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="hsl(var(--primary))" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="hsl(var(--primary))" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="gUpdated" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%"  stopColor="#10b981" stopOpacity={0.2} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                <XAxis
                  dataKey="label"
                  tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false} interval="preserveStartEnd"
                />
                <YAxis
                  tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false} axisLine={false} width={32} allowDecimals={false}
                />
                <Tooltip
                  contentStyle={{
                    border: '1px solid hsl(var(--border))',
                    borderRadius: '6px', fontSize: 12,
                    background: 'hsl(var(--card))',
                    color: 'hsl(var(--foreground))',
                  }}
                  formatter={(v, name) => [v, name === 'total' ? 'Всего товаров' : 'Обновлено']}
                />
                <Legend
                  formatter={(v) => v === 'total' ? 'Всего товаров' : 'Обновлено'}
                  wrapperStyle={{ fontSize: 11 }}
                />
                <Area
                  type="monotone" dataKey="total"
                  stroke="hsl(var(--primary))" strokeWidth={1.5}
                  fill="url(#gTotal)" dot={false} activeDot={{ r: 3 }}
                />
                <Area
                  type="monotone" dataKey="updated"
                  stroke="#10b981" strokeWidth={2}
                  fill="url(#gUpdated)" dot={false} activeDot={{ r: 3 }}
                />
              </AreaChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
      )}

      {/* ── Recent runs ── */}
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <PlayCircle className="h-4 w-4 text-muted-foreground" />
              Последние прогоны
            </CardTitle>
            <Button variant="ghost" size="xs" onClick={() => navigate('/runs')} className="gap-1">
              Все прогоны <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <div className="py-10 text-center">
              <PlayCircle className="mx-auto mb-3 h-10 w-10 text-muted-foreground/20" />
              <p className="text-sm text-muted-foreground">Прогоны ещё не выполнялись</p>
            </div>
          ) : (
            <div className="space-y-1.5">
              {recentRuns.map((run, i) => (
                <div
                  key={run.id}
                  className="run-row"
                  style={{ animationDelay: `${i * 0.05}s` }}
                >
                  <div className="flex items-center gap-3">
                    <span className="w-7 text-right font-mono text-xs tabular-nums text-muted-foreground">
                      #{run.id}
                    </span>
                    <StatusBadge status={run.status} />
                    <span className="text-xs text-muted-foreground">
                      {run.run_type === 'scheduled' ? '↻ Авто' : '↑ Ручной'}
                    </span>
                  </div>
                  <div className="flex items-center gap-5 text-xs">
                    <span>
                      <span className="font-semibold text-emerald-600 dark:text-emerald-400">{run.updated_count}</span>
                      <span className="text-muted-foreground">/{run.total_products} товаров</span>
                    </span>
                    <span className="text-muted-foreground">
                      {formatMsk(run.started_at, 'dd.MM HH:mm')}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

    </div>
  );
}

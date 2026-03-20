import { useEffect, useState, useMemo } from 'react';
import {
  TrendingUp, Package, PlayCircle, Users, Zap, ChevronRight, Activity,
  BarChart2, Loader2, DollarSign, Eye, EyeOff, GripVertical, Settings2,
  X, ScrollText, Clock,
} from 'lucide-react';
import {
  getProducts, getRepricerRuns, getAccounts, runRepricer,
  getSavings, getActivityLog,
} from '../api/client';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import { formatMsk } from '../utils/date';
import {
  AreaChart, Area, PieChart, Pie, Cell, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';

// ─── Widget config ─────────────────────────────────────────────────

const WIDGET_DEFS = [
  { id: 'stats',       label: 'Статистика',          icon: BarChart2 },
  { id: 'chart',       label: 'График прогонов',     icon: Activity },
  { id: 'runs',        label: 'Последние прогоны',   icon: PlayCircle },
  { id: 'savings',     label: 'Экономия за неделю',  icon: DollarSign },
  { id: 'strategies',  label: 'Стратегии товаров',   icon: Package },
  { id: 'activity',    label: 'Последние действия',  icon: ScrollText },
];

const DEFAULT_VISIBLE = ['stats', 'chart', 'runs', 'savings', 'strategies', 'activity'];
const STORAGE_KEY = 'dashboard_widgets';

function loadWidgetConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {}
  return DEFAULT_VISIBLE;
}

function saveWidgetConfig(ids) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}

// ─── Sub-components ────────────────────────────────────────────────

const StatusBadge = ({ status }) => {
  if (status === 'completed') return <Badge variant="success">Завершён</Badge>;
  if (status === 'running')   return <Badge variant="info">Выполняется</Badge>;
  if (status === 'failed')    return <Badge variant="destructive">Ошибка</Badge>;
  return <Badge variant="secondary">{status}</Badge>;
};

const PIE_COLORS = { rrp: '#00c8ff', competitor: '#0066ff', none: '#d1d5db' };
const PIE_LABELS = { rrp: 'РРЦ', competitor: 'Конкурент', none: 'Без настройки' };

const EVENT_ICONS = {
  price_changed: { icon: DollarSign, color: '#22c55e' },
  strategy_activated: { icon: Zap, color: '#00c8ff' },
  account_created: { icon: Users, color: '#3b82f6' },
  product_created: { icon: Package, color: '#3b82f6' },
  repricer_run: { icon: PlayCircle, color: '#00c8ff' },
};

// ─── Widget Settings Modal ─────────────────────────────────────────

function WidgetSettingsModal({ visible, onClose, widgetIds, setWidgetIds }) {
  const toggle = (id) => {
    setWidgetIds((prev) => {
      const next = prev.includes(id)
        ? prev.filter((w) => w !== id)
        : [...prev, id];
      saveWidgetConfig(next);
      return next;
    });
  };

  if (!visible) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm">
      <div className="bg-card border border-border rounded-2xl shadow-2xl w-full max-w-md p-6 animate-fade-in-up">
        <div className="flex items-center justify-between mb-5">
          <h2 className="text-lg font-semibold text-foreground">Настройка виджетов</h2>
          <button onClick={onClose} className="text-muted-foreground hover:text-foreground">
            <X className="w-5 h-5" />
          </button>
        </div>
        <p className="text-sm text-muted-foreground mb-4">
          Выберите, какие виджеты отображать на главной странице
        </p>
        <div className="space-y-2">
          {WIDGET_DEFS.map((w) => {
            const active = widgetIds.includes(w.id);
            return (
              <button
                key={w.id}
                onClick={() => toggle(w.id)}
                className={`flex items-center gap-3 w-full rounded-xl border-2 px-4 py-3 transition-all ${
                  active
                    ? 'border-[#00c8ff] bg-[#00c8ff]/10'
                    : 'border-border bg-card hover:border-[#00c8ff]/40'
                }`}
              >
                <w.icon className={`w-4 h-4 ${active ? 'text-[#00c8ff]' : 'text-muted-foreground'}`} />
                <span className={`text-sm font-medium ${active ? 'text-foreground' : 'text-muted-foreground'}`}>
                  {w.label}
                </span>
                <span className="ml-auto">
                  {active
                    ? <Eye className="w-4 h-4 text-[#00c8ff]" />
                    : <EyeOff className="w-4 h-4 text-muted-foreground/40" />
                  }
                </span>
              </button>
            );
          })}
        </div>
        <Button onClick={onClose} className="w-full mt-5" style={{ background: '#00c8ff' }}>
          Готово
        </Button>
      </div>
    </div>
  );
}

// ─── Main Dashboard ────────────────────────────────────────────────

export default function Dashboard() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [widgetIds, setWidgetIds] = useState(loadWidgetConfig);

  const [stats, setStats] = useState({
    totalAccounts: 0, totalProducts: 0,
    activeProducts: 0, inactiveProducts: 0,
  });
  const [recentRuns, setRecentRuns] = useState([]);
  const [runsChartData, setRunsChartData] = useState([]);
  const [products, setProducts] = useState([]);
  const [savings, setSavings] = useState(0);
  const [activityItems, setActivityItems] = useState([]);

  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    try {
      const [accountsRes, productsRes, runsRes, savingsRes, activityRes] = await Promise.all([
        getAccounts().catch(() => ({ data: { accounts: [] } })),
        getProducts({ page: 1, page_size: 1000 }).catch(() => ({ data: { products: [] } })),
        getRepricerRuns({ limit: 10 }).catch(() => ({ data: { runs: [] } })),
        getSavings(7).catch(() => ({ data: { total_savings_rub: 0 } })),
        getActivityLog({ page: 1, page_size: 5 }).catch(() => ({ data: { items: [] } })),
      ]);

      const accounts = accountsRes.data.accounts || [];
      const prods = productsRes.data.products || [];
      const runs = runsRes.data.runs || [];

      setProducts(prods);
      setRecentRuns(runs);
      setSavings(savingsRes.data?.total_savings_rub ?? 0);
      setActivityItems(activityRes.data?.items ?? []);

      setRunsChartData([...runs].reverse().map((r) => ({
        label: formatMsk(r.started_at, 'dd.MM HH:mm'),
        updated: r.updated_count,
        total: r.total_products,
      })));

      setStats({
        totalAccounts: accounts.length,
        totalProducts: prods.length,
        activeProducts: prods.filter((p) => p.config?.is_active).length,
        inactiveProducts: prods.filter((p) => p.config && !p.config.is_active).length,
      });
    } catch {
      toast.error('Ошибка загрузки данных');
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
      loadAll();
    } catch {
      toast.error('Ошибка запуска', { id: 'run-all' });
    } finally {
      setRunning(false);
    }
  };

  // Derived data
  const stratMap = useMemo(() => {
    const m = { rrp: 0, competitor: 0, none: 0 };
    products.forEach((p) => {
      const s = p.config?.strategy;
      if (s === 'rrp') m.rrp++;
      else if (s === 'competitor') m.competitor++;
      else m.none++;
    });
    return m;
  }, [products]);

  const pieData = Object.entries(stratMap)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({ name: PIE_LABELS[key], value, key }));

  const isVisible = (id) => widgetIds.includes(id);

  // ── Stat cards data ──
  const statCards = [
    { title: 'Аккаунты',   value: stats.totalAccounts,    icon: Users,      color: 'var(--neon)' },
    { title: 'Товаров',    value: stats.totalProducts,    icon: Package,    color: 'var(--accent-blue)' },
    { title: 'Активных',   value: stats.activeProducts,   icon: TrendingUp, color: '#22c55e' },
    { title: 'Неактивных', value: stats.inactiveProducts, icon: PlayCircle, color: '#f59e0b' },
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
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setShowSettings(true)}
            className="gap-1.5"
          >
            <Settings2 className="h-4 w-4" />
            Виджеты
          </Button>
          <Button onClick={handleRunAll} disabled={running}>
            {running
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Запуск...</>
              : <><Zap className="h-4 w-4" /> Запустить репрайсер</>
            }
          </Button>
        </div>
      </div>

      {/* ── Stat cards ── */}
      {isVisible('stats') && (
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {statCards.map((s, i) => (
            <Card key={i} className="stat-card">
              <CardContent className="p-5">
                <div className="flex items-center justify-between mb-3">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {s.title}
                  </p>
                  <s.icon className="h-4 w-4" style={{ color: s.color }} />
                </div>
                <p className="stat-number text-3xl font-bold font-mono" style={{ color: s.color }}>{s.value}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* ── Savings + Strategies row ── */}
      {(isVisible('savings') || isVisible('strategies')) && (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

          {/* Savings widget */}
          {isVisible('savings') && (
            <Card className="border" style={{ borderColor: 'rgba(22,163,74,.15)' }}>
              <CardContent className="p-5">
                <div className="flex items-center justify-between mb-4">
                  <div className="flex items-center gap-2.5">
                    <div className="w-8 h-8 rounded-lg flex items-center justify-center"
                      style={{ background: 'rgba(22,163,74,.1)' }}>
                      <DollarSign className="w-4 h-4" style={{ color: '#16a34a' }} />
                    </div>
                    <div>
                      <p className="text-sm font-semibold text-foreground">Экономия за 7 дней</p>
                      <p className="text-xs text-muted-foreground">Снижение цен по стратегиям</p>
                    </div>
                  </div>
                  <Button variant="ghost" size="xs" onClick={() => navigate('/analytics')} className="gap-1">
                    Подробнее <ChevronRight className="h-3.5 w-3.5" />
                  </Button>
                </div>
                <p className="text-4xl font-extrabold font-mono" style={{ color: '#16a34a' }}>
                  {savings.toLocaleString('ru')} <span className="text-lg">₽</span>
                </p>
              </CardContent>
            </Card>
          )}

          {/* Strategies pie widget */}
          {isVisible('strategies') && (
            <Card>
              <CardContent className="p-5">
                <div className="flex items-center gap-2.5 mb-4">
                  <div className="w-8 h-8 rounded-lg flex items-center justify-center"
                    style={{ background: 'rgba(0,200,255,.1)' }}>
                    <Package className="w-4 h-4" style={{ color: '#00c8ff' }} />
                  </div>
                  <p className="text-sm font-semibold text-foreground">Стратегии товаров</p>
                </div>
                {pieData.length === 0 ? (
                  <div className="flex items-center justify-center h-32">
                    <p className="text-sm text-muted-foreground">Нет товаров</p>
                  </div>
                ) : (
                  <div className="flex items-center gap-4">
                    <ResponsiveContainer width="50%" height={130}>
                      <PieChart>
                        <Pie
                          data={pieData}
                          cx="50%" cy="50%"
                          innerRadius={35} outerRadius={60}
                          dataKey="value" labelLine={false}
                        >
                          {pieData.map((entry) => (
                            <Cell key={entry.key} fill={PIE_COLORS[entry.key]} />
                          ))}
                        </Pie>
                        <Tooltip
                          contentStyle={{
                            border: '1px solid hsl(var(--border))',
                            borderRadius: 6, fontSize: 12,
                            background: 'hsl(var(--card))',
                            color: 'hsl(var(--foreground))',
                          }}
                        />
                      </PieChart>
                    </ResponsiveContainer>
                    <div className="flex flex-col gap-2 flex-1">
                      {pieData.map((d) => (
                        <div key={d.key} className="flex items-center gap-2">
                          <span className="w-2.5 h-2.5 rounded-sm flex-shrink-0"
                            style={{ background: PIE_COLORS[d.key] }} />
                          <span className="text-xs text-muted-foreground">{d.name}</span>
                          <span className="text-xs font-bold ml-auto text-foreground">{d.value}</span>
                        </div>
                      ))}
                      <div className="pt-1 mt-1 border-t border-border">
                        <div className="flex items-center justify-between">
                          <span className="text-xs text-muted-foreground">Всего</span>
                          <span className="text-sm font-bold text-foreground">{products.length}</span>
                        </div>
                      </div>
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* ── Activity chart ── */}
      {isVisible('chart') && runsChartData.length > 1 && (
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
                    <stop offset="5%"  stopColor="#00c8ff" stopOpacity={0.15} />
                    <stop offset="95%" stopColor="#00c8ff" stopOpacity={0} />
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
                  stroke="#00c8ff" strokeWidth={1.5}
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

      {/* ── Bottom row: Recent runs + Activity log ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

        {/* Recent runs */}
        {isVisible('runs') && (
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
                  {recentRuns.slice(0, 5).map((run, i) => (
                    <div key={run.id} className="run-row" style={{ animationDelay: `${i * 0.05}s` }}>
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
                          <span className="text-muted-foreground">/{run.total_products}</span>
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
        )}

        {/* Recent activity */}
        {isVisible('activity') && (
          <Card>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                  <ScrollText className="h-4 w-4 text-muted-foreground" />
                  Последние действия
                </CardTitle>
                <Button variant="ghost" size="xs" onClick={() => navigate('/activity')} className="gap-1">
                  Весь журнал <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            </CardHeader>
            <CardContent>
              {activityItems.length === 0 ? (
                <div className="py-10 text-center">
                  <ScrollText className="mx-auto mb-3 h-10 w-10 text-muted-foreground/20" />
                  <p className="text-sm text-muted-foreground">Нет записей</p>
                </div>
              ) : (
                <div className="space-y-2">
                  {activityItems.map((item, i) => {
                    const cfg = EVENT_ICONS[item.event_type] || { icon: Activity, color: '#9ca3af' };
                    const Icon = cfg.icon;
                    let details = null;
                    try {
                      details = typeof item.details === 'string' ? JSON.parse(item.details) : item.details;
                    } catch {}

                    return (
                      <div
                        key={item.id || i}
                        className="flex items-center gap-3 py-2 px-2 rounded-lg transition-colors hover:bg-muted/50"
                        style={{ animationDelay: `${i * 0.05}s` }}
                      >
                        <div
                          className="flex items-center justify-center w-7 h-7 rounded-full shrink-0"
                          style={{ background: cfg.color + '1a', color: cfg.color }}
                        >
                          <Icon className="w-3.5 h-3.5" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-foreground truncate">
                            {item.event_type?.replace(/_/g, ' ')}
                          </p>
                          {details?.message && (
                            <p className="text-[11px] text-muted-foreground truncate">{details.message}</p>
                          )}
                        </div>
                        <span className="text-[11px] text-muted-foreground whitespace-nowrap shrink-0">
                          {formatMsk(item.created_at || item.timestamp, 'dd.MM HH:mm')}
                        </span>
                      </div>
                    );
                  })}
                </div>
              )}
            </CardContent>
          </Card>
        )}
      </div>

      {/* ── Widget Settings Modal ── */}
      <WidgetSettingsModal
        visible={showSettings}
        onClose={() => setShowSettings(false)}
        widgetIds={widgetIds}
        setWidgetIds={setWidgetIds}
      />

    </div>
  );
}

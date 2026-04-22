import { useEffect, useState, useMemo, useRef, useCallback } from 'react';
import {
  TrendingUp, Package, PlayCircle, Users, Zap, ChevronRight, Activity,
  BarChart2, Loader2, DollarSign, Eye, EyeOff, GripVertical, Settings2,
  X, Clock,
} from 'lucide-react';
import {
  getProducts, getProductStats, getRepricerRuns, getAccounts, runRepricer,
} from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
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
  { id: 'chart',       label: 'График запусков',     icon: Activity },
  { id: 'runs',        label: 'Последние запуски',   icon: PlayCircle },
  { id: 'strategies',  label: 'Стратегии товаров',   icon: Package },
];

const DEFAULT_ORDER = ['stats', 'chart', 'runs', 'strategies'];
const STORAGE_KEY = 'dashboard_widgets';
const ORDER_KEY = 'dashboard_widget_order';

function loadWidgetConfig() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {}
  return DEFAULT_ORDER;
}

function loadWidgetOrder() {
  try {
    const raw = localStorage.getItem(ORDER_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {}
  return DEFAULT_ORDER;
}

function saveWidgetConfig(ids) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(ids));
}

function saveWidgetOrder(order) {
  localStorage.setItem(ORDER_KEY, JSON.stringify(order));
}

// ─── Sub-components ────────────────────────────────────────────────

const PIE_COLORS = { rrp: '#00c8ff', competitor: '#0066ff', none: '#d1d5db' };
const PIE_LABELS = { rrp: 'РРЦ', competitor: 'Конкурент', none: 'Без настройки' };


// ─── Widget Settings Modal with DnD ───────────────────────────────

function WidgetSettingsModal({ visible, onClose, widgetIds, setWidgetIds, widgetOrder, setWidgetOrder }) {
  const [dragIdx, setDragIdx] = useState(null);
  const [overIdx, setOverIdx] = useState(null);

  const toggle = (id) => {
    setWidgetIds((prev) => {
      const next = prev.includes(id)
        ? prev.filter((w) => w !== id)
        : [...prev, id];
      saveWidgetConfig(next);
      return next;
    });
  };

  const handleDragStart = (e, idx) => {
    setDragIdx(idx);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', idx.toString());
  };

  const handleDragOver = (e, idx) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setOverIdx(idx);
  };

  const handleDrop = (e, dropIdx) => {
    e.preventDefault();
    if (dragIdx === null || dragIdx === dropIdx) {
      setDragIdx(null);
      setOverIdx(null);
      return;
    }
    setWidgetOrder((prev) => {
      const next = [...prev];
      const [moved] = next.splice(dragIdx, 1);
      next.splice(dropIdx, 0, moved);
      saveWidgetOrder(next);
      return next;
    });
    setDragIdx(null);
    setOverIdx(null);
  };

  const handleDragEnd = () => {
    setDragIdx(null);
    setOverIdx(null);
  };

  if (!visible) return null;

  // Order the defs by current widget order
  const orderedDefs = [...WIDGET_DEFS].sort((a, b) => {
    const ai = widgetOrder.indexOf(a.id);
    const bi = widgetOrder.indexOf(b.id);
    return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
  });

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
          Перетаскивайте для изменения порядка. Нажмите для показа/скрытия.
        </p>
        <div className="space-y-2">
          {orderedDefs.map((w, idx) => {
            const active = widgetIds.includes(w.id);
            const isDragging = dragIdx === idx;
            const isOver = overIdx === idx && dragIdx !== idx;
            return (
              <div
                key={w.id}
                draggable
                onDragStart={(e) => handleDragStart(e, idx)}
                onDragOver={(e) => handleDragOver(e, idx)}
                onDrop={(e) => handleDrop(e, idx)}
                onDragEnd={handleDragEnd}
                className={`flex items-center gap-3 w-full rounded-xl border-2 px-4 py-3 transition-all cursor-grab active:cursor-grabbing select-none ${
                  isDragging
                    ? 'opacity-40 scale-95'
                    : isOver
                    ? 'border-[#00c8ff] bg-[#00c8ff]/5 scale-[1.02]'
                    : active
                    ? 'border-[#00c8ff] bg-[#00c8ff]/10'
                    : 'border-border bg-card hover:border-[#00c8ff]/40'
                }`}
              >
                <GripVertical className="w-4 h-4 text-muted-foreground/50 shrink-0" />
                <w.icon className={`w-4 h-4 ${active ? 'text-[#00c8ff]' : 'text-muted-foreground'}`} />
                <span className={`text-sm font-medium ${active ? 'text-foreground' : 'text-muted-foreground'}`}>
                  {w.label}
                </span>
                <button
                  onClick={(e) => { e.stopPropagation(); toggle(w.id); }}
                  className="ml-auto p-1 rounded-lg hover:bg-muted/50 transition-colors"
                >
                  {active
                    ? <Eye className="w-4 h-4 text-[#00c8ff]" />
                    : <EyeOff className="w-4 h-4 text-muted-foreground/40" />
                  }
                </button>
              </div>
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
  const [widgetOrder, setWidgetOrder] = useState(loadWidgetOrder);

  // DnD state for dashboard itself
  const [dashDragId, setDashDragId] = useState(null);
  const [dashOverId, setDashOverId] = useState(null);

  const [stats, setStats] = useState({
    totalAccounts: 0, totalProducts: 0,
    activeProducts: 0, inactiveProducts: 0,
    rrp: null, competitor: null, noConfig: null,
  });
  const [recentRuns, setRecentRuns] = useState([]);
  const [runsChartData, setRunsChartData] = useState([]);
  const [products, setProducts] = useState([]);
  useEffect(() => { loadAll(); }, []);

  const loadAll = async () => {
    try {
      const [accountsRes, productsRes, runsRes, statsRes] = await Promise.all([
        getAccounts().catch(() => ({ data: { accounts: [] } })),
        getProducts({ page: 1, page_size: 50 }).catch(() => ({ data: { products: [] } })),
        getRepricerRuns({ limit: 10 }).catch(() => ({ data: { runs: [] } })),
        getProductStats().catch(() => ({ data: { total: 0, active: 0, inactive: 0 } })),
      ]);

      const accounts = accountsRes.data.accounts || [];
      const prods = productsRes.data.products || [];
      const runs = runsRes.data.runs || [];

      setProducts(prods);
      setRecentRuns(runs);

      setRunsChartData([...runs].reverse().map((r) => ({
        label: formatMsk(r.started_at, 'dd.MM HH:mm'),
        updated: r.updated_count,
        total: r.total_products,
      })));

      const s = statsRes.data;
      setStats({
        totalAccounts: accounts.length,
        totalProducts: s.total || prods.length,
        activeProducts: s.active ?? prods.filter((p) => p.config?.is_active).length,
        inactiveProducts: (s.total ?? prods.length) - (s.active ?? prods.filter((p) => p.config?.is_active).length),
        rrp: s.rrp ?? null,
        competitor: s.competitor ?? null,
        noConfig: s.no_config ?? null,
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

  // Derived data — берём из API (все товары), не из paginated списка
  const stratMap = useMemo(() => {
    if (stats.rrp !== null) {
      return { rrp: stats.rrp, competitor: stats.competitor, none: stats.noConfig ?? 0 };
    }
    // fallback: in-memory из загруженных товаров
    const m = { rrp: 0, competitor: 0, none: 0 };
    products.forEach((p) => {
      const s = p.config?.strategy;
      if (s === 'rrp') m.rrp++;
      else if (s === 'competitor') m.competitor++;
      else m.none++;
    });
    return m;
  }, [stats, products]);

  const pieData = Object.entries(stratMap)
    .filter(([, v]) => v > 0)
    .map(([key, value]) => ({ name: PIE_LABELS[key], value, key }));

  const isVisible = (id) => widgetIds.includes(id);

  // ── Dashboard DnD handlers ──
  const handleDashDragStart = useCallback((e, id) => {
    setDashDragId(id);
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', id);
  }, []);

  const handleDashDragOver = useCallback((e, id) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    setDashOverId(id);
  }, []);

  const handleDashDrop = useCallback((e, dropId) => {
    e.preventDefault();
    if (!dashDragId || dashDragId === dropId) {
      setDashDragId(null);
      setDashOverId(null);
      return;
    }
    setWidgetOrder((prev) => {
      const next = [...prev];
      const fromIdx = next.indexOf(dashDragId);
      const toIdx = next.indexOf(dropId);
      if (fromIdx === -1 || toIdx === -1) return prev;
      next.splice(fromIdx, 1);
      next.splice(toIdx, 0, dashDragId);
      saveWidgetOrder(next);
      return next;
    });
    setDashDragId(null);
    setDashOverId(null);
  }, [dashDragId]);

  const handleDashDragEnd = useCallback(() => {
    setDashDragId(null);
    setDashOverId(null);
  }, []);

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

  // ── Widget renderers ──
  const widgetRenderers = {
    stats: () => (
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
    ),

    chart: () => (
      runsChartData.length > 1 ? (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2 text-sm font-semibold">
                <BarChart2 className="h-4 w-4 text-muted-foreground" />
                Активность запусков
              </CardTitle>
              <span className="text-xs text-muted-foreground">
                Последние {runsChartData.length} запусков
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
      ) : null
    ),

    runs: () => (
      <Card>
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="flex items-center gap-2 text-sm font-semibold">
              <PlayCircle className="h-4 w-4 text-muted-foreground" />
              Последние запуски
            </CardTitle>
            <Button variant="ghost" size="xs" onClick={() => navigate('/runs')} className="gap-1">
              Все запуски <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {recentRuns.length === 0 ? (
            <div className="py-10 text-center">
              <PlayCircle className="mx-auto mb-3 h-10 w-10 text-muted-foreground/20" />
              <p className="text-sm text-muted-foreground">Запуски ещё не выполнялись</p>
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
    ),

    strategies: () => (
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
                    <span className="text-sm font-bold text-foreground">{stats.totalProducts}</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>
    ),

  };

  // Widgets that span full width
  const fullWidthWidgets = new Set(['stats', 'chart']);

  // Build ordered visible widgets
  const orderedWidgets = widgetOrder
    .filter((id) => isVisible(id) && widgetRenderers[id]);

  // Group into layout: full-width widgets go alone, others pair up
  const layoutGroups = [];
  let halfRow = [];

  orderedWidgets.forEach((id) => {
    if (fullWidthWidgets.has(id)) {
      if (halfRow.length > 0) {
        layoutGroups.push({ type: 'half', ids: [...halfRow] });
        halfRow = [];
      }
      layoutGroups.push({ type: 'full', ids: [id] });
    } else {
      halfRow.push(id);
      if (halfRow.length === 2) {
        layoutGroups.push({ type: 'half', ids: [...halfRow] });
        halfRow = [];
      }
    }
  });
  if (halfRow.length > 0) {
    layoutGroups.push({ type: 'half', ids: [...halfRow] });
  }

  const renderDraggableWidget = (id) => {
    const isDragging = dashDragId === id;
    const isOver = dashOverId === id && dashDragId !== id;
    const renderer = widgetRenderers[id];
    if (!renderer) return null;

    return (
      <div
        key={id}
        draggable
        onDragStart={(e) => handleDashDragStart(e, id)}
        onDragOver={(e) => handleDashDragOver(e, id)}
        onDrop={(e) => handleDashDrop(e, id)}
        onDragEnd={handleDashDragEnd}
        className={`relative group transition-all duration-200 ${
          isDragging ? 'opacity-30 scale-[0.98]' : ''
        } ${isOver ? 'ring-2 ring-[#00c8ff] ring-offset-2 ring-offset-background rounded-xl' : ''}`}
      >
        {/* Drag handle indicator */}
        <div className="absolute -left-1 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 transition-opacity z-10 cursor-grab active:cursor-grabbing">
          <div className="bg-card border border-border rounded-md shadow-sm p-1">
            <GripVertical className="w-3.5 h-3.5 text-muted-foreground" />
          </div>
        </div>
        {renderer()}
      </div>
    );
  };

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

      {/* ── Widgets by order ── */}
      {layoutGroups.map((group, gi) => {
        if (group.type === 'full') {
          return (
            <div key={`g-${gi}`}>
              {renderDraggableWidget(group.ids[0])}
            </div>
          );
        }
        return (
          <div key={`g-${gi}`} className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            {group.ids.map((id) => renderDraggableWidget(id))}
          </div>
        );
      })}

      {/* ── Widget Settings Modal ── */}
      <WidgetSettingsModal
        visible={showSettings}
        onClose={() => setShowSettings(false)}
        widgetIds={widgetIds}
        setWidgetIds={setWidgetIds}
        widgetOrder={widgetOrder}
        setWidgetOrder={setWidgetOrder}
      />

    </div>
  );
}

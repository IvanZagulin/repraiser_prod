import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import {
  Home, Package, LogOut, PlayCircle, FileText,
  SlidersHorizontal, Users, BarChart2, Moon, Sun,
  AlertTriangle, X, Clock, ScrollText, FileBarChart, Menu,
} from 'lucide-react';
import { useAuthStore } from '../store';
import { getAccounts } from '../api/client';
import toast from 'react-hot-toast';
import { useEffect, useState, useCallback } from 'react';
import { formatMsk } from '../utils/date';
import { differenceInDays, parseISO } from 'date-fns';
import { cn } from '@/lib/utils';
import { Separator } from '@/components/ui/separator';

const navItems = [
  { to: '/',              icon: Home,              label: 'Главная' },
  { to: '/products',      icon: Package,           label: 'Товары' },
  { to: '/analytics',     icon: BarChart2,         label: 'Аналитика' },
  { to: '/runs',          icon: PlayCircle,        label: 'Запуски' },
  { to: '/schedule',      icon: Clock,             label: 'Расписание' },
  { to: '/accounts',      icon: Users,             label: 'Аккаунты' },
  { to: '/reports',       icon: FileBarChart,      label: 'Отчёты' },
  { to: '/import-export', icon: FileText,          label: 'Импорт/Экспорт' },
  { to: '/settings',      icon: SlidersHorizontal, label: 'Настройки' },
];

function SidebarContent({ dark, setDark, user, handleLogout, tokenAlerts, alertDismissed, setAlertDismissed, closeSidebar }) {
  return (
    <>
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 px-4 border-b border-sidebar-border">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-lg flex-shrink-0"
          style={{
            background: 'linear-gradient(135deg, var(--neon), var(--accent-blue))',
            boxShadow: '0 2px 12px rgba(0,200,255,.3)',
          }}
        >
          <span className="w-2 h-2 rounded-full bg-white" />
        </div>
        <span className="font-mono text-sm font-bold tracking-wider text-foreground flex-1">QUASAR-X</span>
        <button onClick={closeSidebar} className="md:hidden p-1 text-muted-foreground hover:text-foreground">
          <X className="h-5 w-5" />
        </button>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-0.5 overflow-y-auto p-3">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            onClick={closeSidebar}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 rounded-[10px] px-3 py-2 text-sm font-medium transition-all',
                isActive
                  ? 'text-foreground font-semibold'
                  : 'text-muted-foreground hover:text-foreground'
              )
            }
            style={({ isActive }) =>
              isActive
                ? { background: 'var(--neon-glow)', color: 'var(--neon)', borderLeft: '3px solid var(--neon)' }
                : {}
            }
          >
            {({ isActive }) => (
              <>
                <item.icon
                  className="h-4 w-4 flex-shrink-0 transition-colors"
                  style={isActive ? { color: 'var(--neon)' } : {}}
                />
                <span>{item.label}</span>
              </>
            )}
          </NavLink>
        ))}
      </nav>

      {/* Plan badge */}
      {user && (
        <div className="mx-3 mb-1 rounded-lg p-3 text-xs" style={{ background: 'var(--neon-glow)', border: '1px solid var(--neon-dim)' }}>
          {user.plan ? (
            <>
              <span className="font-semibold" style={{ color: 'var(--neon)' }}>{user.plan.name}</span>
              <span className="text-muted-foreground ml-1">план</span>
            </>
          ) : user.trialEndsAt ? (
            <>
              <span className="font-semibold" style={{ color: 'var(--neon)' }}>Пробный период</span>
              <br />
              <span className="text-muted-foreground">
                до {formatMsk(user.trialEndsAt, 'dd.MM.yyyy')}
              </span>
            </>
          ) : (
            <span className="text-muted-foreground">Нет активного плана</span>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="space-y-0.5 p-3 border-t border-sidebar-border">
        <div className="flex items-center gap-2 rounded-[10px] px-3 py-2">
          <span
            className="h-2 w-2 rounded-full flex-shrink-0"
            style={{ background: 'var(--neon)', boxShadow: '0 0 8px var(--neon-dim)' }}
          />
          <span className="text-xs text-muted-foreground">Система активна</span>
        </div>

        <Separator className="my-1.5" />

        <button
          onClick={() => setDark((d) => !d)}
          className="flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-sm font-medium text-muted-foreground transition-all hover:text-foreground"
        >
          {dark
            ? <Sun className="h-4 w-4 flex-shrink-0" />
            : <Moon className="h-4 w-4 flex-shrink-0" />
          }
          <span>{dark ? 'Светлая тема' : 'Тёмная тема'}</span>
        </button>

        <button
          onClick={handleLogout}
          className="flex w-full items-center gap-2.5 rounded-[10px] px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
        >
          <LogOut className="h-4 w-4 flex-shrink-0" />
          <span>Выход</span>
        </button>
      </div>
    </>
  );
}

export default function Layout() {
  const navigate = useNavigate();
  const logout   = useAuthStore((s) => s.logout);
  const user     = useAuthStore((s) => s.user);

  const [dark, setDark] = useState(() => localStorage.getItem('theme') === 'dark');
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }, [dark]);

  const [tokenAlerts, setTokenAlerts]       = useState([]);
  const [alertDismissed, setAlertDismissed] = useState(false);
  useEffect(() => {
    getAccounts()
      .then((res) => {
        const now  = new Date();
        const warn = (res.data.accounts || [])
          .filter((a) => a.token_expires_at)
          .map((a) => ({ ...a, daysLeft: differenceInDays(parseISO(a.token_expires_at), now) }))
          .filter((a) => a.daysLeft <= 7)
          .sort((a, b) => a.daysLeft - b.daysLeft);
        setTokenAlerts(warn);
      })
      .catch(() => {});
  }, []);

  const handleLogout = useCallback(() => {
    logout();
    toast.success('Выход выполнен');
    navigate('/login');
  }, [logout, navigate]);

  // Mobile sidebar
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const closeSidebar = useCallback(() => setSidebarOpen(false), []);

  // Close on Escape
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') setSidebarOpen(false); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="flex min-h-screen bg-background">

      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={closeSidebar}
        />
      )}

      {/* ── SIDEBAR ─────────────────────────────────────────── */}
      <aside className={cn(
        'fixed inset-y-0 left-0 z-50 flex w-60 flex-col bg-sidebar border-r border-sidebar-border transition-transform md:translate-x-0',
        sidebarOpen ? 'translate-x-0' : '-translate-x-full',
      )}>
        <SidebarContent
          dark={dark}
          setDark={setDark}
          user={user}
          handleLogout={handleLogout}
          tokenAlerts={tokenAlerts}
          alertDismissed={alertDismissed}
          setAlertDismissed={setAlertDismissed}
          closeSidebar={closeSidebar}
        />
      </aside>

      {/* ── MAIN ────────────────────────────────────────────── */}
      <div className="flex flex-1 flex-col md:pl-60">

        {/* Mobile top bar */}
        <button
          onClick={() => setSidebarOpen(true)}
          className="md:hidden fixed top-3 left-3 z-30 rounded-lg bg-card border border-border p-2 shadow-sm"
          aria-label="Открыть меню"
        >
          <Menu className="h-5 w-5" />
        </button>

        {/* Token expiry alerts banner */}
        {!alertDismissed && tokenAlerts.length > 0 && (
          <div
            className={cn(
              'flex items-start gap-3 px-6 py-3 text-sm md:pl-60',
              tokenAlerts[0].daysLeft < 0
                ? 'bg-destructive/10 text-destructive border-b border-destructive/20'
                : 'bg-amber-50 text-amber-800 border-b border-amber-200 dark:bg-amber-900/20 dark:text-amber-300 dark:border-amber-800'
            )}
          >
            <AlertTriangle className="h-4 w-4 flex-shrink-0 mt-0.5" />
            <div className="flex-1">
              {tokenAlerts.map((a, i) => (
                <span key={a.id}>
                  {i > 0 && ', '}
                  <strong>«{a.name}»</strong>
                  {a.daysLeft < 0
                    ? ' — токен истёк! Обновите в разделе Аккаунты.'
                    : ` — токен истекает через ${a.daysLeft} ${a.daysLeft === 1 ? 'день' : a.daysLeft < 5 ? 'дня' : 'дней'}.`
                  }
                </span>
              ))}
            </div>
            <button
              onClick={() => setAlertDismissed(true)}
              className="flex-shrink-0 rounded p-0.5 opacity-60 hover:opacity-100 transition-opacity"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )}

        <main className="flex-1 p-8 pt-12 md:pt-8 animate-fade-in-up">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

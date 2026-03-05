import { Outlet, NavLink, useNavigate } from 'react-router-dom';
import { Home, Package, LogOut, PlayCircle, FileText, SlidersHorizontal, Users, Zap, BarChart2, Moon, Sun, AlertTriangle, X } from 'lucide-react';
import { useAuthStore } from '../store';
import { getAccounts } from '../api/client';
import toast from 'react-hot-toast';
import { useEffect, useState } from 'react';
import { differenceInDays, parseISO } from 'date-fns';

const navItems = [
  { to: '/',              icon: Home,              label: 'Главная' },
  { to: '/products',      icon: Package,           label: 'Товары' },
  { to: '/analytics',     icon: BarChart2,         label: 'Аналитика' },
  { to: '/runs',          icon: PlayCircle,         label: 'Прогоны' },
  { to: '/accounts',      icon: Users,             label: 'Аккаунты' },
  { to: '/import-export', icon: FileText,          label: 'Импорт/Экспорт' },
  { to: '/settings',      icon: SlidersHorizontal, label: 'Настройки' },
];

export default function Layout() {
  const navigate = useNavigate();
  const logout   = useAuthStore((s) => s.logout);

  // ── Dark mode ──────────────────────────────────────────────
  const [dark, setDark] = useState(() => localStorage.getItem('theme') !== 'light');
  useEffect(() => {
    document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('theme', dark ? 'dark' : 'light');
  }, [dark]);

  // ── Token alerts ───────────────────────────────────────────
  const [tokenAlerts, setTokenAlerts] = useState([]);
  const [alertDismissed, setAlertDismissed] = useState(false);
  useEffect(() => {
    getAccounts()
      .then((res) => {
        const now = new Date();
        const warnings = (res.data.accounts || [])
          .filter((a) => a.token_expires_at)
          .map((a) => ({ ...a, daysLeft: differenceInDays(parseISO(a.token_expires_at), now) }))
          .filter((a) => a.daysLeft <= 7)
          .sort((a, b) => a.daysLeft - b.daysLeft);
        setTokenAlerts(warnings);
      })
      .catch(() => {});
  }, []);

  const handleLogout = () => {
    logout();
    toast.success('Выход выполнен');
    navigate('/login');
  };

  const sidebarBg   = dark ? '#12121e' : '#ffffff';
  const sidebarBord = dark ? '#2a2a3e' : '#e8eaf0';
  const mainBg      = dark ? '#0a0a12' : '#f4f5f8';

  return (
    <div className="min-h-screen" style={{ background: mainBg }}>

      {/* ─── SIDEBAR ─────────────────────────────────────────────── */}
      <aside
        className="fixed inset-y-0 left-0 w-56 flex flex-col z-30"
        style={{
          background: sidebarBg,
          borderRight: `1px solid ${sidebarBord}`,
          boxShadow: '4px 0 24px rgba(0,0,0,0.04)',
        }}
      >
        {/* ── Logo ── */}
        <div
          className="px-4 py-5 flex-shrink-0"
          style={{ borderBottom: `1px solid ${sidebarBord}` }}
        >
          <div className="flex items-center gap-3">
            <div
              className="w-8 h-8 rounded-xl flex items-center justify-center flex-shrink-0 relative overflow-hidden"
              style={{
                background: 'linear-gradient(135deg, #00f0ff 0%, #0066ff 50%, #ff00d4 100%)',
                boxShadow: dark
                  ? '0 0 16px rgba(0,240,255,0.45), 0 0 32px rgba(0,240,255,0.15)'
                  : '0 4px 14px rgba(0,102,255,0.35)',
              }}
            >
              <Zap className="w-4 h-4 relative z-10" style={{ color: '#0a0a12' }} />
            </div>
            <div>
              <p
                style={{
                  fontFamily: "'Orbitron', sans-serif",
                  fontSize: '0.75rem', fontWeight: 900,
                  letterSpacing: '0.08em', lineHeight: 1.1,
                  background: 'linear-gradient(135deg, #00f0ff 0%, #0066ff 55%, #ff00d4 100%)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}
              >
                QUASAR-X
              </p>
              <p className="text-xs mt-0.5 font-medium" style={{ color: dark ? '#555577' : '#9ca3af' }}>v1.2.0</p>
            </div>
          </div>
        </div>

        {/* ── Navigation ── */}
        <nav className="flex-1 p-3 space-y-0.5 overflow-y-auto">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => isActive ? 'sidebar-link active' : 'sidebar-link'}
            >
              <item.icon className="w-[15px] h-[15px] flex-shrink-0 transition-colors duration-200" />
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>

        {/* ── Status + Dark Toggle + Logout ── */}
        <div
          className="p-3 flex-shrink-0 space-y-2"
          style={{ borderTop: `1px solid ${sidebarBord}` }}
        >
          {/* online pill */}
          <div
            className="px-3 py-2 rounded-xl flex items-center gap-2"
            style={{ background: dark ? 'rgba(22,163,74,0.12)' : '#f0fdf4', border: dark ? '1px solid rgba(22,163,74,0.25)' : '1px solid #bbf7d0' }}
          >
            <span className="glow-dot flex-shrink-0" />
            <span className="text-xs font-semibold" style={{ color: '#16a34a' }}>Система активна</span>
          </div>

          {/* Dark mode toggle */}
          <button
            onClick={() => setDark((d) => !d)}
            className="sidebar-link w-full group"
          >
            {dark
              ? <Sun className="w-[15px] h-[15px] flex-shrink-0 text-yellow-400" />
              : <Moon className="w-[15px] h-[15px] flex-shrink-0" />
            }
            <span>{dark ? 'Светлая тема' : 'Тёмная тема'}</span>
          </button>

          <button onClick={handleLogout} className="sidebar-link w-full group">
            <LogOut className="w-[15px] h-[15px] flex-shrink-0 transition-transform duration-200 group-hover:-translate-x-0.5" />
            <span>Выход</span>
          </button>
        </div>
      </aside>

      {/* ─── MAIN ────────────────────────────────────────────────── */}
      <div className="ml-56 min-h-screen">

        {/* Token expiry alerts */}
        {!alertDismissed && tokenAlerts.length > 0 && (
          <div
            className="flex items-start gap-3 px-6 py-3"
            style={{
              background: tokenAlerts[0].daysLeft < 0 ? '#fef2f2' : '#fff7ed',
              borderBottom: tokenAlerts[0].daysLeft < 0 ? '1px solid #fecaca' : '1px solid #fed7aa',
            }}
          >
            <AlertTriangle
              className="w-4 h-4 flex-shrink-0 mt-0.5"
              style={{ color: tokenAlerts[0].daysLeft < 0 ? '#ef4444' : '#f97316' }}
            />
            <div className="flex-1 text-sm" style={{ color: tokenAlerts[0].daysLeft < 0 ? '#991b1b' : '#9a3412' }}>
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
              className="flex-shrink-0 p-0.5 rounded opacity-60 hover:opacity-100"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        <div className="p-8 animate-fade-in-up">
          <Outlet />
        </div>
      </div>
    </div>
  );
}

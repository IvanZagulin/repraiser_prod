import { formatMsk } from '../utils/date';
import { Bell } from 'lucide-react';
import { useEffect, useState, useRef } from 'react';
import { getAlertCount, getAlerts, markAlertRead, markAllAlertsRead } from '../api/client';

export default function AlertsBell() {
  const [unreadCount, setUnreadCount] = useState(0);
  const [alerts, setAlerts] = useState([]);
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const ref = useRef(null);

  // Poll unread count every 60s
  const fetchCount = () => {
    getAlertCount()
      .then((res) => setUnreadCount(res.data.count ?? 0))
      .catch(() => {});
  };

  useEffect(() => {
    fetchCount();
    const interval = setInterval(fetchCount, 60000);
    return () => clearInterval(interval);
  }, []);

  // Close dropdown on outside click
  useEffect(() => {
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, []);

  const handleOpen = () => {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setLoading(true);
    getAlerts({ limit: 20 })
      .then((res) => setAlerts(res.data.alerts ?? res.data ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  const handleMarkRead = (id) => {
    markAlertRead(id)
      .then(() => {
        setAlerts((prev) => prev.map((a) => (a.id === id ? { ...a, is_read: true } : a)));
        setUnreadCount((c) => Math.max(0, c - 1));
      })
      .catch(() => {});
  };

  const handleMarkAllRead = () => {
    markAllAlertsRead()
      .then(() => {
        setAlerts((prev) => prev.map((a) => ({ ...a, is_read: true })));
        setUnreadCount(0);
      })
      .catch(() => {});
  };


  const formatChange = (oldPrice, newPrice) => {
    if (!oldPrice || !newPrice) return '';
    const pct = (((newPrice - oldPrice) / oldPrice) * 100).toFixed(1);
    const sign = pct > 0 ? '+' : '';
    return `${sign}${pct}%`;
  };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      {/* Bell button */}
      <button
        onClick={handleOpen}
        style={{
          position: 'relative',
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          padding: 6,
          borderRadius: 8,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
        }}
        title="Уведомления"
      >
        <Bell
          style={{ width: 20, height: 20, color: 'var(--text-2)' }}
        />
        {unreadCount > 0 && (
          <span
            style={{
              position: 'absolute',
              top: 2,
              right: 2,
              minWidth: 16,
              height: 16,
              borderRadius: '50%',
              background: '#ef4444',
              color: '#fff',
              fontSize: 10,
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '0 4px',
              lineHeight: 1,
            }}
          >
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown */}
      {open && (
        <div
          style={{
            position: 'absolute',
            top: '100%',
            left: 0,
            marginTop: 8,
            width: 360,
            maxHeight: 420,
            overflowY: 'auto',
            background: 'var(--card, #1a1a2e)',
            border: '1px solid var(--border, #2a2a3e)',
            borderRadius: 12,
            boxShadow: '0 8px 32px rgba(0,0,0,0.25)',
            zIndex: 50,
          }}
        >
          {/* Header */}
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '12px 16px',
              borderBottom: '1px solid var(--border, #2a2a3e)',
            }}
          >
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: 'var(--text, var(--foreground))',
              }}
            >
              Уведомления
            </span>
            {unreadCount > 0 && (
              <button
                onClick={handleMarkAllRead}
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 11,
                  fontWeight: 500,
                  color: '#7c3aed',
                  padding: '2px 6px',
                  borderRadius: 6,
                }}
              >
                Прочитать все
              </button>
            )}
          </div>

          {/* Body */}
          {loading ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>
              Загрузка...
            </div>
          ) : alerts.length === 0 ? (
            <div style={{ padding: 24, textAlign: 'center', color: 'var(--text-3)', fontSize: 12 }}>
              Нет уведомлений
            </div>
          ) : (
            alerts.map((alert) => {
              const changeColor =
                alert.new_price > alert.old_price ? '#ef4444' : '#22c55e';
              return (
                <div
                  key={alert.id}
                  onClick={() => !alert.is_read && handleMarkRead(alert.id)}
                  style={{
                    padding: '10px 16px',
                    cursor: alert.is_read ? 'default' : 'pointer',
                    borderBottom: '1px solid var(--border, #2a2a3e)',
                    background: alert.is_read
                      ? 'transparent'
                      : 'rgba(124, 58, 237, 0.06)',
                    transition: 'background 0.15s',
                  }}
                  onMouseEnter={(e) => {
                    if (!alert.is_read)
                      e.currentTarget.style.background = 'rgba(124, 58, 237, 0.12)';
                  }}
                  onMouseLeave={(e) => {
                    e.currentTarget.style.background = alert.is_read
                      ? 'transparent'
                      : 'rgba(124, 58, 237, 0.06)';
                  }}
                >
                  <div
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'space-between',
                      marginBottom: 4,
                    }}
                  >
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)' }}>
                      Арт. WB: {alert.competitor_nm_id}
                    </span>
                    <span style={{ fontSize: 10, color: 'var(--text-3)' }}>
                      {formatMsk(alert.created_at, 'dd.MM HH:mm')}
                    </span>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                    <span style={{ fontSize: 12, color: 'var(--text-3)' }}>
                      {alert.old_price} ₽
                    </span>
                    <span style={{ fontSize: 11, color: 'var(--text-3)' }}>→</span>
                    <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--text-2)' }}>
                      {alert.new_price} ₽
                    </span>
                    <span
                      style={{
                        fontSize: 11,
                        fontWeight: 600,
                        color: changeColor,
                        marginLeft: 'auto',
                      }}
                    >
                      {formatChange(alert.old_price, alert.new_price)}
                    </span>
                    {!alert.is_read && (
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: '50%',
                          background: '#7c3aed',
                          flexShrink: 0,
                        }}
                      />
                    )}
                  </div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

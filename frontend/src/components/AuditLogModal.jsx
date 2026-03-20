import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { getProductAuditLog } from '../api/client';
import { formatMsk } from '../utils/date';

export default function AuditLogModal({ productId, productName, onClose }) {
  const [entries, setEntries] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getProductAuditLog(productId)
      .then((res) => setEntries(res.data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [productId]);

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div
        className="modal-box"
        style={{ maxWidth: 560, width: '95%', padding: '1.5rem' }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-start justify-between mb-5">
          <div>
            <h2 className="text-base font-semibold" style={{ color: 'var(--text)' }}>
              Журнал изменений
            </h2>
            {productName && (
              <p className="text-sm mt-0.5 font-medium" style={{ color: '#7c3aed' }}>
                {productName}
              </p>
            )}
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1.5">
            <X className="w-4 h-4" />
          </button>
        </div>

        {loading ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <div className="spinner" />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Загрузка журнала...</p>
          </div>
        ) : entries.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-64 gap-3">
            <X className="w-10 h-10 opacity-10" style={{ color: 'var(--text)' }} />
            <p className="text-sm" style={{ color: 'var(--text-3)' }}>Нет записей об изменениях</p>
            <p className="text-xs" style={{ color: 'var(--text-3)' }}>
              Изменения настроек товара будут отображаться здесь
            </p>
          </div>
        ) : (
          <div
            style={{
              maxHeight: 420,
              overflowY: 'auto',
              paddingRight: 4,
            }}
          >
            {entries.map((entry, i) => (
              <div
                key={i}
                className="flex gap-3"
                style={{ marginBottom: i < entries.length - 1 ? 0 : undefined }}
              >
                {/* Timeline bar */}
                <div className="flex flex-col items-center" style={{ width: 20 }}>
                  <div
                    style={{
                      width: 10,
                      height: 10,
                      borderRadius: '50%',
                      background: '#7c3aed',
                      flexShrink: 0,
                      marginTop: 4,
                    }}
                  />
                  {i < entries.length - 1 && (
                    <div style={{ width: 2, flex: 1, background: '#e8eaf0' }} />
                  )}
                </div>

                {/* Content */}
                <div style={{ paddingBottom: 16, flex: 1 }}>
                  <p className="text-sm font-medium" style={{ color: 'var(--text-2)' }}>
                    {entry.field_name}
                  </p>
                  <div className="flex items-center gap-1.5 mt-1 text-sm">
                    <span
                      style={{
                        color: 'var(--text-3)',
                        textDecoration: 'line-through',
                        opacity: 0.7,
                      }}
                    >
                      {entry.old_value ?? '—'}
                    </span>
                    <span style={{ color: 'var(--text-3)' }}>&rarr;</span>
                    <span style={{ color: '#7c3aed', fontWeight: 600 }}>
                      {entry.new_value ?? '—'}
                    </span>
                  </div>
                  <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                    {formatMsk(entry.changed_at, 'dd.MM.yyyy HH:mm')}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

import { useEffect, useState } from 'react';
import { Plus, Trash2, AlertCircle, X, KeyRound } from 'lucide-react';
import toast from 'react-hot-toast';
import { getAccounts, createAccount, deleteAccount } from '../api/client';
import { format } from 'date-fns';

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);

  useEffect(() => {
    loadAccounts();
  }, []);

  const loadAccounts = async () => {
    setLoading(true);
    try {
      const response = await getAccounts();
      setAccounts(response.data.accounts);
    } catch {
      toast.error('Ошибка загрузки аккаунтов');
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (id, name) => {
    if (!confirm(`Удалить аккаунт "${name}"? Все товары тоже будут удалены!`)) return;
    try {
      await deleteAccount(id);
      setAccounts(accounts.filter((a) => a.id !== id));
      toast.success('Аккаунт удален');
    } catch {
      toast.error('Ошибка удаления аккаунта');
    }
  };

  const getTokenStatus = (account) => {
    if (!account.token_ttl_days) return <span className="badge badge-warning">Неизвестно</span>;
    if (account.token_ttl_days <= 0) return <span className="badge badge-error">Истек</span>;
    if (account.token_ttl_days <= 7) return <span className="badge badge-warning">{account.token_ttl_days} дней</span>;
    return <span className="badge badge-success">{account.token_ttl_days} дней</span>;
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-2xl font-bold" style={{ background: 'linear-gradient(135deg,#1e1b4b,#7c3aed)', WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent', backgroundClip:'text' }}>Аккаунты WB</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-3)' }}>Управление аккаунтами продавцов</p>
        </div>
        <button onClick={() => setShowModal(true)} className="btn btn-primary gap-2">
          <Plus className="w-4 h-4" />
          Добавить аккаунт
        </button>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="spinner" />
        </div>
      ) : accounts.length === 0 ? (
        <div className="card text-center py-16">
          <p style={{ color: 'var(--text-3)' }}>Аккаунты не найдены</p>
          <button onClick={() => setShowModal(true)} className="btn btn-primary mt-4">
            Добавить первый аккаунт
          </button>
        </div>
      ) : (
        <div className="grid gap-4">
          {accounts.map((account) => (
            <div key={account.id} className="card">
              <div className="flex items-start justify-between">
                <div className="flex-1">
                  <div className="flex items-center gap-3">
                    <div
                      className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
                      style={{ background: '#ede9fe' }}
                    >
                      <KeyRound className="w-4 h-4" style={{ color: '#7c3aed' }} />
                    </div>
                    <div>
                      <h3 className="font-semibold text-gray-900">{account.name}</h3>
                      <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
                        Создан {format(new Date(account.created_at), 'dd.MM.yyyy')}
                      </p>
                    </div>
                    {account.is_active
                      ? <span className="badge badge-success">Активен</span>
                      : <span className="badge">Отключен</span>}
                  </div>

                  <div className="mt-4 flex items-center gap-6 pl-12">
                    <div>
                      <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>ID аккаунта</p>
                      <span
                        className="font-mono text-sm font-semibold px-2 py-0.5 rounded"
                        style={{ background: '#ede9fe', color: '#7c3aed' }}
                        title="Используйте этот ID при загрузке товаров через Excel"
                      >
                        {account.id}
                      </span>
                    </div>
                    <div>
                      <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Срок токена</p>
                      {getTokenStatus(account)}
                    </div>
                  </div>

                  {account.token_ttl_days !== null && account.token_ttl_days <= 7 && (
                    <div
                      className="mt-3 ml-12 flex items-start gap-2 p-3 rounded-lg"
                      style={{ background: '#fefce8', border: '1px solid #fde68a' }}
                    >
                      <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5" style={{ color: '#d97706' }} />
                      <p className="text-sm" style={{ color: '#92400e' }}>
                        <strong>Внимание!</strong> Токен скоро истечет. Обновите его в настройках аккаунта WB.
                      </p>
                    </div>
                  )}
                </div>

                <button
                  onClick={() => handleDelete(account.id, account.name)}
                  className="btn btn-sm btn-danger ml-4"
                  title="Удалить"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {showModal && (
        <AccountModal onClose={() => { setShowModal(false); loadAccounts(); }} />
      )}
    </div>
  );
}

function AccountModal({ onClose }) {
  const [formData, setFormData] = useState({ name: '', wb_api_token: '' });
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await createAccount(formData);
      toast.success('Аккаунт создан');
      onClose();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Ошибка создания аккаунта');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-box max-w-md">
        <div className="modal-header">
          <h2 className="text-base font-semibold text-gray-900">Добавить аккаунт</h2>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
              Название аккаунта
            </label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              className="input"
              placeholder="Основной магазин"
              required
            />
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
              WB API Токен (JWT)
            </label>
            <textarea
              value={formData.wb_api_token}
              onChange={(e) => setFormData({ ...formData, wb_api_token: e.target.value })}
              className="input font-mono text-xs"
              rows="4"
              placeholder="eyJ..."
              required
            />
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
              Токен из личного кабинета WB → Настройки → Доступ к API
            </p>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn btn-secondary">
              Отмена
            </button>
            <button type="submit" disabled={loading} className="btn btn-primary">
              {loading ? 'Создание...' : 'Создать'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

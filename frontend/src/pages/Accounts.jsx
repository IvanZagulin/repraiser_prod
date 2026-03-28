import { useEffect, useState } from 'react';
import { Plus, Trash2, AlertCircle, X, KeyRound, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { getAccounts, createAccount, deleteAccount, syncAccountCatalog, syncAccountProducts } from '../api/client';
import { formatMsk } from '../utils/date';

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [syncingId, setSyncingId] = useState(null);
  const [syncingProductsId, setSyncingProductsId] = useState(null);

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

  const handleSyncCatalog = async (id, name) => {
    setSyncingId(id);
    try {
      await syncAccountCatalog(id);
      toast.success(`Синхронизация каталога "${name}" запущена — фото обновятся через 1–3 мин.`);
    } catch {
      toast.error('Ошибка запуска синхронизации');
    } finally {
      setSyncingId(null);
    }
  };

  const handleSyncProducts = async (id, name) => {
    setSyncingProductsId(id);
    try {
      await syncAccountProducts(id);
      toast.success(`Синхронизация товаров "${name}" запущена — новые товары появятся через 1–2 мин.`);
    } catch {
      toast.error('Ошибка запуска синхронизации товаров');
    } finally {
      setSyncingProductsId(null);
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
          <h1 className="page-h1">Аккаунты WB</h1>
          <p className="text-sm mt-1 text-muted-foreground">Управление аккаунтами продавцов</p>
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
                    <div className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 bg-primary/10">
                      <KeyRound className="w-4 h-4 text-primary" />
                    </div>
                    <div>
                      <h3 className="font-semibold text-foreground">{account.name}</h3>
                      <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
                        Создан {formatMsk(account.created_at, 'dd.MM.yyyy')}
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
                        className="font-mono text-sm font-semibold px-2 py-0.5 rounded bg-primary/10 text-primary"
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
                    <div className="mt-3 ml-12 flex items-start gap-2 p-3 rounded-lg bg-amber-50 border border-amber-200 dark:bg-amber-950/20 dark:border-amber-800">
                      <AlertCircle className="w-4 h-4 flex-shrink-0 mt-0.5 text-amber-600" />
                      <p className="text-sm text-amber-800 dark:text-amber-300">
                        <strong>Внимание!</strong> Токен скоро истечет. Обновите его в настройках аккаунта WB.
                      </p>
                    </div>
                  )}
                </div>

                <div className="flex items-center gap-2 ml-4">
                  <button
                    onClick={() => handleSyncProducts(account.id, account.name)}
                    disabled={syncingProductsId === account.id}
                    className="btn btn-sm btn-secondary"
                    title="Синхронизировать товары из каталога"
                  >
                    <RefreshCw className={`w-4 h-4 ${syncingProductsId === account.id ? 'animate-spin' : ''}`} />
                    <span className="hidden sm:inline text-xs">Товары</span>
                  </button>
                  <button
                    onClick={() => handleSyncCatalog(account.id, account.name)}
                    disabled={syncingId === account.id}
                    className="btn btn-sm btn-secondary"
                    title="Синхронизировать каталог (обновить фото товаров)"
                  >
                    <RefreshCw className={`w-4 h-4 ${syncingId === account.id ? 'animate-spin' : ''}`} />
                  </button>
                  <button
                    onClick={() => handleDelete(account.id, account.name)}
                    className="btn btn-sm btn-danger"
                    title="Удалить"
                  >
                    <Trash2 className="w-4 h-4" />
                  </button>
                </div>
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
          <h2 className="text-base font-semibold text-foreground">Добавить аккаунт</h2>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
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
            <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
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

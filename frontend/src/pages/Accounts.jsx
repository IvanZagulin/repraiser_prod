import { useEffect, useState } from 'react';
import { Plus, Trash2, AlertCircle, X, KeyRound, RefreshCw, ShieldCheck, Check } from 'lucide-react';
import toast from 'react-hot-toast';
import {
  getAccounts, createAccount, updateAccount, deleteAccount,
  syncAccountCatalog, syncAccountProducts, checkAccountCredentials,
} from '../api/client';
import { formatMsk } from '../utils/date';

/**
 * Срок действия WB-токена читаем прямо из вставленного JWT —
 * так пользователь видит дату ещё до сохранения и не вставит просроченный ключ.
 */
function readJwtExpiry(token) {
  try {
    const payload = token.trim().split('.')[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const exp = JSON.parse(json)?.exp;
    if (!exp) return null;
    const date = new Date(exp * 1000);
    const days = Math.floor((date - Date.now()) / 86400000);
    return { date, days };
  } catch {
    return null;
  }
}

function tokenBadge(ttl) {
  if (ttl === null || ttl === undefined) return <span className="badge badge-warning">Неизвестно</span>;
  if (ttl <= 0) return <span className="badge badge-error">Истёк</span>;
  if (ttl <= 7) return <span className="badge badge-warning">{ttl} дн.</span>;
  return <span className="badge badge-success">{ttl} дн.</span>;
}

export default function Accounts() {
  const [accounts, setAccounts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editAccount, setEditAccount] = useState(null);
  const [syncingId, setSyncingId] = useState(null);
  const [syncingProductsId, setSyncingProductsId] = useState(null);
  const [checkingId, setCheckingId] = useState(null);

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

  const handleCheck = async (id) => {
    setCheckingId(id);
    try {
      const { data } = await checkAccountCredentials(id);
      if (data.ok) toast.success(data.message);
      else toast.error(data.message, { duration: 8000 });
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Не удалось проверить ключ');
    } finally {
      setCheckingId(null);
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

  // Аккаунты, где ключ пора менять — выносим наверх, чтобы это нельзя было пропустить.
  const expiring = accounts.filter(
    (a) => a.marketplace === 'wb' && a.token_ttl_days !== null && a.token_ttl_days <= 7
  );

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-h1">Аккаунты и API-ключи</h1>
          <p className="text-sm mt-1 text-muted-foreground">
            Здесь же меняется API-ключ каждого аккаунта — кнопка «Сменить ключ» на карточке
          </p>
        </div>
        <button onClick={() => setShowModal(true)} className="btn btn-primary gap-2">
          <Plus className="w-4 h-4" />
          Добавить аккаунт
        </button>
      </div>

      {expiring.length > 0 && (
        <div className="card mb-4 border-amber-300 bg-amber-50 dark:bg-amber-950/20 dark:border-amber-800">
          <div className="flex items-start gap-3">
            <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5 text-amber-600" />
            <div className="flex-1">
              <p className="text-sm font-semibold text-amber-900 dark:text-amber-200">
                Пора сменить API-ключ
              </p>
              <p className="text-sm mt-1 text-amber-800 dark:text-amber-300">
                {expiring.map((a) => (
                  <span key={a.id} className="inline-flex items-center gap-1.5 mr-3">
                    <strong>{a.name}</strong>
                    {a.token_ttl_days <= 0 ? '— ключ истёк' : `— осталось ${a.token_ttl_days} дн.`}
                    <button
                      onClick={() => setEditAccount(a)}
                      className="underline underline-offset-2 hover:no-underline"
                    >
                      сменить
                    </button>
                  </span>
                ))}
              </p>
            </div>
          </div>
        </div>
      )}

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
                    <span className="badge">{account.marketplace === 'ozon' ? 'Ozon' : 'WB'}</span>
                  </div>

                  <div className="mt-4 flex flex-wrap items-start gap-x-8 gap-y-3 pl-12">
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
                      <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>
                        {account.marketplace === 'ozon' ? 'Ozon Api-Key' : 'WB API-ключ'}
                      </p>
                      <span className="font-mono text-sm text-foreground">
                        {account.marketplace === 'ozon'
                          ? (account.ozon_api_key_masked || '— не задан —')
                          : (account.wb_token_masked || '— не задан —')}
                      </span>
                    </div>

                    {account.marketplace === 'ozon' ? (
                      <div>
                        <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Client-Id</p>
                        <span className="font-mono text-sm text-foreground">
                          {account.ozon_client_id || '—'}
                        </span>
                      </div>
                    ) : (
                      <div>
                        <p className="text-xs mb-1" style={{ color: 'var(--text-3)' }}>Срок ключа</p>
                        {tokenBadge(account.token_ttl_days)}
                        {account.token_expires_at && (
                          <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                            до {formatMsk(account.token_expires_at, 'dd.MM.yyyy')}
                          </p>
                        )}
                      </div>
                    )}
                  </div>

                  <div className="mt-4 ml-12 flex flex-wrap items-center gap-2">
                    <button
                      onClick={() => setEditAccount(account)}
                      className="btn btn-sm btn-primary gap-1.5"
                    >
                      <KeyRound className="w-4 h-4" />
                      Сменить ключ
                    </button>
                    <button
                      onClick={() => handleCheck(account.id)}
                      disabled={checkingId === account.id || !account.has_credentials}
                      className="btn btn-sm btn-secondary gap-1.5"
                      title="Проверить ключ живым запросом к маркетплейсу"
                    >
                      {checkingId === account.id
                        ? <RefreshCw className="w-4 h-4 animate-spin" />
                        : <ShieldCheck className="w-4 h-4" />}
                      Проверить ключ
                    </button>
                  </div>
                </div>

                <div className="flex items-center gap-2 ml-4">
                  {account.marketplace !== 'ozon' && (
                    <>
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
                    </>
                  )}
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
      {editAccount && (
        <EditAccountModal
          account={editAccount}
          onClose={() => { setEditAccount(null); loadAccounts(); }}
        />
      )}
    </div>
  );
}

function EditAccountModal({ account, onClose }) {
  const [formData, setFormData] = useState({
    name: account.name,
    marketplace: account.marketplace || 'wb',
    wb_api_token: '',
    ozon_client_id: '',
    ozon_api_key: '',
    is_active: account.is_active,
  });
  const [loading, setLoading] = useState(false);
  const isWb = formData.marketplace !== 'ozon';

  const newTokenInfo = isWb && formData.wb_api_token.trim()
    ? readJwtExpiry(formData.wb_api_token)
    : null;
  const newTokenLooksWrong = isWb
    && formData.wb_api_token.trim().length > 0
    && newTokenInfo === null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      // Отправляем только то, что реально поменяли: маркетплейс аккаунта здесь
      // не меняется, а пустые поля ключей означают «оставить прежний».
      const payload = {};
      if (formData.name !== account.name) payload.name = formData.name;
      if (formData.is_active !== account.is_active) payload.is_active = formData.is_active;
      if (formData.marketplace !== account.marketplace) payload.marketplace = formData.marketplace;
      if (isWb && formData.wb_api_token.trim()) {
        payload.wb_api_token = formData.wb_api_token.trim();
      }
      if (!isWb) {
        if (formData.ozon_client_id.trim()) payload.ozon_client_id = formData.ozon_client_id.trim();
        if (formData.ozon_api_key.trim()) payload.ozon_api_key = formData.ozon_api_key.trim();
      }
      if (Object.keys(payload).length === 0) {
        toast('Нечего сохранять — ничего не изменилось');
        setLoading(false);
        return;
      }

      const keyChanged = Boolean(payload.wb_api_token || payload.ozon_api_key);
      await updateAccount(account.id, payload);
      toast.success(
        keyChanged
          ? 'Ключ сохранён — каталог синхронизируется с новым ключом'
          : 'Аккаунт обновлён'
      );
      onClose();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Ошибка обновления аккаунта');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-box max-w-md">
        <div className="modal-header">
          <h2 className="text-base font-semibold text-foreground">
            {account.name} — API-ключ
          </h2>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div className="rounded-lg border border-border p-3 text-sm">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">Текущий ключ</span>
              <span className="font-mono text-foreground">
                {isWb
                  ? (account.wb_token_masked || '— не задан —')
                  : (account.ozon_api_key_masked || '— не задан —')}
              </span>
            </div>
            {isWb && (
              <div className="flex items-center justify-between mt-2">
                <span className="text-muted-foreground">Действует</span>
                <span>{tokenBadge(account.token_ttl_days)}</span>
              </div>
            )}
          </div>

          {isWb ? (
            <div>
              <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                Новый WB API-ключ (JWT)
              </label>
              <textarea
                value={formData.wb_api_token}
                onChange={(e) => setFormData({ ...formData, wb_api_token: e.target.value })}
                className="input font-mono text-xs"
                rows="4"
                placeholder="eyJ... — вставьте новый ключ, чтобы заменить текущий"
                autoFocus
              />
              <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                Личный кабинет WB → Настройки → Доступ к API. Нужны категории «Контент»
                и «Цены и скидки». Оставьте поле пустым, чтобы ключ не менять.
              </p>
              {newTokenInfo && (
                <p className={`text-xs mt-2 flex items-center gap-1.5 ${
                  newTokenInfo.days <= 0 ? 'text-red-600' : 'text-emerald-600'
                }`}>
                  <Check className="w-3.5 h-3.5" />
                  {newTokenInfo.days <= 0
                    ? 'Этот ключ уже просрочен — возьмите в WB новый'
                    : `Ключ действует до ${newTokenInfo.date.toLocaleDateString('ru-RU')} (${newTokenInfo.days} дн.)`}
                </p>
              )}
              {newTokenLooksWrong && (
                <p className="text-xs mt-2 flex items-center gap-1.5 text-amber-600">
                  <AlertCircle className="w-3.5 h-3.5" />
                  Не похоже на ключ WB — скопируйте его целиком, без пробелов и переносов
                </p>
              )}
            </div>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                  Новый Ozon Client-Id
                </label>
                <input
                  value={formData.ozon_client_id}
                  onChange={(e) => setFormData({ ...formData, ozon_client_id: e.target.value })}
                  className="input font-mono text-xs"
                  placeholder={account.ozon_client_id || 'Оставьте пустым, чтобы не менять'}
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                  Новый Ozon Api-Key
                </label>
                <textarea
                  value={formData.ozon_api_key}
                  onChange={(e) => setFormData({ ...formData, ozon_api_key: e.target.value })}
                  className="input font-mono text-xs"
                  rows="3"
                  placeholder="Оставьте пустым, чтобы не менять"
                />
                <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                  Личный кабинет Ozon → Настройки → Seller API.
                </p>
              </div>
            </>
          )}

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
              Маркетплейс
            </label>
            <select
              value={formData.marketplace}
              onChange={(e) => setFormData({ ...formData, marketplace: e.target.value })}
              className="input"
            >
              <option value="wb">Wildberries</option>
              <option value="ozon">Ozon</option>
            </select>
            {formData.marketplace !== account.marketplace && (
              <p className="text-xs mt-1 text-amber-600">
                Смена маркетплейса — введите ключ новой площадки, иначе сохранить не получится.
              </p>
            )}
          </div>

          <div className="flex items-center gap-3">
            <input
              type="checkbox"
              id="is_active"
              checked={formData.is_active}
              onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              className="w-4 h-4"
            />
            <label htmlFor="is_active" className="text-sm font-medium text-muted-foreground cursor-pointer">
              Аккаунт активен
            </label>
          </div>

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn btn-secondary">
              Отмена
            </button>
            <button type="submit" disabled={loading} className="btn btn-primary">
              {loading ? 'Сохранение...' : 'Сохранить ключ'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

function AccountModal({ onClose }) {
  const [formData, setFormData] = useState({
    name: '',
    marketplace: 'wb',
    wb_api_token: '',
    ozon_client_id: '',
    ozon_api_key: '',
  });
  const [loading, setLoading] = useState(false);

  const newTokenInfo = formData.marketplace === 'wb' && formData.wb_api_token.trim()
    ? readJwtExpiry(formData.wb_api_token)
    : null;

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const payload = { name: formData.name, marketplace: formData.marketplace };
      if (formData.marketplace === 'wb') {
        payload.wb_api_token = formData.wb_api_token.trim();
      } else {
        payload.ozon_client_id = formData.ozon_client_id.trim();
        payload.ozon_api_key = formData.ozon_api_key.trim();
      }
      await createAccount(payload);
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
              Маркетплейс
            </label>
            <select
              value={formData.marketplace}
              onChange={(e) => setFormData({ ...formData, marketplace: e.target.value })}
              className="input"
              required
            >
              <option value="wb">Wildberries</option>
              <option value="ozon">Ozon</option>
            </select>
          </div>

          {formData.marketplace === 'wb' ? (
            <div>
              <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                WB API-ключ (JWT)
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
                Личный кабинет WB → Настройки → Доступ к API. Нужны категории «Контент»
                и «Цены и скидки».
              </p>
              {newTokenInfo && (
                <p className={`text-xs mt-2 flex items-center gap-1.5 ${
                  newTokenInfo.days <= 0 ? 'text-red-600' : 'text-emerald-600'
                }`}>
                  <Check className="w-3.5 h-3.5" />
                  {newTokenInfo.days <= 0
                    ? 'Этот ключ уже просрочен — возьмите в WB новый'
                    : `Ключ действует до ${newTokenInfo.date.toLocaleDateString('ru-RU')} (${newTokenInfo.days} дн.)`}
                </p>
              )}
            </div>
          ) : (
            <>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                  Ozon Client-Id
                </label>
                <input
                  value={formData.ozon_client_id}
                  onChange={(e) => setFormData({ ...formData, ozon_client_id: e.target.value })}
                  className="input font-mono text-xs"
                  required
                />
              </div>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">
                  Ozon Api-Key
                </label>
                <textarea
                  value={formData.ozon_api_key}
                  onChange={(e) => setFormData({ ...formData, ozon_api_key: e.target.value })}
                  className="input font-mono text-xs"
                  rows="3"
                  required
                />
                <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                  Личный кабинет Ozon → Настройки → Seller API.
                </p>
              </div>
            </>
          )}

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

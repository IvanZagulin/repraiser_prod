import { useState, useEffect } from 'react';
import { X, Search, Loader2, CheckCircle } from 'lucide-react';
import toast from 'react-hot-toast';
import { createProduct, getAccounts, searchWBProduct } from '../api/client';

export default function ProductModal({ onClose }) {
  const [accounts, setAccounts] = useState([]);
  const [accountId, setAccountId] = useState('');
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [found, setFound] = useState(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadAccounts();
  }, []);

  const loadAccounts = async () => {
    try {
      const response = await getAccounts();
      const accs = response.data.accounts;
      setAccounts(accs);
      if (accs.length > 0) setAccountId(accs[0].id);
    } catch {
      toast.error('Ошибка загрузки аккаунтов');
    }
  };

  const handleSearch = async () => {
    if (!accountId) { toast.error('Выберите аккаунт'); return; }
    if (!searchText.trim()) { toast.error('Введите артикул WB или артикул продавца'); return; }
    setSearching(true);
    setFound(null);
    try {
      const res = await searchWBProduct(accountId, searchText.trim());
      setFound(res.data);
      toast.success('Товар найден');
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Товар не найден');
    } finally {
      setSearching(false);
    }
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') { e.preventDefault(); handleSearch(); }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!found) { toast.error('Сначала найдите товар'); return; }
    setLoading(true);
    try {
      await createProduct({
        account_id: parseInt(accountId),
        nm_id: found.nm_id,
        seller_sku: found.seller_sku,
        name: found.name,
      });
      toast.success('Товар добавлен');
      onClose();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Ошибка создания товара');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-box max-w-md">
        <div className="modal-header">
          <h2 className="text-base font-semibold text-foreground">Добавить товар</h2>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
              Аккаунт
            </label>
            <select
              value={accountId}
              onChange={(e) => { setAccountId(e.target.value); setFound(null); }}
              className="input"
              required
            >
              <option value="">Выберите аккаунт</option>
              {accounts.map((acc) => (
                <option key={acc.id} value={acc.id}>{acc.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
              Артикул WB или артикул продавца
            </label>
            <div className="flex gap-2">
              <input
                type="text"
                value={searchText}
                onChange={(e) => { setSearchText(e.target.value); setFound(null); }}
                onKeyDown={handleKeyDown}
                className="input flex-1"
                placeholder="12345678 или SKU-001"
              />
              <button
                type="button"
                onClick={handleSearch}
                disabled={searching}
                className="btn btn-secondary gap-1 whitespace-nowrap"
              >
                {searching ? <Loader2 className="w-4 h-4 animate-spin" /> : <Search className="w-4 h-4" />}
                Найти
              </button>
            </div>
            <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
              nmID (артикул WB) или vendorCode (артикул продавца)
            </p>
          </div>

          {found && (
            <div
              className="rounded-lg p-4 space-y-1.5"
              style={{ background: '#f0fdf4', border: '1px solid #bbf7d0' }}
            >
              <div className="flex items-center gap-2 text-sm font-medium mb-2" style={{ color: '#16a34a' }}>
                <CheckCircle className="w-4 h-4" />
                Товар найден
              </div>
              <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                <span style={{ color: 'var(--text-2)' }}>Артикул WB:</span> {found.nm_id}
              </p>
              <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                <span style={{ color: 'var(--text-2)' }}>Артикул продавца:</span> {found.seller_sku}
              </p>
              <p className="text-sm" style={{ color: 'var(--text-2)' }}>
                <span style={{ color: 'var(--text-2)' }}>Название:</span> {found.name}
              </p>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button type="button" onClick={onClose} className="btn btn-secondary">
              Отмена
            </button>
            <button type="submit" disabled={loading || !found} className="btn btn-primary">
              {loading ? 'Создание...' : 'Добавить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

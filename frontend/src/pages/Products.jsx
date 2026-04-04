import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Plus, Settings, Trash2, Play, Search, LayoutGrid, List, LineChart, ChevronLeft, ChevronRight, RefreshCw } from 'lucide-react';
import toast from 'react-hot-toast';
import { getProducts, deleteProduct, runRepricer, getSettings, fetchPrices, bulkActivate, bulkDelete, bulkStrategy, getAccounts } from '../api/client';
import { useProductsStore } from '../store';
import ProductModal from '../components/ProductModal';
import ConfigModal from '../components/ConfigModal';
import PriceHistoryModal from '../components/PriceHistoryModal';

const PAGE_SIZE = 50;

// Delete button is shown directly on card (no hover needed)
const getWBImageUrl = (nmId) => {
  const nm = parseInt(nmId);
  if (!nm || isNaN(nm)) return null;
  const vol = Math.floor(nm / 100000);
  const part = Math.floor(nm / 1000);
  const baskets = [
    [143,'01'],[287,'02'],[431,'03'],[719,'04'],[1007,'05'],
    [1061,'06'],[1115,'07'],[1169,'08'],[1313,'09'],[1601,'10'],
    [1655,'11'],[1919,'12'],[2045,'13'],[2189,'14'],[2405,'15'],
    [2621,'16'],[2837,'17'],[3053,'18'],[3261,'19'],[3473,'20'],
    [3721,'21'],[4001,'22'],[4281,'23'],[4601,'24'],[5001,'25'],
  ];
  const basket = (baskets.find(([v]) => vol <= v) ?? [0,'26'])[1];
  return `https://basket-${basket}.wbbasket.ru/vol${vol}/part${part}/${nm}/images/c246x328/1.webp`;
};

export default function Products() {
  const navigate = useNavigate();
  const { products, setProducts, loading, setLoading, removeProduct } = useProductsStore();
  const [showProductModal, setShowProductModal] = useState(false);
  const [showConfigModal, setShowConfigModal] = useState(false);
  const [showHistoryModal, setShowHistoryModal] = useState(false);
  const [selectedProduct, setSelectedProduct] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [filterStrategy, setFilterStrategy] = useState('all');
  const [walletPct, setWalletPct] = useState(3.0);
  const [viewMode, setViewMode] = useState('grid');
  const [page, setPage] = useState(1);
  const [totalProducts, setTotalProducts] = useState(0);
  const [fetchingPrices, setFetchingPrices] = useState(false);
  const [selected, setSelected] = useState(new Set());
  const [filterStatus, setFilterStatus] = useState('all');
  const [filterAccount, setFilterAccount] = useState('');
  const [accounts, setAccounts] = useState([]);
  const [showBulkStrategy, setShowBulkStrategy] = useState(false);
  const [bulkStrategyType, setBulkStrategyType] = useState('rrp');
  const [bulkMinPrice, setBulkMinPrice] = useState('');
  const [bulkRrpPrice, setBulkRrpPrice] = useState('');
  const isMounted = useRef(true);

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedSearch(searchTerm), 350);
    return () => clearTimeout(timer);
  }, [searchTerm]);

  // Reload when debounced search or strategy changes
  useEffect(() => {
    loadProducts(1);
  }, [debouncedSearch, filterStrategy, filterStatus, filterAccount]);

  useEffect(() => {
    isMounted.current = true;
    getSettings()
      .then((res) => { if (isMounted.current) setWalletPct(res.data.wb_wallet_discount_pct); })
      .catch(() => {});
    getAccounts()
      .then((res) => { if (isMounted.current) setAccounts(res.data.accounts || []); })
      .catch(() => {});
    const interval = setInterval(() => refreshProductsSilent(), 30000);
    return () => {
      isMounted.current = false;
      clearInterval(interval);
    };
  }, []);

  const loadProducts = async (p = page) => {
    setLoading(true);
    try {
      const params = { page: p, page_size: PAGE_SIZE };
      if (debouncedSearch) params.search = debouncedSearch;
      if (filterStrategy !== 'all') params.strategy = filterStrategy;
      if (filterStatus === 'active') params.is_active = true;
      if (filterStatus === 'inactive') params.is_active = false;
      if (filterAccount) params.account_id = filterAccount;
      const response = await getProducts(params);
      setProducts(response.data.products);
      setTotalProducts(response.data.total);
      setPage(p);
    } catch {
      toast.error('Ошибка загрузки товаров');
    } finally {
      setLoading(false);
    }
  };

  const pageRef = useRef(page);
  useEffect(() => { pageRef.current = page; }, [page]);

  const searchRef = useRef(debouncedSearch);
  const strategyRef = useRef(filterStrategy);
  useEffect(() => { searchRef.current = debouncedSearch; }, [debouncedSearch]);
  useEffect(() => { strategyRef.current = filterStrategy; }, [filterStrategy]);

  const refreshProductsSilent = async () => {
    try {
      const params = { page: pageRef.current, page_size: PAGE_SIZE };
      if (searchRef.current) params.search = searchRef.current;
      if (strategyRef.current !== 'all') params.strategy = strategyRef.current;
      const response = await getProducts(params);
      if (isMounted.current) {
        setProducts(response.data.products);
        setTotalProducts(response.data.total);
      }
    } catch {}
  };

  const totalPages = Math.ceil(totalProducts / PAGE_SIZE);

  const handlePageChange = (newPage) => {
    if (newPage < 1 || newPage > totalPages) return;
    loadProducts(newPage);
    window.scrollTo({ top: 0, behavior: 'smooth' });
  };

  const handleDelete = async (id, sku) => {
    if (!confirm(`Удалить товар ${sku}?`)) return;
    try {
      await deleteProduct(id);
      removeProduct(id);
      toast.success('Товар удален');
    } catch {
      toast.error('Ошибка удаления товара');
    }
  };

  const handleRunRepricer = async (sku) => {
    try {
      toast.loading('Запуск репрайсера...', { id: 'repricer' });
      await runRepricer([sku]);
      toast.success('Репрайсер завершен', { id: 'repricer' });
      setTimeout(loadProducts, 2000);
    } catch {
      toast.error('Ошибка запуска репрайсера', { id: 'repricer' });
    }
  };

  const handleFetchPrices = async () => {
    setFetchingPrices(true);
    try {
      const res = await fetchPrices();
      toast.success(`Цены обновлены: ${res.data.updated} из ${res.data.total}`);
      setTimeout(() => loadProducts(page), 1000);
    } catch {
      toast.error('Ошибка обновления цен');
    } finally {
      setFetchingPrices(false);
    }
  };

  const handleConfigClick = (product) => {
    setSelectedProduct(product);
    setShowConfigModal(true);
  };

  const handleHistoryClick = (product) => {
    setSelectedProduct(product);
    setShowHistoryModal(true);
  };

  const toggleSelect = (id) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === filteredProducts.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(filteredProducts.map((p) => p.id)));
    }
  };

  const handleBulkActivate = async (isActive) => {
    try {
      await bulkActivate(Array.from(selected), isActive);
      toast.success(isActive ? 'Товары активированы' : 'Товары деактивированы');
      setSelected(new Set());
      loadProducts();
    } catch {
      toast.error('Ошибка массовой операции');
    }
  };

  const handleBulkDelete = async () => {
    if (!confirm(`Удалить ${selected.size} товар(ов)?`)) return;
    try {
      await bulkDelete(Array.from(selected));
      toast.success('Товары удалены');
      setSelected(new Set());
      loadProducts();
    } catch {
      toast.error('Ошибка массового удаления');
    }
  };

  // Filtering is now done server-side via search & strategy params
  const filteredProducts = products;

  const getStatusBadge = (product, compact = false) => {
    if (compact) {
      const color = !product.config ? '#d97706' : !product.config.is_active ? '#9ca3af' : '#16a34a';
      return <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} title={!product.config ? 'Не настроен' : !product.config.is_active ? 'Отключен' : 'Активен'} />;
    }
    if (!product.config) return <span className="badge badge-warning">Не настроен</span>;
    if (!product.config.is_active) return <span className="badge">Отключен</span>;
    return <span className="badge badge-success">Активен</span>;
  };

  const getStrategyLabel = (strategy) => {
    if (strategy === 'rrp') return 'РРЦ';
    if (strategy === 'competitor') return 'Конкурент';
    return 'N/A';
  };

  const getMarginColor = (m) => {
    if (m === null || m === undefined) return 'var(--text-3)';
    if (m >= 30) return '#16a34a';
    if (m >= 10) return '#d97706';
    return '#dc2626';
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="page-h1">Товары</h1>
          <p className="text-sm mt-1" style={{ color: 'var(--text-3)' }}>
            Управление товарами и стратегиями ценообразования
          </p>
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleFetchPrices}
            disabled={fetchingPrices}
            className="btn btn-secondary gap-2"
            title="Получить текущие цены с WB для всех товаров (без изменения цен)"
          >
            <RefreshCw className={`w-4 h-4 ${fetchingPrices ? 'animate-spin' : ''}`} />
            {fetchingPrices ? 'Обновление...' : 'Обновить цены'}
          </button>
          <button onClick={() => setShowProductModal(true)} className="btn btn-primary gap-2">
            <Plus className="w-4 h-4" />
            Добавить товар
          </button>
        </div>
      </div>

      {/* Filters + View Toggle */}
      <div className="card mb-4">
        <div className="flex items-center gap-3">
          <div className="flex-1 relative">
            <Search
              className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4"
              style={{ color: 'var(--text-3)' }}
            />
            <input
              type="text"
              placeholder="Поиск по артикулу или названию..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="input pl-9"
            />
          </div>
          <select
            value={filterStrategy}
            onChange={(e) => setFilterStrategy(e.target.value)}
            className="input w-40"
          >
            <option value="all">Все стратегии</option>
            <option value="rrp">РРЦ</option>
            <option value="competitor">Конкурент</option>
          </select>
          <select
            value={filterStatus}
            onChange={(e) => setFilterStatus(e.target.value)}
            className="input w-36"
          >
            <option value="all">Все статусы</option>
            <option value="active">Активные</option>
            <option value="inactive">Отключённые</option>
          </select>
          <select
            value={filterAccount}
            onChange={(e) => setFilterAccount(e.target.value)}
            className="input w-40"
          >
            <option value="">Все кабинеты</option>
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>{a.name}</option>
            ))}
          </select>

          <div className="flex rounded-lg overflow-hidden border border-border bg-muted/50">
            <button
              onClick={() => setViewMode('grid')}
              className={`p-2 transition-colors ${viewMode === 'grid' ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              title="Карточки"
            >
              <LayoutGrid className="w-4 h-4" />
            </button>
            <button
              onClick={() => setViewMode('table')}
              className={`p-2 transition-colors ${viewMode === 'table' ? 'bg-primary/10 text-primary' : 'text-muted-foreground hover:text-foreground'}`}
              title="Таблица"
            >
              <List className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <div className="spinner" />
        </div>
      ) : filteredProducts.length === 0 ? (
        <div className="card text-center py-12">
          <p style={{ color: 'var(--text-3)' }}>Товары не найдены</p>
          <button onClick={() => setShowProductModal(true)} className="btn btn-primary mt-4">
            Добавить первый товар
          </button>
        </div>
      ) : viewMode === 'grid' ? (
        /* Card Grid — компактный вид, сгруппированный по seller_sku */
        (() => {
          // Группируем по seller_sku только когда не выбран конкретный кабинет
          const groups = [];
          if (filterAccount) {
            filteredProducts.forEach(p => groups.push([p.seller_sku || `nm_${p.nm_id}`, [p]]));
          } else {
            const map = new Map();
            filteredProducts.forEach(p => {
              const key = p.seller_sku || `nm_${p.nm_id}`;
              if (!map.has(key)) map.set(key, []);
              map.get(key).push(p);
            });
            map.forEach((prods, key) => groups.push([key, prods]));
          }

          return (
            <div className="grid grid-cols-3 md:grid-cols-4 lg:grid-cols-6 xl:grid-cols-7 gap-3">
              {groups.map(([groupKey, groupProds]) => {
                const first = groupProds[0];
                const isMulti = groupProds.length > 1;
                const imgSrc = first.image_url || getWBImageUrl(first.nm_id);

                return (
                  <div
                    key={groupKey}
                    className="product-card"
                    style={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/products/${first.id}`)}
                  >
                    {/* Image */}
                    <div className="relative overflow-hidden bg-muted" style={{ paddingBottom: '120%' }}>
                      {imgSrc && (
                        <img
                          src={imgSrc}
                          alt={first.name}
                          className="absolute inset-0 w-full h-full object-cover"
                          loading="lazy"
                          onError={(e) => {
                            e.target.style.display = 'none';
                            const ph = e.target.parentElement?.querySelector('.img-placeholder');
                            if (ph) ph.style.display = 'flex';
                          }}
                        />
                      )}
                      <div
                        className="img-placeholder absolute inset-0 flex-col items-center justify-center"
                        style={{ display: !imgSrc ? 'flex' : 'none', background: '#f3f4f6' }}
                      >
                        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" style={{ color: '#d1d5db' }}>
                          <rect x="3" y="3" width="18" height="18" rx="2" stroke="currentColor" strokeWidth="1.5"/>
                          <circle cx="8.5" cy="8.5" r="1.5" fill="currentColor"/>
                          <path d="M21 15l-5-5L5 21" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                        </svg>
                      </div>
                      {/* Hover actions */}
                      <div
                        className="absolute inset-0 flex items-center justify-center gap-1 opacity-0 hover:opacity-100 pointer-events-none hover:pointer-events-auto transition-opacity"
                        style={{ background: 'rgba(0,0,0,.65)' }}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <button onClick={() => handleRunRepricer(first.seller_sku)} className="btn btn-xs btn-primary p-1" title="Запустить">
                          <Play className="w-3 h-3" />
                        </button>
                        <button onClick={() => handleHistoryClick(first)} className="btn btn-xs btn-secondary p-1" title="История цен">
                          <LineChart className="w-3 h-3" />
                        </button>
                        {!isMulti && (
                          <>
                            <button onClick={() => handleConfigClick(first)} className="btn btn-xs btn-secondary p-1" title="Настроить">
                              <Settings className="w-3 h-3" />
                            </button>
                            <button onClick={() => handleDelete(first.id, first.seller_sku)} className="btn btn-xs btn-danger p-1" title="Удалить">
                              <Trash2 className="w-3 h-3" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>

                    {/* Info — photo + code + name only */}
                    <div className="p-2" style={{ background: 'var(--card)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 4 }}>
                      <div className="min-w-0 flex-1">
                        {first.name && (
                          <p className="truncate mb-0.5" style={{ fontSize: '12px', fontWeight: 500, color: 'var(--text)' }}>
                            {first.name}
                          </p>
                        )}
                        <p className="font-bold truncate" style={{ fontSize: '11px', color: 'var(--neon)' }}>
                          {first.seller_sku || <span style={{ color: 'var(--text-3)' }}>#{first.nm_id}</span>}
                        </p>
                      </div>
                      <button
                        onClick={(e) => { e.stopPropagation(); handleDelete(first.id, first.seller_sku); }}
                        className="btn btn-xs btn-danger p-1 flex-shrink-0"
                        title="Удалить товар"
                        style={{ padding: '3px' }}
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          );
        })()
      ) : (
        /* Table View */
        <div className="card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="dark-table">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>
                    <input
                      type="checkbox"
                      checked={filteredProducts.length > 0 && selected.size === filteredProducts.length}
                      onChange={toggleSelectAll}
                      className="accent-[var(--primary)]"
                    />
                  </th>
                  <th>Артикул</th>
                  <th>Кабинет</th>
                  <th>Название</th>
                  <th>nmID</th>
                  <th>Стратегия</th>
                  <th>Цена (СПП)</th>
                  <th>С кошельком</th>
                  <th>Маржа</th>
                  <th>Статус</th>
                  <th style={{ textAlign: 'right' }}>Действия</th>
                </tr>
              </thead>
              <tbody>
                {filteredProducts.map((product) => (
                  <tr key={product.id} className="cursor-pointer hover:bg-muted/50" onClick={() => navigate(`/products/${product.id}`)}>
                    <td onClick={(e) => e.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selected.has(product.id)}
                        onChange={() => toggleSelect(product.id)}
                        className="accent-[var(--primary)]"
                      />
                    </td>
                    <td>
                      <span className="font-medium text-foreground">
                        {product.seller_sku || <span className="text-muted-foreground">#{product.nm_id}</span>}
                      </span>
                    </td>
                    <td>
                      <span style={{ fontSize: '12px', color: 'var(--text-3)' }}>{product.account_name || '—'}</span>
                    </td>
                    <td>
                      <span className="line-clamp-2">{product.name || '—'}</span>
                    </td>
                    <td style={{ color: 'var(--text-2)' }}>{product.nm_id}</td>
                    <td>
                      {product.config
                        ? <span className="badge badge-info">{getStrategyLabel(product.config.strategy)}</span>
                        : <span style={{ color: 'var(--text-3)' }}>—</span>}
                    </td>
                    <td>
                      {product.current_seller_price ? (
                        <div>
                          <div className="font-medium text-foreground">
                            {Number(product.current_seller_price).toLocaleString('ru')}₽
                          </div>
                          <div className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
                            СПП: {Number(product.current_final_price).toLocaleString('ru')}₽
                          </div>
                        </div>
                      ) : <span style={{ color: 'var(--text-3)' }}>—</span>}
                    </td>
                    <td>
                      {product.current_final_price ? (
                        <div>
                          <div className="font-medium text-green-600">
                            {Math.round(Number(product.current_final_price) * (1 - walletPct / 100)).toLocaleString('ru')}₽
                          </div>
                          <div className="text-xs" style={{ color: 'var(--text-3)' }}>−{walletPct}%</div>
                        </div>
                      ) : <span style={{ color: 'var(--text-3)' }}>—</span>}
                    </td>
                    <td>
                      {product.margin !== null && product.margin !== undefined ? (
                        <span className="font-semibold text-sm" style={{ color: getMarginColor(product.margin) }}>
                          {product.margin}%
                        </span>
                      ) : (
                        <span style={{ color: 'var(--text-3)' }}>—</span>
                      )}
                    </td>
                    <td>{getStatusBadge(product)}</td>
                    <td onClick={(e) => e.stopPropagation()}>
                      <div className="flex items-center justify-end gap-1.5">
                        <button
                          onClick={() => handleRunRepricer(product.seller_sku)}
                          className="btn btn-xs btn-secondary"
                          title="Запустить"
                        >
                          <Play className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleHistoryClick(product)}
                          className="btn btn-xs btn-secondary"
                          title="История цен"
                        >
                          <LineChart className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleConfigClick(product)}
                          className="btn btn-xs btn-secondary"
                          title="Настроить"
                        >
                          <Settings className="w-3.5 h-3.5" />
                        </button>
                        <button
                          onClick={() => handleDelete(product.id, product.seller_sku)}
                          className="btn btn-xs btn-danger"
                          title="Удалить"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Pagination */}
      {totalPages > 1 && !loading && (
        <div className="flex items-center justify-between mt-5">
          <p className="text-xs" style={{ color: 'var(--text-3)' }}>
            Показано {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, totalProducts)} из {totalProducts}
          </p>
          <div className="flex items-center gap-1.5">
            <button
              onClick={() => handlePageChange(page - 1)}
              disabled={page <= 1}
              className="btn btn-sm btn-secondary p-1.5"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            {Array.from({ length: Math.min(totalPages, 7) }, (_, i) => {
              let p;
              if (totalPages <= 7) {
                p = i + 1;
              } else if (page <= 4) {
                p = i + 1;
              } else if (page >= totalPages - 3) {
                p = totalPages - 6 + i;
              } else {
                p = page - 3 + i;
              }
              return (
                <button
                  key={p}
                  onClick={() => handlePageChange(p)}
                  className={`btn btn-sm min-w-[32px] text-xs ${p === page ? 'bg-primary text-primary-foreground font-bold' : 'text-muted-foreground font-medium'}`}
                >
                  {p}
                </button>
              );
            })}
            <button
              onClick={() => handlePageChange(page + 1)}
              disabled={page >= totalPages}
              className="btn btn-sm btn-secondary p-1.5"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      )}

      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-50 flex items-center gap-3 rounded-2xl bg-card border border-border px-6 py-3 shadow-2xl">
          <span className="text-sm font-medium" style={{ color: 'var(--text-2)' }}>
            Выбрано: {selected.size}
          </span>
          <button onClick={() => handleBulkActivate(true)} className="btn btn-sm btn-primary">
            Активировать
          </button>
          <button onClick={() => handleBulkActivate(false)} className="btn btn-sm btn-secondary">
            Деактивировать
          </button>
          <button onClick={() => setShowBulkStrategy(true)} className="btn btn-sm btn-secondary">
            Стратегия
          </button>
          <button onClick={handleBulkDelete} className="btn btn-sm btn-danger">
            Удалить
          </button>
          <button onClick={() => setSelected(new Set())} className="btn btn-sm btn-secondary">
            ✕
          </button>
        </div>
      )}

      {/* Bulk strategy modal */}
      {showBulkStrategy && (
        <div className="modal-overlay">
          <div className="modal-box max-w-sm">
            <div className="modal-header">
              <h2 className="text-base font-semibold text-foreground">Массовая смена стратегии</h2>
              <button onClick={() => setShowBulkStrategy(false)} className="btn btn-ghost btn-sm p-1">✕</button>
            </div>
            <div className="p-5 space-y-4">
              <p className="text-sm text-muted-foreground">Применить к {selected.size} товарам</p>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">Стратегия</label>
                <select value={bulkStrategyType} onChange={(e) => setBulkStrategyType(e.target.value)} className="input">
                  <option value="rrp">РРЦ</option>
                  <option value="competitor">Конкурент</option>
                </select>
              </div>
              <div>
                <label className="block text-sm font-medium mb-1.5 text-muted-foreground">Мин. цена (МРЦ), ₽</label>
                <input type="number" value={bulkMinPrice} onChange={(e) => setBulkMinPrice(e.target.value)} className="input" placeholder="Обязательно" min="0" />
              </div>
              {bulkStrategyType === 'rrp' && (
                <div>
                  <label className="block text-sm font-medium mb-1.5 text-muted-foreground">РРЦ, ₽</label>
                  <input type="number" value={bulkRrpPrice} onChange={(e) => setBulkRrpPrice(e.target.value)} className="input" placeholder="Рекомендованная цена" min="0" />
                </div>
              )}
              <div className="flex justify-end gap-2 pt-2">
                <button onClick={() => setShowBulkStrategy(false)} className="btn btn-secondary">Отмена</button>
                <button
                  className="btn btn-primary"
                  onClick={async () => {
                    if (!bulkMinPrice) { toast.error('Укажите мин. цену'); return; }
                    try {
                      const cfg = { min_price: Number(bulkMinPrice), is_active: true };
                      if (bulkStrategyType === 'rrp' && bulkRrpPrice) cfg.rrp_price = Number(bulkRrpPrice);
                      await bulkStrategy(Array.from(selected), bulkStrategyType, cfg);
                      toast.success(`Стратегия применена к ${selected.size} товарам`);
                      setShowBulkStrategy(false);
                      setSelected(new Set());
                      loadProducts();
                    } catch { toast.error('Ошибка применения стратегии'); }
                  }}
                >
                  Применить
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {showProductModal && (
        <ProductModal onClose={() => { setShowProductModal(false); loadProducts(); }} />
      )}
      {showConfigModal && selectedProduct && (
        <ConfigModal
          product={selectedProduct}
          onClose={() => { setShowConfigModal(false); setSelectedProduct(null); loadProducts(); }}
        />
      )}
      {showHistoryModal && selectedProduct && (
        <PriceHistoryModal
          product={selectedProduct}
          onClose={() => { setShowHistoryModal(false); setSelectedProduct(null); }}
        />
      )}
    </div>
  );
}

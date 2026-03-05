import { useState, useRef } from 'react';
import {
  Upload, Download, FileText, FileSpreadsheet,
  CheckCircle, AlertCircle, Loader2, ArrowDown,
  Package, Settings2, Filter, ChevronDown, TrendingUp
} from 'lucide-react';
import toast from 'react-hot-toast';
import {
  importStrategy, importProducts, importCosts,
  exportHistory, exportProducts, downloadTemplate, downloadCostsTemplate
} from '../api/client';

/* ─── helpers ─── */
function triggerDownload(blob, filename) {
  const url = window.URL.createObjectURL(new Blob([blob]));
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

/* ─── sub-components ─── */
function SectionTitle({ icon: Icon, color, title, subtitle }) {
  return (
    <div className="flex items-center gap-3 mb-5">
      <div
        className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
        style={{ background: color + '20' }}
      >
        <Icon className="w-5 h-5" style={{ color }} />
      </div>
      <div>
        <h2 className="text-base font-semibold text-gray-900">{title}</h2>
        <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>
      </div>
    </div>
  );
}

function ImportResult({ result }) {
  if (!result) return null;
  const hasErrors = result.errors?.length > 0;
  return (
    <div
      className="mt-3 p-3 rounded-xl text-xs border"
      style={{
        background: hasErrors ? '#fef2f2' : '#f0fdf4',
        borderColor: hasErrors ? '#fecaca' : '#bbf7d0',
        color: hasErrors ? '#991b1b' : '#14532d',
      }}
    >
      <div className="flex items-center gap-1.5 font-semibold mb-1">
        {hasErrors ? <AlertCircle className="w-3.5 h-3.5" /> : <CheckCircle className="w-3.5 h-3.5" />}
        {hasErrors ? 'Импорт завершён с ошибками' : 'Импорт успешен'}
      </div>
      <div className="space-y-0.5 text-gray-600">
        {result.created  !== undefined && <div>Создано: <b>{result.created}</b></div>}
        {result.updated  !== undefined && <div>Обновлено: <b>{result.updated}</b></div>}
        {result.skipped  !== undefined && <div>Пропущено (уже есть): <b>{result.skipped}</b></div>}
        {result.total    !== undefined && <div>Всего строк: <b>{result.total}</b></div>}
      </div>
      {hasErrors && (
        <div className="mt-2 space-y-0.5 max-h-28 overflow-y-auto">
          {result.errors.map((e, i) => (
            <div key={i} className="text-red-700">• {e}</div>
          ))}
        </div>
      )}
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-gray-500">{label}</label>
      <div className="relative">
        <select
          value={value}
          onChange={e => onChange(e.target.value)}
          className="input text-sm pr-8 appearance-none"
          style={{ paddingRight: '2rem' }}
        >
          {options.map(o => (
            <option key={o.value} value={o.value}>{o.label}</option>
          ))}
        </select>
        <ChevronDown className="w-3.5 h-3.5 text-gray-400 absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
      </div>
    </div>
  );
}

/* ─── main page ─── */
export default function ImportExport() {
  // Template downloads
  const [loadingTplProducts, setLoadingTplProducts] = useState(false);
  const [loadingTplStrategy, setLoadingTplStrategy] = useState(false);
  const [loadingTplCosts,    setLoadingTplCosts]    = useState(false);

  // Export
  const [loadingExpProducts, setLoadingExpProducts] = useState(false);
  const [loadingExpHistory,  setLoadingExpHistory]  = useState(false);
  const [exportFilters, setExportFilters] = useState({ status: 'all', strategy: 'all' });

  // Import
  const [uploadingProducts, setUploadingProducts] = useState(false);
  const [uploadingStrategy, setUploadingStrategy] = useState(false);
  const [uploadingCosts,    setUploadingCosts]    = useState(false);
  const [importResultProd,  setImportResultProd]  = useState(null);
  const [importResultStrat, setImportResultStrat] = useState(null);
  const [importResultCosts, setImportResultCosts] = useState(null);

  const fileInputProducts = useRef(null);
  const fileInputStrategy = useRef(null);
  const fileInputCosts    = useRef(null);

  /* ── templates ── */
  const handleDownloadTemplate = async (type) => {
    const setLoading = type === 'products' ? setLoadingTplProducts : setLoadingTplStrategy;
    setLoading(true);
    try {
      const res = await downloadTemplate(type);
      const name = type === 'products' ? 'template_products.xlsx' : 'template_strategies.xlsx';
      triggerDownload(res.data, name);
      toast.success('Шаблон скачан');
    } catch {
      toast.error('Ошибка скачивания шаблона');
    } finally {
      setLoading(false);
    }
  };

  const handleDownloadCostsTemplate = async () => {
    setLoadingTplCosts(true);
    try {
      const res = await downloadCostsTemplate();
      triggerDownload(res.data, 'template_costs.xlsx');
      toast.success('Шаблон скачан');
    } catch {
      toast.error('Ошибка скачивания шаблона');
    } finally {
      setLoadingTplCosts(false);
    }
  };

  /* ── export ── */
  const handleExportProducts = async () => {
    setLoadingExpProducts(true);
    try {
      const res = await exportProducts(exportFilters);
      const ts = new Date().toISOString().slice(0, 10);
      triggerDownload(res.data, `products_${ts}.xlsx`);
      toast.success('Файл скачан');
    } catch {
      toast.error('Ошибка экспорта');
    } finally {
      setLoadingExpProducts(false);
    }
  };

  const handleExportHistory = async () => {
    setLoadingExpHistory(true);
    try {
      const res = await exportHistory();
      const ts = new Date().toISOString().slice(0, 10);
      triggerDownload(res.data, `price_history_${ts}.xlsx`);
      toast.success('Файл скачан');
    } catch {
      toast.error('Ошибка экспорта');
    } finally {
      setLoadingExpHistory(false);
    }
  };

  /* ── import ── */
  const handleProductsImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingProducts(true);
    setImportResultProd(null);
    try {
      const res = await importProducts(file);
      setImportResultProd(res.data);
      toast.success(`Создано ${res.data.created} товаров`);
      if (res.data.errors?.length) toast.error(`Ошибок: ${res.data.errors.length}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка импорта');
    } finally {
      setUploadingProducts(false);
      e.target.value = '';
    }
  };

  const handleStrategyImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingStrategy(true);
    setImportResultStrat(null);
    try {
      const res = await importStrategy(file);
      setImportResultStrat(res.data);
      toast.success(`Обновлено ${res.data.updated}, создано ${res.data.created}`);
      if (res.data.errors?.length) toast.error(`Ошибок: ${res.data.errors.length}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка импорта');
    } finally {
      setUploadingStrategy(false);
      e.target.value = '';
    }
  };

  const handleCostsImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingCosts(true);
    setImportResultCosts(null);
    try {
      const res = await importCosts(file);
      setImportResultCosts(res.data);
      toast.success(`Себестоимость обновлена: ${res.data.updated} товаров`);
      if (res.data.errors?.length) toast.error(`Ошибок: ${res.data.errors.length}`);
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка импорта');
    } finally {
      setUploadingCosts(false);
      e.target.value = '';
    }
  };

  const Spin = () => <Loader2 className="w-4 h-4 animate-spin" />;

  // Derived label for active filters badge
  const activeFilterCount = [
    exportFilters.status !== 'all',
    exportFilters.strategy !== 'all',
  ].filter(Boolean).length;

  return (
    <div>
      {/* Header */}
      <div className="mb-8">
        <h1 className="text-2xl font-bold text-gray-900">Импорт / Экспорт</h1>
        <p className="text-gray-500 mt-1 text-sm">
          Массовая загрузка товаров и стратегий, выгрузка данных в Excel
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* ══ ШАБЛОНЫ ══ */}
        <div className="card">
          <SectionTitle
            icon={FileText}
            color="#7c3aed"
            title="Шаблоны"
            subtitle="Скачайте пустые файлы для заполнения"
          />

          <div className="space-y-3">
            {/* Products template */}
            <div
              className="flex items-center justify-between gap-3 p-3.5 rounded-xl border"
              style={{ borderColor: '#e8eaf0', background: '#f9fafc' }}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                     style={{ background: '#7c3aed15' }}>
                  <Package className="w-4 h-4" style={{ color: '#7c3aed' }} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">Шаблон товаров</p>
                  <p className="text-xs text-gray-400">nmID + ID кабинета обязательны</p>
                </div>
              </div>
              <button
                className="btn btn-secondary btn-sm gap-1.5 flex-shrink-0"
                onClick={() => handleDownloadTemplate('products')}
                disabled={loadingTplProducts}
              >
                {loadingTplProducts ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
                .xlsx
              </button>
            </div>

            {/* Strategy template */}
            <div
              className="flex items-center justify-between gap-3 p-3.5 rounded-xl border"
              style={{ borderColor: '#e8eaf0', background: '#f9fafc' }}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                     style={{ background: '#6366f115' }}>
                  <Settings2 className="w-4 h-4" style={{ color: '#6366f1' }} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">Шаблон стратегий</p>
                  <p className="text-xs text-gray-400">12 колонок: МРЦ, РРЦ, конкурент…</p>
                </div>
              </div>
              <button
                className="btn btn-secondary btn-sm gap-1.5 flex-shrink-0"
                onClick={() => handleDownloadTemplate('strategy')}
                disabled={loadingTplStrategy}
              >
                {loadingTplStrategy ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
                .xlsx
              </button>
            </div>

            {/* Costs template */}
            <div
              className="flex items-center justify-between gap-3 p-3.5 rounded-xl border"
              style={{ borderColor: '#e8eaf0', background: '#f9fafc' }}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                     style={{ background: '#00b8c815' }}>
                  <TrendingUp className="w-4 h-4" style={{ color: '#00b8c8' }} />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-gray-900">Шаблон себестоимостей</p>
                  <p className="text-xs text-gray-400">Артикул + закупочная цена</p>
                </div>
              </div>
              <button
                className="btn btn-secondary btn-sm gap-1.5 flex-shrink-0"
                onClick={handleDownloadCostsTemplate}
                disabled={loadingTplCosts}
              >
                {loadingTplCosts ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
                .xlsx
              </button>
            </div>
          </div>

          {/* Collapsible column descriptions */}
          <div className="mt-4 space-y-2">
            <details>
              <summary className="cursor-pointer text-xs text-purple-600 font-medium select-none list-none flex items-center gap-1">
                <ChevronDown className="w-3 h-3" /> Колонки шаблона товаров
              </summary>
              <div className="mt-2 p-3 rounded-lg bg-gray-50 border border-gray-100 text-xs text-gray-600 space-y-1.5">
                <div className="font-semibold text-gray-700 mb-1">Хотя бы одно из A или B обязательно:</div>
                <div><b>A — nmID</b> — числовой артикул WB (если есть)</div>
                <div><b>B — Артикул продавца</b> — vendorCode (если нет nmID, система найдёт его сама)</div>
                <div className="pt-1 border-t border-gray-200 mt-1"><b>C — ID аккаунта *</b> — из раздела Аккаунты (обязательно)</div>
                <div><b>D — Название</b> — <i>необязательно</i>, подтянется из WB автоматически</div>
              </div>
            </details>
            <details>
              <summary className="cursor-pointer text-xs text-indigo-600 font-medium select-none list-none flex items-center gap-1">
                <ChevronDown className="w-3 h-3" /> Колонки шаблона стратегий
              </summary>
              <div className="mt-2 p-3 rounded-lg bg-gray-50 border border-gray-100 text-xs text-gray-600 space-y-1">
                <div><b>A</b> — Артикул продавца *</div>
                <div><b>B</b> — Стратегия: <code>rrp</code> / <code>competitor</code> *</div>
                <div><b>C</b> — Активен: <code>да</code> / <code>нет</code></div>
                <div><b>D</b> — МРЦ — мин. цена *</div>
                <div><b>E–H</b> — Макс. цена, РРЦ, допуск, кошелёк</div>
                <div><b>I–L</b> — nmID конкурента, смещение</div>
              </div>
            </details>
          </div>
        </div>

        {/* ══ ИМПОРТ ══ */}
        <div className="card">
          <SectionTitle
            icon={Upload}
            color="#059669"
            title="Импорт"
            subtitle="Загрузите заполненный Excel-файл"
          />

          <div className="space-y-4">
            {/* Import products */}
            <div
              className="p-4 rounded-xl border-2 border-dashed"
              style={{ borderColor: '#a7f3d0', background: '#f0fdf4' }}
            >
              <div className="flex items-center gap-2 mb-1">
                <Package className="w-4 h-4 text-emerald-600" />
                <span className="text-sm font-medium text-gray-900">Товары</span>
                {uploadingProducts && <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-600 ml-auto" />}
              </div>
              <p className="text-xs text-gray-500 mb-3">
                Достаточно nmID <b>или</b> артикула продавца + ID кабинета. Название и фото подтянутся автоматически.
              </p>
              <input ref={fileInputProducts} type="file" accept=".xlsx,.xls"
                     onChange={handleProductsImport} disabled={uploadingProducts} className="hidden" />
              <button
                className="btn btn-sm w-full gap-1.5"
                style={{ background: uploadingProducts ? '#a7f3d0' : '#059669', color: '#fff', border: 'none' }}
                onClick={() => fileInputProducts.current?.click()}
                disabled={uploadingProducts}
              >
                {uploadingProducts ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingProducts ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultProd} />

            {/* Import strategies */}
            <div
              className="p-4 rounded-xl border-2 border-dashed"
              style={{ borderColor: '#c4b5fd', background: '#f5f3ff' }}
            >
              <div className="flex items-center gap-2 mb-1">
                <Settings2 className="w-4 h-4 text-purple-600" />
                <span className="text-sm font-medium text-gray-900">Стратегии</span>
                {uploadingStrategy && <Loader2 className="w-3.5 h-3.5 animate-spin text-purple-600 ml-auto" />}
              </div>
              <p className="text-xs text-gray-500 mb-3">
                Обновить параметры репрайсера по артикулу продавца
              </p>
              <input ref={fileInputStrategy} type="file" accept=".xlsx,.xls"
                     onChange={handleStrategyImport} disabled={uploadingStrategy} className="hidden" />
              <button
                className="btn btn-sm w-full gap-1.5"
                style={{ background: uploadingStrategy ? '#c4b5fd' : '#7c3aed', color: '#fff', border: 'none' }}
                onClick={() => fileInputStrategy.current?.click()}
                disabled={uploadingStrategy}
              >
                {uploadingStrategy ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingStrategy ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultStrat} />

            {/* Import costs */}
            <div
              className="p-4 rounded-xl border-2 border-dashed"
              style={{ borderColor: '#67e8f9', background: '#ecfeff' }}
            >
              <div className="flex items-center gap-2 mb-1">
                <TrendingUp className="w-4 h-4" style={{ color: '#0891b2' }} />
                <span className="text-sm font-medium text-gray-900">Себестоимости</span>
                {uploadingCosts && <Loader2 className="w-3.5 h-3.5 animate-spin ml-auto" style={{ color: '#0891b2' }} />}
              </div>
              <p className="text-xs text-gray-500 mb-3">
                Загрузить закупочные цены для расчёта маржи по артикулу продавца
              </p>
              <input ref={fileInputCosts} type="file" accept=".xlsx,.xls"
                     onChange={handleCostsImport} disabled={uploadingCosts} className="hidden" />
              <button
                className="btn btn-sm w-full gap-1.5"
                style={{ background: uploadingCosts ? '#67e8f9' : '#0891b2', color: '#fff', border: 'none' }}
                onClick={() => fileInputCosts.current?.click()}
                disabled={uploadingCosts}
              >
                {uploadingCosts ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingCosts ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultCosts} />
          </div>
        </div>

        {/* ══ ЭКСПОРТ ══ */}
        <div className="card">
          <SectionTitle
            icon={Download}
            color="#2563eb"
            title="Экспорт"
            subtitle="Скачайте данные из системы"
          />

          {/* Products export with filters */}
          <div
            className="p-4 rounded-xl border mb-3"
            style={{ borderColor: '#bfdbfe', background: '#eff6ff' }}
          >
            <div className="flex items-center gap-2 mb-2">
              <FileSpreadsheet className="w-4 h-4 text-blue-600" />
              <span className="text-sm font-medium text-gray-900">Все товары + стратегии</span>
            </div>
            <p className="text-xs text-gray-500 mb-3">
              18 колонок: аккаунт, цены, МРЦ, РРЦ, стратегия. Строки раскрашены по статусу.
            </p>

            {/* Filters */}
            <div
              className="p-3 rounded-lg mb-3 space-y-2"
              style={{ background: 'rgba(37,99,235,0.06)', border: '1px solid #bfdbfe' }}
            >
              <div className="flex items-center gap-1.5 mb-2">
                <Filter className="w-3.5 h-3.5 text-blue-500" />
                <span className="text-xs font-semibold text-blue-700">
                  Фильтры
                  {activeFilterCount > 0 && (
                    <span className="ml-1.5 px-1.5 py-0.5 bg-blue-100 text-blue-700 rounded-full text-xs">
                      {activeFilterCount}
                    </span>
                  )}
                </span>
              </div>
              <FilterSelect
                label="Статус"
                value={exportFilters.status}
                onChange={v => setExportFilters(f => ({ ...f, status: v }))}
                options={[
                  { value: 'all',          label: 'Все статусы' },
                  { value: 'active',       label: 'Активные' },
                  { value: 'inactive',     label: 'Отключённые' },
                  { value: 'unconfigured', label: 'Без стратегии' },
                ]}
              />
              <FilterSelect
                label="Стратегия"
                value={exportFilters.strategy}
                onChange={v => setExportFilters(f => ({ ...f, strategy: v }))}
                options={[
                  { value: 'all',        label: 'Все стратегии' },
                  { value: 'rrp',        label: 'РРЦ' },
                  { value: 'competitor', label: 'Конкурент' },
                ]}
              />
            </div>

            <button
              className="btn btn-primary btn-sm w-full gap-1.5"
              onClick={handleExportProducts}
              disabled={loadingExpProducts}
            >
              {loadingExpProducts ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
              {loadingExpProducts ? 'Формирую…' : 'Скачать xlsx'}
            </button>
          </div>

          {/* History export */}
          <div
            className="p-4 rounded-xl border"
            style={{ borderColor: '#fde68a', background: '#fffbeb' }}
          >
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-4 h-4 text-amber-600" />
              <span className="text-sm font-medium text-gray-900">История цен</span>
            </div>
            <p className="text-xs text-gray-500 mb-3">
              Все снимки цен: цена продавца, финальная цена, СПП, дата
            </p>
            <button
              className="btn btn-sm w-full gap-1.5"
              style={{ background: '#d97706', color: '#fff', border: 'none' }}
              onClick={handleExportHistory}
              disabled={loadingExpHistory}
            >
              {loadingExpHistory ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
              {loadingExpHistory ? 'Формирую…' : 'Скачать xlsx'}
            </button>
          </div>

          {/* Status legend */}
          <div className="mt-4 pt-4 border-t border-gray-100">
            <p className="text-xs font-medium text-gray-400 mb-2">Цвета строк в экспорте:</p>
            <div className="space-y-1.5">
              {[
                { bg: '#dcfce7', border: '#bbf7d0', label: 'Активен' },
                { bg: '#fff7ed', border: '#fed7aa', label: 'Отключен' },
                { bg: '#f5f5f5', border: '#e5e7eb', label: 'Без стратегии' },
              ].map(({ bg, border, label }) => (
                <div key={label} className="flex items-center gap-2">
                  <div className="w-4 h-4 rounded border flex-shrink-0"
                       style={{ background: bg, borderColor: border }} />
                  <span className="text-xs text-gray-500">{label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

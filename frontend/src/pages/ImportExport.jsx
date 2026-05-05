import { useState, useRef } from 'react';
import {
  Upload, Download, FileText, FileSpreadsheet,
  CheckCircle, AlertCircle, Loader2, ArrowDown,
  Package, Settings2, Filter, ChevronDown, TrendingUp
} from 'lucide-react';
import toast from 'react-hot-toast';
import {
  importStrategy, importProducts, getTaskStatus, importCharacteristics,
  exportHistory, exportProducts, downloadTemplate, downloadCharacteristicsTemplate
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
        <h2 className="text-base font-semibold text-foreground">{title}</h2>
        <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
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
        background: hasErrors ? 'hsl(var(--status-error-bg))' : 'hsl(var(--status-success-bg))',
        borderColor: hasErrors ? 'hsl(var(--status-error-bg) / 0.5)' : 'hsl(var(--status-success-bg) / 0.5)',
        color: hasErrors ? 'hsl(var(--status-error-fg))' : 'hsl(var(--status-success-fg))',
      }}
    >
      <div className="flex items-center gap-1.5 font-semibold mb-1">
        {hasErrors ? <AlertCircle className="w-3.5 h-3.5" /> : <CheckCircle className="w-3.5 h-3.5" />}
        {hasErrors ? 'Импорт завершён с ошибками' : 'Импорт успешен'}
      </div>
      <div className="space-y-0.5 text-muted-foreground">
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
      {result.message && (
        <div className="mt-2 pt-2 border-t border-current border-opacity-20 text-xs italic opacity-80">
          ℹ️ {result.message}
        </div>
      )}
    </div>
  );
}

function FilterSelect({ label, value, onChange, options }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-muted-foreground">{label}</label>
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
        <ChevronDown className="w-3.5 h-3.5 text-muted-foreground absolute right-2.5 top-1/2 -translate-y-1/2 pointer-events-none" />
      </div>
    </div>
  );
}

/* ─── main page ─── */
export default function ImportExport() {
  // Template downloads
  const [loadingTplProducts, setLoadingTplProducts] = useState(false);
  const [loadingTplStrategy, setLoadingTplStrategy] = useState(false);
  const [loadingTplChar, setLoadingTplChar] = useState(false);
  const [uploadingChar, setUploadingChar] = useState(false);
  const [importResultChar, setImportResultChar] = useState(null);

  // Export
  const [loadingExpProducts, setLoadingExpProducts] = useState(false);
  const [loadingExpHistory,  setLoadingExpHistory]  = useState(false);
  const [exportFilters, setExportFilters] = useState({ status: 'all', strategy: 'all' });

  // Import
  const [uploadingProducts, setUploadingProducts] = useState(false);
  const [uploadingStrategy, setUploadingStrategy] = useState(false);
  const [importResultProd,  setImportResultProd]  = useState(null);
  const [importResultStrat, setImportResultStrat] = useState(null);

  const fileInputProducts = useRef(null);
  const fileInputStrategy = useRef(null);
  const fileInputChar = useRef(null);

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

  const handleDownloadCharacteristicsTemplate = async () => {
    setLoadingTplChar(true);
    try {
      const res = await downloadCharacteristicsTemplate();
      triggerDownload(res.data, 'template_characteristics.xlsx');
      toast.success('Шаблон скачан');
    } catch {
      toast.error('Ошибка скачивания шаблона');
    } finally {
      setLoadingTplChar(false);
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
  const parseImportError = (err) => {
    const detail = err.response?.data?.detail;
    if (Array.isArray(detail)) {
      return detail.map(d => (typeof d === 'string' ? d : d.msg || JSON.stringify(d))).join('; ');
    }
    return typeof detail === 'string' ? detail : 'Ошибка импорта';
  };

  const handleProductsImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingProducts(true);
    setImportResultProd(null);
    try {
      const res = await importProducts(file);
      // Background task — poll for result
      if (res.data?.task_id) {
        const taskId = res.data.task_id;
        toast.success('Импорт запущен, обрабатывается...');
        setUploadingProducts(false);
        e.target.value = '';
        // Poll every 2s up to 60 attempts (2 min)
        let attempts = 0;
        const poll = setInterval(async () => {
          attempts++;
          try {
            const statusRes = await getTaskStatus(taskId);
            const { status, result, error } = statusRes.data;
            if (status === 'done') {
              clearInterval(poll);
              setImportResultProd(result);
              toast.success(`Импорт завершён: создано ${result.created} товаров`);
              if (result.errors?.length) toast.error(`Ошибок: ${result.errors.length}`);
            } else if (status === 'error') {
              clearInterval(poll);
              setImportResultProd({ errors: [error || 'Ошибка импорта'] });
              toast.error('Ошибка импорта');
            } else if (attempts >= 60) {
              clearInterval(poll);
              toast.error('Импорт занял слишком долго — проверьте товары вручную');
            }
          } catch {
            if (attempts >= 60) clearInterval(poll);
          }
        }, 2000);
      } else {
        // Fallback: sync response
        setImportResultProd(res.data);
        toast.success(`Создано ${res.data.created} товаров`);
        if (res.data.errors?.length) toast.error(`Ошибок: ${res.data.errors.length}`);
        setUploadingProducts(false);
        e.target.value = '';
      }
    } catch (err) {
      const msg = parseImportError(err);
      toast.error(msg);
      setImportResultProd({ errors: [msg] });
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
      const msg = parseImportError(err);
      toast.error(msg);
      setImportResultStrat({ errors: [msg] });
    } finally {
      setUploadingStrategy(false);
      e.target.value = '';
    }
  };

  const handleCharImport = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    setUploadingChar(true);
    setImportResultChar(null);
    try {
      const res = await importCharacteristics(file);
      setImportResultChar(res.data);
      toast.success(`Характеристики обновлены: ${res.data.updated} товаров`);
      if (res.data.errors?.length) toast.error(`Ошибок: ${res.data.errors.length}`);
    } catch (err) {
      const msg = parseImportError(err);
      toast.error(msg);
      setImportResultChar({ errors: [msg] });
    } finally {
      setUploadingChar(false);
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
        <h1 className="text-2xl font-bold text-foreground">Импорт / Экспорт</h1>
        <p className="text-muted-foreground mt-1 text-sm">
          Массовая загрузка товаров и стратегий, выгрузка данных в Excel
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">

        {/* ══ ШАБЛОНЫ ══ */}
        <div className="card">
          <SectionTitle
            icon={FileText}
            color="hsl(var(--primary))"
            title="Шаблоны"
            subtitle="Скачайте пустые файлы для заполнения"
          />

          <div className="space-y-3">
            {/* Products template */}
            <div className="flex items-center justify-between gap-3 p-3.5 rounded-xl border border-border bg-muted/30">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-primary/10">
                  <Package className="w-4 h-4 text-primary" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">Шаблон товаров</p>
                  <p className="text-xs text-muted-foreground">Артикул WB + ID кабинета обязательны</p>
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
            <div className="flex items-center justify-between gap-3 p-3.5 rounded-xl border border-border bg-muted/30">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0"
                     style={{ background: '#6366f115' }}>
                  <Settings2 className="w-4 h-4 text-indigo-500" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">Шаблон стратегий</p>
                  <p className="text-xs text-muted-foreground">12 колонок: МРЦ, РРЦ, конкурент…</p>
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

            {/* Characteristics template */}
            <div className="flex items-center justify-between gap-3 p-3.5 rounded-xl border border-border bg-muted/30">
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-8 h-8 rounded-lg flex items-center justify-center flex-shrink-0 bg-purple-100 dark:bg-purple-900/30">
                  <FileSpreadsheet className="w-4 h-4 text-purple-500" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground">Шаблон характеристик</p>
                  <p className="text-xs text-muted-foreground">Бренд + себестоимость + МРЦ</p>
                </div>
              </div>
              <button
                className="btn btn-secondary btn-sm gap-1.5 flex-shrink-0"
                onClick={handleDownloadCharacteristicsTemplate}
                disabled={loadingTplChar}
              >
                {loadingTplChar ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
                .xlsx
              </button>
            </div>
          </div>

          {/* Collapsible column descriptions */}
          <div className="mt-4 space-y-2">
            <details>
              <summary className="cursor-pointer text-xs text-primary font-medium select-none list-none flex items-center gap-1">
                <ChevronDown className="w-3 h-3" /> Колонки шаблона товаров
              </summary>
              <div className="mt-2 p-3 rounded-lg bg-muted/50 border border-border text-xs text-muted-foreground space-y-1.5">
                <div className="font-semibold text-foreground mb-1">Хотя бы одно из A или B обязательно:</div>
                <div><b>A — Артикул WB</b> — числовой артикул (если есть)</div>
                <div><b>B — Артикул продавца</b> — (если нет артикула WB, система найдёт его сама)</div>
                <div className="pt-1 border-t border-gray-200 mt-1"><b>C — ID аккаунта *</b> — из раздела Аккаунты (обязательно)</div>
                <div><b>D — Название</b> — <i>необязательно</i>, подтянется из WB автоматически</div>
              </div>
            </details>
            <details>
              <summary className="cursor-pointer text-xs text-primary font-medium select-none list-none flex items-center gap-1">
                <ChevronDown className="w-3 h-3" /> Колонки шаблона стратегий
              </summary>
              <div className="mt-2 p-3 rounded-lg bg-muted/50 border border-border text-xs text-muted-foreground space-y-1">
                <div><b>A</b> — Артикул продавца *</div>
                <div><b>B</b> — Стратегия: <code>rrp</code> (РРЦ) / <code>competitor</code> (Конкурент) *</div>
                <div><b>C</b> — Активен: <code>да</code> / <code>нет</code></div>
                <div><b>D</b> — МРЦ — мин. цена *</div>
                <div><b>E–H</b> — Макс. цена, РРЦ, допуск, кошелёк</div>
                <div><b>I</b> — артикул WB конкурента, <b>J</b> — тип смещения (rub/pct), <b>K</b> — направление (ниже/выше), <b>L</b> — смещение</div>
              </div>
            </details>
          </div>
        </div>

        {/* ══ ИМПОРТ ══ */}
        <div className="card">
          <SectionTitle
            icon={Upload}
            color="emerald-600"
            title="Импорт"
            subtitle="Загрузите заполненный Excel-файл"
          />

          <div className="space-y-4">
            {/* Import products */}
            <div
              className="p-4 rounded-xl border-2 border-dashed border-emerald-200 bg-emerald-50 dark:border-emerald-800 dark:bg-emerald-950/30"
            >
              <div className="flex items-center gap-2 mb-1">
                <Package className="w-4 h-4 text-emerald-600" />
                <span className="text-sm font-medium text-foreground">Товары</span>
                {uploadingProducts && <Loader2 className="w-3.5 h-3.5 animate-spin text-emerald-600 ml-auto" />}
              </div>
              <p className="text-xs text-muted-foreground mb-3">
                Достаточно артикула WB <b>или</b> артикула продавца + ID кабинета. Название и фото подтянутся автоматически.
              </p>
              <input ref={fileInputProducts} type="file" accept=".xlsx,.xls"
                     onChange={handleProductsImport} disabled={uploadingProducts} className="hidden" />
              <button
                className="btn btn-sm w-full gap-1.5 bg-emerald-600 hover:bg-emerald-700 text-white"
                onClick={() => fileInputProducts.current?.click()}
                disabled={uploadingProducts}
              >
                {uploadingProducts ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingProducts ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultProd} />

            {/* Import strategies */}
            <div className="p-4 rounded-xl border-2 border-dashed border-primary/30 bg-primary/5">
              <div className="flex items-center gap-2 mb-1">
                <Settings2 className="w-4 h-4 text-primary" />
                <span className="text-sm font-medium text-foreground">Стратегии</span>
                {uploadingStrategy && <Loader2 className="w-3.5 h-3.5 animate-spin text-primary ml-auto" />}
              </div>
              <p className="text-xs text-muted-foreground mb-3">
                Обновить параметры репрайсера по артикулу продавца
              </p>
              <input ref={fileInputStrategy} type="file" accept=".xlsx,.xls"
                     onChange={handleStrategyImport} disabled={uploadingStrategy} className="hidden" />
              <button
                className="btn btn-primary btn-sm w-full gap-1.5"
                onClick={() => fileInputStrategy.current?.click()}
                disabled={uploadingStrategy}
              >
                {uploadingStrategy ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingStrategy ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultStrat} />

            {/* Import characteristics */}
            <div
              className="p-4 rounded-xl border-2 border-dashed border-purple-200 bg-purple-50 dark:border-purple-800 dark:bg-purple-950/30"
            >
              <div className="flex items-center gap-2 mb-1">
                <FileSpreadsheet className="w-4 h-4 text-purple-600" />
                <span className="text-sm font-medium text-foreground">Характеристики</span>
                {uploadingChar && <Loader2 className="w-3.5 h-3.5 animate-spin text-purple-600 ml-auto" />}
              </div>
              <p className="text-xs text-muted-foreground mb-3">
                Бренд, себестоимость, МРЦ — любые поля сразу
              </p>
              <input ref={fileInputChar} type="file" accept=".xlsx,.xls"
                     onChange={handleCharImport} disabled={uploadingChar} className="hidden" />
              <button
                className="btn btn-sm w-full gap-1.5 bg-purple-600 hover:bg-purple-700 text-white"
                onClick={() => fileInputChar.current?.click()}
                disabled={uploadingChar}
              >
                {uploadingChar ? <Spin /> : <Upload className="w-3.5 h-3.5" />}
                {uploadingChar ? 'Загрузка…' : 'Выбрать файл'}
              </button>
            </div>
            <ImportResult result={importResultChar} />
          </div>
        </div>

        {/* ══ ЭКСПОРТ ══ */}
        <div className="card">
          <SectionTitle
            icon={Download}
          color="blue-600"
            title="Экспорт"
            subtitle="Скачайте данные из системы"
          />

          {/* Products export with filters */}
          <div
            className="p-4 rounded-xl border border-blue-200 bg-blue-50 dark:border-blue-800 dark:bg-blue-950/30"
          >
            <div className="flex items-center gap-2 mb-2">
              <FileSpreadsheet className="w-4 h-4 text-blue-600" />
              <span className="text-sm font-medium text-foreground">Все товары + стратегии</span>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              18 колонок: аккаунт, цены, МРЦ, РРЦ, стратегия. Строки раскрашены по статусу.
            </p>

            {/* Filters */}
            <div
              className="p-3 rounded-lg mb-3 space-y-2 bg-blue-50/60 dark:bg-blue-950/30 border border-blue-100 dark:border-blue-800"
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
            className="p-4 rounded-xl border border-amber-200 bg-amber-50 dark:border-amber-800 dark:bg-amber-950/30"
          >
            <div className="flex items-center gap-2 mb-1">
              <FileText className="w-4 h-4 text-amber-600" />
              <span className="text-sm font-medium text-foreground">История цен</span>
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              Все снимки цен: цена продавца, финальная цена, СПП, дата
            </p>
            <button
              className="btn btn-sm w-full gap-1.5 bg-amber-600 hover:bg-amber-700 text-white"
              onClick={handleExportHistory}
              disabled={loadingExpHistory}
            >
              {loadingExpHistory ? <Spin /> : <ArrowDown className="w-3.5 h-3.5" />}
              {loadingExpHistory ? 'Формирую…' : 'Скачать xlsx'}
            </button>
          </div>

          {/* Status legend */}
          <div className="mt-4 pt-4 border-t dark:border-gray-700">
            <p className="text-xs font-medium text-muted-foreground mb-2">Цвета строк в экспорте:</p>
            <div className="space-y-1.5">
              {[
                { bg: 'bg-green-100 dark:bg-green-900/30', border: 'border-green-200 dark:border-green-800', label: 'Активен' },
                { bg: 'bg-orange-100 dark:bg-orange-900/30', border: 'border-orange-200 dark:border-orange-800', label: 'Отключен' },
                { bg: 'bg-gray-100 dark:bg-gray-800/30', border: 'border-gray-200 dark:border-gray-700', label: 'Без стратегии' },
              ].map(({ bg, border, label }) => (
                <div key={label} className="flex items-center gap-2">
                  <div className={`w-4 h-4 rounded border flex-shrink-0 ${bg} ${border}`} />
                  <span className="text-xs text-muted-foreground">{label}</span>
                </div>
              ))}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

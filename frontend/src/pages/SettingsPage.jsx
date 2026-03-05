import { useEffect, useState } from 'react';
import { Clock, Save, Wallet, TrendingUp } from 'lucide-react';
import toast from 'react-hot-toast';
import { getSettings, updateSettings } from '../api/client';

const INTERVAL_OPTIONS = [
  { value: 1,    label: '1 минута (тест)' },
  { value: 5,    label: '5 минут' },
  { value: 10,   label: '10 минут' },
  { value: 15,   label: '15 минут' },
  { value: 20,   label: '20 минут' },
  { value: 30,   label: '30 минут' },
  { value: 60,   label: '1 час' },
  { value: 120,  label: '2 часа' },
  { value: 180,  label: '3 часа' },
  { value: 360,  label: '6 часов' },
  { value: 720,  label: '12 часов' },
  { value: 1440, label: '24 часа' },
];

function FeeRow({ label, type, value, onTypeChange, onValueChange }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-gray-700 w-28 flex-shrink-0">{label}</span>
      <select
        value={type}
        onChange={e => onTypeChange(e.target.value)}
        className="input text-sm"
        style={{ width: '70px', flexShrink: 0 }}
      >
        <option value="pct">%</option>
        <option value="rub">₽</option>
      </select>
      <input
        type="number"
        min="0"
        step="0.1"
        value={value}
        onChange={e => onValueChange(parseFloat(e.target.value) || 0)}
        className="input text-sm flex-1"
        placeholder="0"
      />
    </div>
  );
}

export default function SettingsPage() {
  const [interval, setInterval] = useState(60);
  const [walletPct, setWalletPct] = useState(3.0);
  const [commissionType,  setCommissionType]  = useState('pct');
  const [commissionValue, setCommissionValue] = useState(0.0);
  const [logisticsType,   setLogisticsType]   = useState('rub');
  const [logisticsValue,  setLogisticsValue]  = useState(0.0);
  const [acquiringType,   setAcquiringType]   = useState('pct');
  const [acquiringValue,  setAcquiringValue]  = useState(0.0);
  const [minMarginPct,    setMinMarginPct]    = useState(0.0);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    loadSettings();
  }, []);

  const loadSettings = async () => {
    try {
      const res = await getSettings();
      const d = res.data;
      setInterval(d.repricer_interval_minutes);
      setWalletPct(d.wb_wallet_discount_pct);
      setCommissionType(d.commission_type  || 'pct');
      setCommissionValue(d.commission_value ?? 0);
      setLogisticsType(d.logistics_type   || 'rub');
      setLogisticsValue(d.logistics_value  ?? 0);
      setAcquiringType(d.acquiring_type   || 'pct');
      setAcquiringValue(d.acquiring_value  ?? 0);
      setMinMarginPct(d.min_margin_pct ?? 0);
    } catch {
      toast.error('Ошибка загрузки настроек');
    } finally {
      setLoading(false);
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await updateSettings({
        repricer_interval_minutes: interval,
        wb_wallet_discount_pct: walletPct,
        commission_type:  commissionType,
        commission_value: commissionValue,
        logistics_type:   logisticsType,
        logistics_value:  logisticsValue,
        acquiring_type:   acquiringType,
        acquiring_value:  acquiringValue,
        min_margin_pct:   minMarginPct,
      });
      toast.success('Настройки сохранены');
    } catch {
      toast.error('Ошибка сохранения настроек');
    } finally {
      setSaving(false);
    }
  };

  // Живой пример маржи при цене 5000₽ и себестоимости 2000₽
  const examplePrice = 5000;
  const exampleCost  = 2000;
  const calcFee = (type, value) =>
    type === 'pct' ? examplePrice * value / 100 : value;
  const totalFees = calcFee(commissionType, commissionValue)
    + calcFee(logisticsType, logisticsValue)
    + calcFee(acquiringType, acquiringValue);
  const exampleMargin = exampleCost > 0
    ? (((examplePrice - exampleCost - totalFees) / exampleCost) * 100).toFixed(1)
    : '—';

  const exampleFinal  = 23000;
  const exampleWallet = Math.round(exampleFinal * (1 - walletPct / 100));

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-gray-600">Загрузка...</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900">Настройки</h1>
        <p className="text-gray-600 mt-2">Параметры работы репрайсера</p>
      </div>

      <div className="max-w-4xl">
      <div className="grid gap-6 items-start" style={{ gridTemplateColumns: '1fr 1.4fr' }}>

        {/* Левая колонка: Интервал + Кошелёк */}
        <div className="space-y-6">

        {/* Интервал */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-primary-50 p-2 rounded-lg">
              <Clock className="w-5 h-5 text-primary-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Интервал обновления цен</h2>
              <p className="text-sm text-gray-500">Как часто репрайсер будет проверять и менять цены</p>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Периодичность запуска
            </label>
            <select
              value={interval}
              onChange={(e) => setInterval(Number(e.target.value))}
              className="input w-full"
            >
              {INTERVAL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-500 mt-2">
              Изменение вступает в силу в течение 1 минуты без перезапуска.
            </p>
          </div>
        </div>

        {/* WB Кошелёк */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-green-50 p-2 rounded-lg">
              <Wallet className="w-5 h-5 text-green-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Скидка WB Кошелька</h2>
              <p className="text-sm text-gray-500">Дополнительная скидка для владельцев WB Кошелька</p>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Процент скидки (%)
            </label>
            <input
              type="number"
              min="0"
              max="10"
              step="0.1"
              value={walletPct}
              onChange={(e) => setWalletPct(parseFloat(e.target.value) || 0)}
              className="input w-full"
            />
            <p className="text-xs text-gray-500 mt-2">
              Пример: цена после СПП {exampleFinal.toLocaleString('ru')}₽ → с кошельком{' '}
              <span className="font-medium text-green-600">{exampleWallet.toLocaleString('ru')}₽</span>
            </p>
            <p className="text-xs text-gray-400 mt-1">
              Используется только для отображения в таблице товаров. На расчёт РРЦ не влияет.
            </p>
          </div>
        </div>

        </div>{/* /Левая колонка */}

        {/* Правая колонка: Расчёт маржи */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="p-2 rounded-lg" style={{ background: '#00f0ff18' }}>
              <TrendingUp className="w-5 h-5" style={{ color: '#00b8c8' }} />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-gray-900">Расходы для расчёта маржи</h2>
              <p className="text-sm text-gray-500">
                Маржа = (Цена − Расходы) / Себестоимость × 100%
              </p>
            </div>
          </div>

          <div className="space-y-3 mb-4">
            <FeeRow
              label="Комиссия WB"
              type={commissionType}
              value={commissionValue}
              onTypeChange={setCommissionType}
              onValueChange={setCommissionValue}
            />
            <FeeRow
              label="Логистика"
              type={logisticsType}
              value={logisticsValue}
              onTypeChange={setLogisticsType}
              onValueChange={setLogisticsValue}
            />
            <FeeRow
              label="Эквайринг"
              type={acquiringType}
              value={acquiringValue}
              onTypeChange={setAcquiringType}
              onValueChange={setAcquiringValue}
            />
          </div>

          {/* Живой пример */}
          <div
            className="p-3 rounded-xl text-xs space-y-1"
            style={{ background: '#f0fdf4', border: '1px solid #bbf7d0' }}
          >
            <p className="font-semibold text-gray-700">Пример (цена {examplePrice.toLocaleString('ru')}₽, себестоимость {exampleCost.toLocaleString('ru')}₽):</p>
            <p className="text-gray-500">
              Осн. цена (продавца): <b>{examplePrice.toLocaleString('ru')}₽</b>
            </p>
            <p className="text-gray-500">
              − Себестоимость: <b>{exampleCost.toLocaleString('ru')}₽</b>
            </p>
            <p className="text-gray-500">
              − Расходы: <b>{totalFees.toFixed(2)}₽</b>
            </p>
            <p className="text-gray-500 border-t border-green-200 pt-1">
              Остаток: <b>{(examplePrice - exampleCost - totalFees).toFixed(2)}₽</b>
            </p>
            <p className="text-gray-500">
              ÷ Себестоимость: <b>{exampleCost.toLocaleString('ru')}₽</b>
            </p>
            <p className="font-bold pt-0.5" style={{ color: Number(exampleMargin) >= 0 ? '#16a34a' : '#dc2626' }}>
              = Маржа: {exampleMargin}%
            </p>
          </div>

          <p className="text-xs text-gray-400 mt-3">
            Себестоимость товаров загружается через раздел Импорт / Экспорт.
          </p>

          {/* Мин. маржа */}
          <div className="mt-4 pt-4" style={{ borderTop: '1px solid #bbf7d0' }}>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Мин. маржа — Margin Guard (%)
            </label>
            <input
              type="number"
              min="0"
              step="0.5"
              value={minMarginPct}
              onChange={e => setMinMarginPct(parseFloat(e.target.value) || 0)}
              className="input w-full text-sm"
              placeholder="0"
            />
            <p className="text-xs text-gray-400 mt-1">
              Репрайсер не опустит цену ниже уровня при котором маржа меньше этого порога.
              0 = выключено.
            </p>
          </div>
        </div>{/* /Правая колонка */}

      </div>{/* /grid */}

        <button
          onClick={handleSave}
          disabled={saving}
          className="btn btn-primary flex items-center space-x-2 mt-6 justify-center"
          style={{ minWidth: 220 }}
        >
          <Save className="w-4 h-4" />
          <span>{saving ? 'Сохранение...' : 'Сохранить настройки'}</span>
        </button>
      </div>
    </div>
  );
}

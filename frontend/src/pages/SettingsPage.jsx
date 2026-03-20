import { useEffect, useState } from 'react';
import { Save, Wallet, TrendingUp, User, KeyRound, Eye, EyeOff } from 'lucide-react';
import toast from 'react-hot-toast';
import { getSettings, updateSettings, changePasswordSendCode, changePasswordConfirm } from '../api/client';
import { useAuthStore } from '../store';

function FeeRow({ label, type, value, onTypeChange, onValueChange }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-sm text-foreground w-28 flex-shrink-0">{label}</span>
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

// ─── Блок смены пароля ────────────────────────────────────────────────────────
function ChangePasswordBlock({ userEmail }) {
  // step: 'idle' | 'code_sent' | 'done'
  const [step, setStep] = useState('idle');
  const [sending, setSending] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [code, setCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [showPass, setShowPass] = useState(false);

  const handleSendCode = async () => {
    setSending(true);
    try {
      await changePasswordSendCode();
      setStep('code_sent');
      toast.success('Код отправлен на ' + userEmail);
    } catch (err) {
      const detail = err.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Ошибка отправки кода');
    } finally {
      setSending(false);
    }
  };

  const handleConfirm = async () => {
    if (!code || code.length !== 6) {
      toast.error('Введите 6-значный код из письма');
      return;
    }
    if (!newPassword || newPassword.length < 8) {
      toast.error('Пароль должен быть не менее 8 символов');
      return;
    }
    setConfirming(true);
    try {
      await changePasswordConfirm(code, newPassword);
      setStep('done');
      toast.success('Пароль успешно изменён!');
    } catch (err) {
      const detail = err.response?.data?.detail;
      toast.error(typeof detail === 'string' ? detail : 'Ошибка подтверждения');
    } finally {
      setConfirming(false);
    }
  };

  const handleReset = () => {
    setStep('idle');
    setCode('');
    setNewPassword('');
    setShowPass(false);
  };

  return (
    <div className="card mb-6">
      <div className="flex items-center space-x-3 mb-5">
        <div className="bg-primary/10 p-2 rounded-lg">
          <User className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h2 className="text-lg font-semibold text-foreground">Профиль</h2>
          <p className="text-sm text-muted-foreground">Данные вашего аккаунта</p>
        </div>
      </div>

      {/* Email */}
      <div className="mb-5">
        <label className="block text-sm font-medium text-muted-foreground mb-1">Почта</label>
        <div className="px-3 py-2 rounded-lg text-sm font-medium text-foreground bg-muted border border-border">
          {userEmail}
        </div>
      </div>

      {/* Separator */}
      <div className="border-t border-border mb-5" />

      {/* Смена пароля */}
      <div className="flex items-center space-x-2 mb-4">
        <KeyRound className="w-4 h-4 text-muted-foreground" />
        <span className="text-sm font-medium text-foreground">Смена пароля</span>
      </div>

      {step === 'idle' && (
        <div>
          <p className="text-sm text-muted-foreground mb-3">
            На вашу почту будет отправлен 6-значный код подтверждения.
          </p>
          <button
            onClick={handleSendCode}
            disabled={sending}
            className="btn btn-primary text-sm"
            style={{ minWidth: 200 }}
          >
            {sending ? 'Отправка...' : 'Получить код на почту'}
          </button>
        </div>
      )}

      {step === 'code_sent' && (
        <div className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Код отправлен на <b>{userEmail}</b>. Введите его и новый пароль:
          </p>

          {/* Код */}
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Код из письма</label>
            <input
              type="text"
              maxLength={6}
              value={code}
              onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
              placeholder="123456"
              className="input w-full font-mono tracking-widest text-center text-lg"
              style={{ maxWidth: 160 }}
            />
          </div>

          {/* Новый пароль */}
          <div>
            <label className="block text-xs font-medium text-muted-foreground mb-1">Новый пароль</label>
            <div className="relative" style={{ maxWidth: 300 }}>
              <input
                type={showPass ? 'text' : 'password'}
                value={newPassword}
                onChange={e => setNewPassword(e.target.value)}
                placeholder="Минимум 8 символов"
                className="input w-full pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPass(v => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
              >
                {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>

          <div className="flex items-center gap-3 pt-1">
            <button
              onClick={handleConfirm}
              disabled={confirming}
              className="btn btn-primary text-sm"
              style={{ minWidth: 180 }}
            >
              {confirming ? 'Проверка...' : 'Сменить пароль'}
            </button>
            <button
              onClick={handleSendCode}
              disabled={sending}
              className="text-sm text-muted-foreground hover:text-foreground underline"
            >
              {sending ? 'Отправка...' : 'Отправить код снова'}
            </button>
          </div>
        </div>
      )}

      {step === 'done' && (
        <div className="flex items-center gap-3">
          <div className="text-sm font-medium px-4 py-2 rounded-lg bg-green-50 text-green-700 border border-green-200 dark:bg-green-950/20 dark:text-green-400 dark:border-green-900">
            ✅ Пароль успешно изменён
          </div>
          <button onClick={handleReset} className="text-sm text-muted-foreground hover:text-foreground underline">
            Сменить ещё раз
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Основная страница настроек ────────────────────────────────────────────────
export default function SettingsPage() {
  const { user } = useAuthStore();
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
        <div className="text-muted-foreground">Загрузка...</div>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-foreground">Настройки</h1>
        <p className="text-muted-foreground mt-2">Параметры работы репрайсера</p>
      </div>

      <div className="max-w-4xl">

      {/* Профиль и смена пароля */}
      <ChangePasswordBlock userEmail={user?.email || ''} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">

        {/* Расходы для расчёта маржи */}
        <div className="card">
          <div className="flex items-center space-x-3 mb-6">
            <div className="bg-cyan-50 p-2 rounded-lg dark:bg-cyan-950/20">
              <TrendingUp className="w-5 h-5 text-cyan-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-foreground">Расходы для расчёта маржи</h2>
              <p className="text-sm text-muted-foreground">
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
          <div className="p-3 rounded-xl text-xs space-y-1 bg-green-50 border border-green-200 dark:bg-green-950/20 dark:border-green-900">
            <p className="font-semibold text-foreground">Пример (цена {examplePrice.toLocaleString('ru')}₽, себестоимость {exampleCost.toLocaleString('ru')}₽):</p>
            <p className="text-muted-foreground">
              Осн. цена (продавца): <b>{examplePrice.toLocaleString('ru')}₽</b>
            </p>
            <p className="text-muted-foreground">
              − Себестоимость: <b>{exampleCost.toLocaleString('ru')}₽</b>
            </p>
            <p className="text-muted-foreground">
              − Расходы: <b>{totalFees.toFixed(2)}₽</b>
            </p>
            <p className="text-muted-foreground border-t border-green-200 pt-1">
              Остаток: <b>{(examplePrice - exampleCost - totalFees).toFixed(2)}₽</b>
            </p>
            <p className="text-muted-foreground">
              ÷ Себестоимость: <b>{exampleCost.toLocaleString('ru')}₽</b>
            </p>
            <p className="font-bold pt-0.5" style={{ color: Number(exampleMargin) >= 0 ? '#16a34a' : '#dc2626' }}>
              = Маржа: {exampleMargin}%
            </p>
          </div>

          <p className="text-xs text-muted-foreground mt-3">
            Себестоимость товаров загружается через раздел Импорт / Экспорт.
          </p>
        </div>

        {/* Правая колонка: Кошелёк + Margin Guard */}
        <div className="space-y-6">

          {/* WB Кошелёк */}
          <div className="card">
            <div className="flex items-center space-x-3 mb-6">
              <div className="bg-green-50 p-2 rounded-lg dark:bg-green-950/20">
                <Wallet className="w-5 h-5 text-green-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">Скидка WB Кошелька</h2>
                <p className="text-sm text-muted-foreground">Дополнительная скидка для владельцев WB Кошелька</p>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">
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
              <p className="text-xs text-muted-foreground mt-2">
                Пример: цена после СПП {exampleFinal.toLocaleString('ru')}₽ → с кошельком{' '}
                <span className="font-medium text-green-600">{exampleWallet.toLocaleString('ru')}₽</span>
              </p>
              <p className="text-xs text-muted-foreground mt-1">
                Используется только для отображения в таблице товаров. На расчёт РРЦ не влияет.
              </p>
            </div>
          </div>

          {/* Мин. маржа — Margin Guard */}
          <div className="card">
            <div className="flex items-center space-x-3 mb-6">
              <div className="bg-amber-50 p-2 rounded-lg dark:bg-amber-950/20">
                <TrendingUp className="w-5 h-5 text-amber-600" />
              </div>
              <div>
                <h2 className="text-lg font-semibold text-foreground">Margin Guard</h2>
                <p className="text-sm text-muted-foreground">Минимальная допустимая маржа</p>
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-1">
                Мин. маржа (%)
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
              <p className="text-xs text-muted-foreground mt-2">
                Репрайсер не опустит цену ниже уровня при котором маржа меньше этого порога.
                0 = выключено.
              </p>
            </div>
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

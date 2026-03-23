import { useState, useEffect } from 'react';
import { X, Plus, Trash2 } from 'lucide-react';
import toast from 'react-hot-toast';
import { createProductConfig, updateProductConfig, getSettings } from '../api/client';


// Инициализация списка конкурентов из конфига
function initCompetitors(config) {
  if (config?.competitors && config.competitors.length > 0) {
    return config.competitors.map(c => ({ nm_id: String(c.nm_id), alias: c.alias || '' }));
  }
  if (config?.competitor_nm_id) {
    return [{ nm_id: String(config.competitor_nm_id), alias: '' }];
  }
  return [{ nm_id: '', alias: '' }];
}

export default function ConfigModal({ product, onClose }) {
  const [strategy, setStrategy] = useState(product.config?.strategy || 'rrp');
  const [formData, setFormData] = useState({
    is_active: product.config?.is_active ?? true,
    min_price: product.config?.min_price || '',
    max_price: product.config?.max_price || '',
    rrp_price: product.config?.rrp_price || '',
    rrp_tolerance: product.config?.rrp_tolerance || '5',
    rrp_consider_wallet: product.config?.rrp_consider_wallet ?? false,
    competitor_offset_type: product.config?.competitor_offset_type || 'rub',
    competitor_offset_direction: product.config?.competitor_offset_direction || 'below',
    competitor_offset_value: product.config?.competitor_offset_value || '',
    competitor_select_rule: product.config?.competitor_select_rule || 'min_price',
  });
  const [competitors, setCompetitors] = useState(() => initCompetitors(product.config));
  const [walletPct, setWalletPct] = useState(3.0);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});

  useEffect(() => {
    getSettings()
      .then((res) => setWalletPct(res.data.wb_wallet_discount_pct))
      .catch(() => {});
  }, []);

  const validate = () => {
    const errs = {};
    const minP = parseFloat(formData.min_price);
    const maxP = parseFloat(formData.max_price);

    if (!formData.min_price || minP < 0) {
      errs.min_price = 'Мин. цена должна быть >= 0';
    }
    if (formData.max_price && maxP <= 0) {
      errs.max_price = 'Макс. цена должна быть > 0';
    }
    if (formData.min_price && formData.max_price && maxP <= minP) {
      errs.max_price = 'Макс. цена должна быть больше мин. цены';
    }

    if (strategy === 'rrp') {
      const rrp = parseFloat(formData.rrp_price);
      const tol = parseFloat(formData.rrp_tolerance);
      if (!formData.rrp_price || rrp <= 0) {
        errs.rrp_price = 'РРЦ должна быть > 0';
      }
      if (formData.rrp_tolerance && tol < 0) {
        errs.rrp_tolerance = 'Допуск не может быть отрицательным';
      }
      if (rrp && minP && rrp < minP) {
        errs.rrp_price = 'РРЦ не может быть ниже мин. цены';
      }
      if (rrp && maxP && rrp > maxP) {
        errs.rrp_price = 'РРЦ не может быть выше макс. цены';
      }
    } else {
      const validCompetitors = competitors.filter(c => c.nm_id && parseInt(c.nm_id) > 0);
      if (validCompetitors.length === 0) {
        errs.competitors = 'Добавьте хотя бы одного конкурента';
      }
      if (!formData.competitor_offset_value || parseFloat(formData.competitor_offset_value) <= 0) {
        errs.competitor_offset_value = 'Смещение должно быть > 0';
      }
    }

    setErrors(errs);
    return Object.keys(errs).length === 0;
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!validate()) return;
    setLoading(true);
    try {
      const payload = {
        strategy,
        is_active: formData.is_active,
        min_price: parseFloat(formData.min_price),
        max_price: formData.max_price ? parseFloat(formData.max_price) : null,
      };

      if (strategy === 'rrp') {
        payload.rrp_price = parseFloat(formData.rrp_price);
        payload.rrp_tolerance = parseFloat(formData.rrp_tolerance);
        payload.rrp_consider_wallet = formData.rrp_consider_wallet;
      } else {
        const validCompetitors = competitors
          .filter(c => c.nm_id && parseInt(c.nm_id) > 0)
          .map(c => ({ nm_id: parseInt(c.nm_id), alias: c.alias || null }));
        payload.competitors = validCompetitors;
        payload.competitor_select_rule = formData.competitor_select_rule;
        payload.competitor_offset_type = formData.competitor_offset_type;
        payload.competitor_offset_direction = formData.competitor_offset_direction;
        payload.competitor_offset_value = parseFloat(formData.competitor_offset_value);
      }

      if (product.config) {
        await updateProductConfig(product.id, payload);
        toast.success('Конфигурация обновлена');
      } else {
        await createProductConfig(product.id, payload);
        toast.success('Конфигурация создана');
      }

      onClose();
    } catch (error) {
      toast.error(error.response?.data?.detail || 'Ошибка сохранения');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="modal-overlay">
      <div className="modal-box max-w-2xl">
        <div className="modal-header">
          <div>
            <h2 className="text-base font-semibold text-foreground">Настройка репрайсера</h2>
            <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
              {product.seller_sku} — {product.name}
            </p>
          </div>
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1">
            <X className="w-5 h-5" />
          </button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-5">
          {/* Active toggle */}
          <label className="flex items-center gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={formData.is_active}
              onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              className="w-4 h-4 accent-violet-500"
            />
            <span className="text-sm font-medium text-foreground">Репрайсер активен</span>
          </label>

          {/* Strategy selector */}
          <div>
            <p className="text-sm font-medium mb-2" style={{ color: 'var(--text-2)' }}>
              Стратегия ценообразования
            </p>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'rrp', label: 'РРЦ', desc: 'Диапазон вокруг рекомендованной розничной цены' },
                { id: 'competitor', label: 'Конкурент', desc: 'Привязка к цене конкурента по nmID' },
              ].map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => setStrategy(s.id)}
                  className={`p-3.5 rounded-lg text-left transition-all ${strategy === s.id
                    ? 'border border-primary/40 bg-primary/5'
                    : 'border border-border bg-muted/30'}`}
                >
                  <div className="font-medium text-sm text-foreground">{s.label}</div>
                  <div className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>{s.desc}</div>
                </button>
              ))}
            </div>
          </div>

          {/* Price Limits */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
                Мин. цена (₽) <span className="text-red-400">*</span>
              </label>
              <input
                type="number"
                step="0.01"
                value={formData.min_price}
                onChange={(e) => { setFormData({ ...formData, min_price: e.target.value }); setErrors((p) => ({ ...p, min_price: undefined })); }}
                className="input"
                placeholder="0 для отключения"
                required
                style={errors.min_price ? { borderColor: '#ef4444' } : {}}
              />
              {errors.min_price && <p className="text-xs mt-1 text-red-500">{errors.min_price}</p>}
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>
                Макс. цена (₽)
              </label>
              <input
                type="number"
                step="0.01"
                value={formData.max_price}
                onChange={(e) => { setFormData({ ...formData, max_price: e.target.value }); setErrors((p) => ({ ...p, max_price: undefined })); }}
                className="input"
                placeholder="Не ограничено"
                style={errors.max_price ? { borderColor: '#ef4444' } : {}}
              />
              {errors.max_price && <p className="text-xs mt-1 text-red-500">{errors.max_price}</p>}
            </div>
          </div>

          {/* RRP params */}
          {strategy === 'rrp' && (
            <div className="p-4 rounded-lg space-y-4 bg-muted/30 border border-border">
              <p className="text-sm font-medium text-foreground">Параметры РРЦ</p>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>
                    РРЦ (₽) <span className="text-red-400">*</span>
                  </label>
                  <input
                    type="number"
                    step="0.01"
                    value={formData.rrp_price}
                    onChange={(e) => { setFormData({ ...formData, rrp_price: e.target.value }); setErrors((p) => ({ ...p, rrp_price: undefined })); }}
                    className="input"
                    placeholder="2500"
                    required
                    style={errors.rrp_price ? { borderColor: '#ef4444' } : {}}
                  />
                  {errors.rrp_price && <p className="text-xs mt-1 text-red-500">{errors.rrp_price}</p>}
                </div>
                <div>
                  <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Допуск (₽)</label>
                  <input
                    type="number"
                    step="0.01"
                    value={formData.rrp_tolerance}
                    onChange={(e) => { setFormData({ ...formData, rrp_tolerance: e.target.value }); setErrors((p) => ({ ...p, rrp_tolerance: undefined })); }}
                    className="input"
                    placeholder="5"
                    style={errors.rrp_tolerance ? { borderColor: '#ef4444' } : {}}
                  />
                  {errors.rrp_tolerance && <p className="text-xs mt-1 text-red-500">{errors.rrp_tolerance}</p>}
                  <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                    Диапазон: {formData.rrp_price && formData.rrp_tolerance
                      ? `${parseFloat(formData.rrp_price) - parseFloat(formData.rrp_tolerance)}₽ — ${parseFloat(formData.rrp_price) + parseFloat(formData.rrp_tolerance)}₽`
                      : '—'}
                  </p>
                </div>
              </div>

              {/* Wallet checkbox */}
              <label className="flex items-start gap-2.5 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={formData.rrp_consider_wallet}
                  onChange={(e) => setFormData({ ...formData, rrp_consider_wallet: e.target.checked })}
                  className="w-4 h-4 mt-0.5 accent-violet-500 flex-shrink-0"
                />
                <div>
                  <span className="text-sm font-medium text-foreground">
                    Учитывать скидку кошелька WB ({walletPct}%)
                  </span>
                  <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>
                    РРЦ будет ценой после СПП <strong>и</strong> кошелька, а не только после СПП
                  </p>
                </div>
              </label>

              {/* Wallet-aware example */}
              {formData.rrp_price && (
                <div
                  className="text-xs p-3 rounded bg-primary/5 border border-primary/20 text-muted-foreground"
                >
                  {formData.rrp_consider_wallet ? (
                    <>
                      <strong className="text-foreground">Расчёт с кошельком:</strong>{' '}
                      РРЦ {parseFloat(formData.rrp_price)}₽ = цена после СПП и кошелька.
                      {' '}Целевая цена до кошелька:{' '}
                      <strong className="text-primary">
                        {(parseFloat(formData.rrp_price) / (1 - walletPct / 100)).toFixed(2)}₽
                      </strong>
                      {' '}(кошелёк {walletPct}% уже учтён)
                    </>
                  ) : (
                    <>
                      <strong className="text-foreground">Расчёт без кошелька:</strong>{' '}
                      РРЦ {parseFloat(formData.rrp_price)}₽ = цена после СПП.
                      {' '}Фактическая цена с кошельком:{' '}
                      <strong className="text-primary">
                        {(parseFloat(formData.rrp_price) * (1 - walletPct / 100)).toFixed(2)}₽
                      </strong>
                      {' '}(кошелёк {walletPct}% сверху)
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {/* Competitor params */}
          {strategy === 'competitor' && (
            <div className="p-4 rounded-lg space-y-4 bg-muted/30 border border-border">
              <p className="text-sm font-medium text-foreground">Конкуренты</p>

              {/* Список конкурентов */}
              <div className="space-y-2">
                {competitors.map((c, idx) => (
                  <div key={idx} className="flex gap-2 items-center">
                    <input
                      type="number"
                      value={c.nm_id}
                      onChange={e => {
                        const next = [...competitors];
                        next[idx] = { ...next[idx], nm_id: e.target.value };
                        setCompetitors(next);
                        setErrors(p => ({ ...p, competitors: undefined }));
                      }}
                      className="input flex-1"
                      placeholder="nmID (напр. 123456789)"
                    />
                    <input
                      type="text"
                      value={c.alias}
                      onChange={e => {
                        const next = [...competitors];
                        next[idx] = { ...next[idx], alias: e.target.value };
                        setCompetitors(next);
                      }}
                      className="input"
                      style={{ width: '120px' }}
                      placeholder="Метка"
                    />
                    <button
                      type="button"
                      onClick={() => setCompetitors(competitors.filter((_, i) => i !== idx))}
                      className="btn btn-ghost btn-sm p-1.5"
                      style={{ color: '#ef4444' }}
                      disabled={competitors.length === 1}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
                {errors.competitors && <p className="text-xs text-red-500">{errors.competitors}</p>}
                <button
                  type="button"
                  onClick={() => setCompetitors([...competitors, { nm_id: '', alias: '' }])}
                  className="btn btn-secondary btn-sm gap-1.5 text-xs"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Добавить конкурента
                </button>
              </div>

              {/* Правило выбора */}
              <div>
                <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>
                  Правило выбора цены
                </label>
                <select
                  value={formData.competitor_select_rule}
                  onChange={e => setFormData({ ...formData, competitor_select_rule: e.target.value })}
                  className="input w-full"
                >
                  <option value="min_price">Минимальная среди всех</option>
                  <option value="cheapest_instock">Минимальная среди тех кто в наличии</option>
                  <option value="avg_price">Средняя цена</option>
                </select>
              </div>

              {/* Смещение */}
              <div className="grid grid-cols-3 gap-3">
                <div>
                  <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Направление</label>
                  <select
                    value={formData.competitor_offset_direction}
                    onChange={(e) => setFormData({ ...formData, competitor_offset_direction: e.target.value })}
                    className="input"
                  >
                    <option value="below">Ниже</option>
                    <option value="above">Выше</option>
                  </select>
                </div>
                <div>
                  <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Смещение</label>
                  <input
                    type="number"
                    step="0.01"
                    value={formData.competitor_offset_value}
                    onChange={(e) => { setFormData({ ...formData, competitor_offset_value: e.target.value }); setErrors((p) => ({ ...p, competitor_offset_value: undefined })); }}
                    className="input"
                    placeholder="50"
                    style={errors.competitor_offset_value ? { borderColor: '#ef4444' } : {}}
                  />
                  {errors.competitor_offset_value && <p className="text-xs mt-1 text-red-500">{errors.competitor_offset_value}</p>}
                </div>
                <div>
                  <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Тип</label>
                  <select
                    value={formData.competitor_offset_type}
                    onChange={(e) => setFormData({ ...formData, competitor_offset_type: e.target.value })}
                    className="input"
                  >
                    <option value="rub">Рубли</option>
                    <option value="pct">Проценты</option>
                  </select>
                </div>
              </div>

              <div className="text-xs p-3 rounded bg-primary/5 border border-primary/20 text-muted-foreground">
                <strong className="text-foreground">Пример:</strong> Если цена конкурента 1000₽, наша цена будет{' '}
                <strong className="text-primary">
                  {formData.competitor_offset_value
                    ? formData.competitor_offset_type === 'rub'
                      ? formData.competitor_offset_direction === 'below'
                        ? `${1000 - parseFloat(formData.competitor_offset_value)}₽`
                        : `${1000 + parseFloat(formData.competitor_offset_value)}₽`
                      : formData.competitor_offset_direction === 'below'
                        ? `${(1000 * (1 - parseFloat(formData.competitor_offset_value) / 100)).toFixed(0)}₽`
                        : `${(1000 * (1 + parseFloat(formData.competitor_offset_value) / 100)).toFixed(0)}₽`
                    : '—'}
                </strong>
              </div>
            </div>
          )}

          <div className="flex justify-end gap-2 pt-4 border-t border-border">
            <div className="flex gap-2">
              <button type="button" onClick={onClose} className="btn btn-secondary">
                Отмена
              </button>
              <button type="submit" disabled={loading} className="btn btn-primary">
                {loading ? 'Сохранение...' : 'Сохранить'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}

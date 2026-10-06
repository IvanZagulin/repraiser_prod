import { useState, useEffect, useCallback } from 'react';
import { X, Plus, Trash2, TrendingUp } from 'lucide-react';
import toast from 'react-hot-toast';
import { createProductConfig, updateProductConfig, updateProductSppTiers, getSettings } from '../api/client';
import { configCompetitors } from '../utils/competitors';


function initCompetitors(config) {
  const list = configCompetitors(config).map(c => ({ nm_id: String(c.nm_id), alias: c.alias }));
  // Пустая строка нужна форме — иначе рендерить нечего.
  return list.length > 0 ? list : [{ nm_id: '', alias: '' }];
}

function initTiers(config) {
  if (config?.rrp_spp_tiers?.length > 0) {
    return config.rrp_spp_tiers.map(t => ({
      spp_min: String(t.spp_min),
      spp_max: t.spp_max != null ? String(t.spp_max) : '',
      seller_price: t.seller_price != null ? String(t.seller_price) : '',
      rrp_price: t.rrp_price != null ? String(t.rrp_price) : '',
      mode: t.seller_price != null ? 'seller' : 'rrp',
    }));
  }
  return [];
}

export default function ConfigModal({ product, onClose }) {
  const isOzon = product.account_marketplace === 'ozon';
  const [strategy, setStrategy] = useState(isOzon ? 'rrp' : (product.config?.strategy || 'rrp'));
  const [formData, setFormData] = useState({
    is_active: product.config?.is_active ?? true,
    min_price: product.config?.min_price || '',
    max_price: product.config?.max_price || '',
    rrp_price: product.config?.rrp_price || '',
    // ?? а не ||: сохранённый допуск 0 не должен превращаться в значение по умолчанию
    rrp_tolerance: product.config?.rrp_tolerance ?? '3',
    rrp_consider_wallet: product.config?.rrp_consider_wallet ?? false,
    competitor_offset_type: product.config?.competitor_offset_type || 'rub',
    competitor_offset_direction: product.config?.competitor_offset_direction || 'below',
    competitor_offset_value: product.config?.competitor_offset_value ?? '',
    competitor_select_rule: product.config?.competitor_select_rule || 'min_price',
  });
  const [competitors, setCompetitors] = useState(() => initCompetitors(product.config));
  const [tiers, setTiers] = useState(() => initTiers(product.config));
  const [walletPct, setWalletPct] = useState(3.0);
  const [loading, setLoading] = useState(false);
  const [errors, setErrors] = useState({});

  const handleEsc = useCallback((e) => { if (e.key === 'Escape') onClose(); }, [onClose]);
  useEffect(() => {
    document.addEventListener('keydown', handleEsc);
    return () => document.removeEventListener('keydown', handleEsc);
  }, [handleEsc]);

  useEffect(() => {
    getSettings().then((res) => setWalletPct(res.data.wb_wallet_discount_pct)).catch(() => {});
  }, []);

  const addTier = () => setTiers([...tiers, { spp_min: '', spp_max: '', seller_price: '', rrp_price: '', mode: 'seller' }]);
  const removeTier = (i) => setTiers(tiers.filter((_, idx) => idx !== i));
  const updateTier = (i, field, value) => {
    const next = [...tiers]; next[i] = { ...next[i], [field]: value }; setTiers(next);
  };

  const validate = () => {
    const errs = {};
    const minP = parseFloat(formData.min_price);
    const maxP = parseFloat(formData.max_price);
    if (!formData.min_price || minP < 0) errs.min_price = 'Мин. цена должна быть >= 0';
    if (formData.max_price && maxP <= 0) errs.max_price = 'Макс. цена должна быть > 0';
    if (formData.min_price && formData.max_price && maxP <= minP) errs.max_price = 'Макс. цена должна быть больше мин. цены';
    if (strategy === 'rrp') {
      if (!formData.rrp_price || parseFloat(formData.rrp_price) <= 0)
        errs.rrp_price = isOzon ? 'Укажите целевую цену' : 'Укажите базовый РРЦ (запасная цена если нет подходящей ступени СПП)';
      if (formData.rrp_tolerance && parseFloat(formData.rrp_tolerance) < 0)
        errs.rrp_tolerance = 'Допуск не может быть отрицательным';
    } else {
      if (competitors.filter(c => c.nm_id && parseInt(c.nm_id) > 0).length === 0)
        errs.competitors = 'Добавьте хотя бы одного конкурента';
      const offsetRaw = formData.competitor_offset_value;
      const offsetNum = parseFloat(offsetRaw);
      const offsetIsZero = offsetRaw === 0 || (typeof offsetRaw === 'string' && offsetRaw.trim() === '0');
      if (!offsetIsZero && (offsetRaw === '' || offsetRaw === null || offsetRaw === undefined || isNaN(offsetNum) || offsetNum < 0))
        errs.competitor_offset_value = 'Смещение должно быть ≥ 0';
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
        payload.rrp_tolerance = formData.rrp_tolerance === '' ? 3 : parseFloat(formData.rrp_tolerance);
        payload.rrp_consider_wallet = isOzon ? false : formData.rrp_consider_wallet;
      } else {
        payload.competitors = competitors
          .filter(c => c.nm_id && parseInt(c.nm_id) > 0)
          .map(c => ({ nm_id: parseInt(c.nm_id), alias: c.alias || null }));
        payload.competitor_select_rule = formData.competitor_select_rule;
        payload.competitor_offset_type = formData.competitor_offset_type;
        payload.competitor_offset_direction = formData.competitor_offset_direction;
        payload.competitor_offset_value = parseFloat(formData.competitor_offset_value);
      }

      if (product.config) {
        await updateProductConfig(product.id, payload);
      } else {
        await createProductConfig(product.id, payload);
      }

      // Save SPP tiers (only for RRP strategy)
      if (strategy === 'rrp' && !isOzon) {
        const tierPayload = tiers
          .filter(t => t.spp_min !== '')
          .map(t => ({
            spp_min: parseFloat(t.spp_min),
            spp_max: t.spp_max !== '' ? parseFloat(t.spp_max) : null,
            seller_price: t.mode === 'seller' && t.seller_price !== '' ? parseFloat(t.seller_price) : null,
            rrp_price: t.mode === 'rrp' && t.rrp_price !== '' ? parseFloat(t.rrp_price) : null,
          }));
        await updateProductSppTiers(product.id, tierPayload);
      }

      toast.success(product.config ? 'Конфигурация обновлена' : 'Конфигурация создана');
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
          <button onClick={onClose} className="btn btn-ghost btn-sm p-1"><X className="w-5 h-5" /></button>
        </div>

        <form onSubmit={handleSubmit} className="p-6 space-y-5 max-h-[80vh] overflow-y-auto">

          {/* Active */}
          <label className="flex items-center gap-2.5 cursor-pointer">
            <input type="checkbox" checked={formData.is_active}
              onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })}
              className="w-4 h-4 accent-violet-500" />
            <span className="text-sm font-medium text-foreground">Репрайсер активен</span>
          </label>

          {/* Strategy */}
          <div>
            <p className="text-sm font-medium mb-2" style={{ color: 'var(--text-2)' }}>Стратегия ценообразования</p>
            <div className="grid grid-cols-2 gap-3">
              {[
                { id: 'rrp', label: 'РРЦ', desc: 'Диапазон вокруг рекомендованной розничной цены' },
                !isOzon && { id: 'competitor', label: 'Конкурент', desc: 'Привязка к цене конкурента по артикулу WB' },
              ].filter(Boolean).map((s) => (
                <button key={s.id} type="button" onClick={() => setStrategy(s.id)}
                  className={`p-3.5 rounded-lg text-left transition-all ${strategy === s.id ? 'border border-primary/40 bg-primary/5' : 'border border-border bg-muted/30'}`}>
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
              <input type="number" step="0.01" value={formData.min_price}
                onChange={(e) => { setFormData({ ...formData, min_price: e.target.value }); setErrors(p => ({ ...p, min_price: undefined })); }}
                className="input" placeholder="0 для отключения" required
                style={errors.min_price ? { borderColor: '#ef4444' } : {}} />
              {errors.min_price && <p className="text-xs mt-1 text-red-500">{errors.min_price}</p>}
            </div>
            <div>
              <label className="block text-sm font-medium mb-1.5" style={{ color: 'var(--text-2)' }}>Макс. цена (₽)</label>
              <input type="number" step="0.01" value={formData.max_price}
                onChange={(e) => { setFormData({ ...formData, max_price: e.target.value }); setErrors(p => ({ ...p, max_price: undefined })); }}
                className="input" placeholder="Не ограничено"
                style={errors.max_price ? { borderColor: '#ef4444' } : {}} />
              {errors.max_price && <p className="text-xs mt-1 text-red-500">{errors.max_price}</p>}
            </div>
          </div>

          {/* RRP section */}
          {strategy === 'rrp' && (
            <>
              <div className="p-4 rounded-lg space-y-4 bg-muted/30 border border-border">
                <p className="text-sm font-medium text-foreground">
                  Базовый РРЦ
                  {!isOzon && <span className="text-xs font-normal ml-1" style={{ color: 'var(--text-3)' }}>
                    — используется если нет подходящей ступени СПП
                  </span>}
                </p>
                <div className="grid grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>РРЦ (₽) <span className="text-red-400">*</span></label>
                    <input type="number" step="0.01" value={formData.rrp_price}
                      onChange={(e) => { setFormData({ ...formData, rrp_price: e.target.value }); setErrors(p => ({ ...p, rrp_price: undefined })); }}
                      className="input" placeholder="2500" required
                      style={errors.rrp_price ? { borderColor: '#ef4444' } : {}} />
                    {errors.rrp_price && <p className="text-xs mt-1 text-red-500">{errors.rrp_price}</p>}
                  </div>
                  <div>
                    <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Допуск над РРЦ (₽)</label>
                    <input type="number" step="0.01" min="0" value={formData.rrp_tolerance}
                      onChange={(e) => { setFormData({ ...formData, rrp_tolerance: e.target.value }); setErrors(p => ({ ...p, rrp_tolerance: undefined })); }}
                      className="input" placeholder="3"
                      style={errors.rrp_tolerance ? { borderColor: '#ef4444' } : {}} />
                    {errors.rrp_tolerance && <p className="text-xs mt-1 text-red-500">{errors.rrp_tolerance}</p>}
                    <p className="text-xs mt-1" style={{ color: 'var(--text-3)' }}>
                      {formData.rrp_price
                        ? `Цена покупателя${formData.rrp_consider_wallet ? ' с кошельком' : ''}: ${parseFloat(formData.rrp_price)}₽ — ${parseFloat(formData.rrp_price) + Math.max(parseFloat(formData.rrp_tolerance) || 0, 3)}₽. `
                        : ''}
                      Ниже РРЦ не опускается; выше коридора — возвращается к РРЦ. Минимум 3 ₽.
                    </p>
                  </div>
                </div>
                {!isOzon && <label className="flex items-start gap-2.5 cursor-pointer select-none">
                  <input type="checkbox" checked={formData.rrp_consider_wallet}
                    onChange={(e) => setFormData({ ...formData, rrp_consider_wallet: e.target.checked })}
                    className="w-4 h-4 mt-0.5 accent-violet-500 flex-shrink-0" />
                  <div>
                    <span className="text-sm font-medium text-foreground">Учитывать скидку кошелька WB ({walletPct}%)</span>
                    <p className="text-xs mt-0.5" style={{ color: 'var(--text-3)' }}>РРЦ будет ценой после СПП <strong>и</strong> кошелька</p>
                  </div>
                </label>}
                {!isOzon && formData.rrp_price && (
                  <div className="text-xs p-3 rounded bg-primary/5 border border-primary/20 text-muted-foreground">
                    {formData.rrp_consider_wallet ? (
                      <>Целевая цена до кошелька:{' '}
                        <strong className="text-primary">{(parseFloat(formData.rrp_price) / (1 - walletPct / 100)).toFixed(2)}₽</strong>
                      </>
                    ) : (
                      <>Фактическая цена с кошельком:{' '}
                        <strong className="text-primary">{(parseFloat(formData.rrp_price) * (1 - walletPct / 100)).toFixed(2)}₽</strong>
                      </>
                    )}
                  </div>
                )}
              </div>

              {/* SPP Tiers inline editor */}
              {!isOzon && <div className="rounded-lg border border-amber-200 bg-amber-50/50 dark:border-amber-800 dark:bg-amber-950/20 overflow-hidden">
                <div className="flex items-center justify-between px-4 py-3 border-b border-amber-200 dark:border-amber-800">
                  <div className="flex items-center gap-2">
                    <TrendingUp className="w-4 h-4 text-amber-500" />
                    <span className="text-sm font-medium text-foreground">Ступени по СПП</span>
                    <span className="text-xs px-1.5 py-0.5 rounded bg-amber-100 dark:bg-amber-900 text-amber-700 dark:text-amber-300">{tiers.length > 0 ? `${tiers.length} шт.` : 'нет'}</span>
                  </div>
                  <button type="button" onClick={addTier}
                    className="btn btn-xs gap-1" style={{ background: '#f59e0b', color: 'white', border: 'none' }}>
                    <Plus className="w-3 h-3" /> Добавить
                  </button>
                </div>
                {tiers.length === 0 ? (
                  <div className="px-4 py-4 text-xs text-center" style={{ color: 'var(--text-3)' }}>
                    Ступени не заданы — используется базовый РРЦ для всех значений СПП.<br />
                    Нажмите «Добавить» чтобы задать разные цены для разных диапазонов СПП.
                  </div>
                ) : (
                  <div className="p-3 space-y-2">
                    <div className="grid gap-1.5 text-xs font-medium" style={{ gridTemplateColumns: '70px 70px 1fr 1fr 32px', color: 'var(--text-3)' }}>
                      <span>СПП от %</span><span>СПП до %</span><span>Тип цены</span><span>Цена ₽</span><span />
                    </div>
                    {tiers.map((t, i) => (
                      <div key={i} className="grid gap-1.5 items-center" style={{ gridTemplateColumns: '70px 70px 1fr 1fr 32px' }}>
                        <input type="number" min="0" max="100" step="0.1" value={t.spp_min} placeholder="0"
                          onChange={(e) => updateTier(i, 'spp_min', e.target.value)}
                          className="input text-xs px-2 py-1.5 h-8" />
                        <input type="number" min="0" max="100" step="0.1" value={t.spp_max} placeholder="∞"
                          onChange={(e) => updateTier(i, 'spp_max', e.target.value)}
                          className="input text-xs px-2 py-1.5 h-8" />
                        <select value={t.mode} onChange={(e) => updateTier(i, 'mode', e.target.value)}
                          className="input text-xs px-2 py-1.5 h-8">
                          <option value="seller">Цена продавца</option>
                          <option value="rrp">РРЦ (финальная)</option>
                        </select>
                        <input type="number" min="1" step="1"
                          value={t.mode === 'seller' ? t.seller_price : t.rrp_price}
                          placeholder={t.mode === 'seller' ? 'до скидок' : 'финальная'}
                          onChange={(e) => updateTier(i, t.mode === 'seller' ? 'seller_price' : 'rrp_price', e.target.value)}
                          className="input text-xs px-2 py-1.5 h-8" />
                        <button type="button" onClick={() => removeTier(i)}
                          className="flex items-center justify-center w-8 h-8 rounded hover:bg-red-50 dark:hover:bg-red-950/30"
                          style={{ color: '#ef4444' }}>
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ))}
                    <p className="text-xs pt-1" style={{ color: 'var(--text-3)' }}>
                      Пустое «СПП до» = без верхней границы.
                    </p>
                  </div>
                )}
              </div>}
            </>
          )}

          {/* Competitor section */}
          {strategy === 'competitor' && (
            <div className="p-4 rounded-lg space-y-4 bg-muted/30 border border-border">
              <p className="text-sm font-medium text-foreground">Конкуренты</p>
              <div className="space-y-2">
                {competitors.map((c, idx) => (
                  <div key={idx} className="flex gap-2 items-center">
                    <input type="number" value={c.nm_id}
                      onChange={e => { const next = [...competitors]; next[idx] = { ...next[idx], nm_id: e.target.value }; setCompetitors(next); setErrors(p => ({ ...p, competitors: undefined })); }}
                      className="input flex-1" placeholder="Артикул WB" />
                    <input type="text" value={c.alias}
                      onChange={e => { const next = [...competitors]; next[idx] = { ...next[idx], alias: e.target.value }; setCompetitors(next); }}
                      className="input" style={{ width: '120px' }} placeholder="Метка" />
                    <button type="button" onClick={() => setCompetitors(competitors.filter((_, i) => i !== idx))}
                      className="btn btn-ghost btn-sm p-1.5" style={{ color: '#ef4444' }} disabled={competitors.length === 1}>
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
                {errors.competitors && <p className="text-xs text-red-500">{errors.competitors}</p>}
                <button type="button" onClick={() => setCompetitors([...competitors, { nm_id: '', alias: '' }])}
                  className="btn btn-secondary btn-sm gap-1.5 text-xs">
                  <Plus className="w-3.5 h-3.5" /> Добавить конкурента
                </button>
              </div>
              <div>
                <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Правило выбора цены</label>
                <select value={formData.competitor_select_rule}
                  onChange={e => setFormData({ ...formData, competitor_select_rule: e.target.value })} className="input w-full">
                  <option value="min_price">Минимальная среди всех</option>
                  <option value="cheapest_instock">Минимальная среди тех кто в наличии</option>
                  <option value="avg_price">Средняя цена</option>
                </select>
              </div>
              {(() => {
                const _v = formData.competitor_offset_value;
                const isEqual = _v === 0 || (typeof _v === 'string' && _v.trim() === '0');
                const directionUiValue = isEqual ? 'equal' : (formData.competitor_offset_direction || 'below');
                const handleDirectionChange = (val) => {
                  if (val === 'equal') {
                    setFormData({ ...formData, competitor_offset_direction: 'above', competitor_offset_value: '0' });
                    setErrors(p => ({ ...p, competitor_offset_value: undefined }));
                  } else {
                    const prevVal = formData.competitor_offset_value;
                    const newVal = (String(prevVal ?? '') === '0' || Number(prevVal) === 0) ? '' : prevVal;
                    setFormData({ ...formData, competitor_offset_direction: val, competitor_offset_value: newVal });
                  }
                };
                return (
                  <div className="grid grid-cols-3 gap-3">
                    <div>
                      <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Направление</label>
                      <select value={directionUiValue}
                        onChange={(e) => handleDirectionChange(e.target.value)} className="input">
                        <option value="below">Ниже</option>
                        <option value="equal">Как у конкурента</option>
                        <option value="above">Выше</option>
                      </select>
                    </div>
                    <div>
                      <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Смещение</label>
                      <input type="number" step="0.01" value={isEqual ? 0 : formData.competitor_offset_value}
                        disabled={isEqual}
                        onChange={(e) => { setFormData({ ...formData, competitor_offset_value: e.target.value }); setErrors(p => ({ ...p, competitor_offset_value: undefined })); }}
                        className="input" placeholder="50" style={(!isEqual && errors.competitor_offset_value) ? { borderColor: '#ef4444' } : {}} />
                      {!isEqual && errors.competitor_offset_value && <p className="text-xs mt-1 text-red-500">{errors.competitor_offset_value}</p>}
                    </div>
                    <div>
                      <label className="block text-sm mb-1.5" style={{ color: 'var(--text-2)' }}>Тип</label>
                      <select value={formData.competitor_offset_type}
                        disabled={isEqual}
                        onChange={(e) => setFormData({ ...formData, competitor_offset_type: e.target.value })} className="input">
                        <option value="rub">Рубли</option>
                        <option value="pct">Проценты</option>
                      </select>
                    </div>
                  </div>
                );
              })()}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-4 border-t border-border">
            <button type="button" onClick={onClose} className="btn btn-secondary">Отмена</button>
            <button type="submit" disabled={loading} className="btn btn-primary">
              {loading ? 'Сохранение...' : 'Сохранить'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

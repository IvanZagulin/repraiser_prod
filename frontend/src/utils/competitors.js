/**
 * Единый источник списка конкурентов из конфига товара.
 *
 * Конкуренты хранятся в competitor_entries (config.competitors), но
 * подавляющее большинство конфигов на проде до сих пор держит конкурента в
 * legacy-поле competitor_nm_id — запись в competitor_entries создаётся только
 * при сохранении карточки настроек. Бэкенд читает оба источника
 * (RepricerEngine._get_competitor_price), поэтому UI обязан делать то же
 * самое: иначе у работающей стратегии карточка товара показывает пустоту.
 *
 * Возвращает [] когда конкурент не задан вовсе.
 */
export function configCompetitors(config) {
  if (config?.competitors?.length > 0) {
    return config.competitors.map((c) => ({ nm_id: c.nm_id, alias: c.alias || '' }));
  }
  if (config?.competitor_nm_id) {
    return [{ nm_id: config.competitor_nm_id, alias: '' }];
  }
  return [];
}

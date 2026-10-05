// Последние фильтры списка товаров: кнопка «Назад» на карточке товара
// возвращает к списку с теми же фильтрами и страницей.
const LAST_SEARCH_KEY = 'products:lastSearch';

export function rememberProductsSearch(search) {
  try { sessionStorage.setItem(LAST_SEARCH_KEY, search); } catch { /* storage недоступен */ }
}

export function productsListUrl() {
  try {
    const last = sessionStorage.getItem(LAST_SEARCH_KEY);
    return last ? `/products?${last}` : '/products';
  } catch {
    return '/products';
  }
}

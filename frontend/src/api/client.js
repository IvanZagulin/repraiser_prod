import axios from 'axios';
import { useAuthStore } from '../store';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
  withCredentials: true,  // отправлять httpOnly cookies на каждый запрос
});

// JWT теперь передаётся через httpOnly cookie (withCredentials: true выше).
// Authorization header больше не нужен — cookie отправляется автоматически.

// Interceptor для обработки ошибок
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      const url = error.config?.url || '';
      // Не редиректим если 401 пришёл от auth-эндпоинтов или фоновых запросов (skipAuthRedirect)
      const isAuthCall = url.includes('/auth/login') ||
                         url.includes('/auth/register') ||
                         url.includes('/auth/forgot-password') ||
                         url.includes('/auth/reset-password') ||
                         url.includes('/auth/verify-email');
      const skipRedirect = error.config?.skipAuthRedirect === true;
      if (!isAuthCall && !skipRedirect) {
        // Use store action instead of window.location.href — avoids full page reload.
        // ProtectedRoute subscribes to token and will redirect via <Navigate> (SPA nav).
        useAuthStore.getState().logout();
      }
    }
    return Promise.reject(error);
  }
);

// Auth
export const login = (email, password) =>
  api.post('/api/auth/login', { email, password });

export const register = (email, password) =>
  api.post('/api/auth/register', { email, password });

export const verifyToken = () =>
  api.get('/api/auth/verify');

export const verifyEmail = (email, code) =>
  api.post('/api/auth/verify-email', { email, code });

export const resendCode = (email) =>
  api.post('/api/auth/resend-code', { email });

export const forgotPassword = (email) =>
  api.post('/api/auth/forgot-password', { email });

export const resetPassword = (email, code, new_password) =>
  api.post('/api/auth/reset-password', { email, code, new_password });

// Accounts
export const getAccounts = () =>
  api.get('/api/accounts');

export const createAccount = (data) =>
  api.post('/api/accounts', data);

export const updateAccount = (id, data) =>
  api.put(`/api/accounts/${id}`, data);

export const deleteAccount = (id) =>
  api.delete(`/api/accounts/${id}`);

export const syncAccountCatalog = (id) =>
  api.post(`/api/accounts/${id}/sync-catalog`);
export const syncAccountProducts = (id) =>
  api.post(`/api/accounts/${id}/sync-products`);

// Products
export const getProductStats = () => api.get('/api/products/stats');
export const getProducts = (params) =>
  api.get('/api/products', { params });

export const searchWBProduct = (accountId, text) =>
  api.get('/api/products/search-wb', { params: { account_id: accountId, text } });

export const createProduct = (data) =>
  api.post('/api/products', data);

export const deleteProduct = (id) =>
  api.delete(`/api/products/${id}`);

export const createProductConfig = (productId, data) =>
  api.post(`/api/products/${productId}/config`, data);

export const updateProductConfig = (productId, data) =>
  api.put(`/api/products/${productId}/config`, data);

export const getProductHistory = (productId, limit = 100) =>
  api.get(`/api/products/${productId}/history`, { params: { limit } });

export const getProductsBySku = (sellerSku) =>
  api.get(`/api/products/by-sku/${encodeURIComponent(sellerSku)}`);

export const getProductDetail = (productId) =>
  api.get(`/api/products/${productId}/detail`);

// Repricer
export const runRepricer = (sellerSkus) =>
  api.post('/api/repricer/run-sync', { seller_skus: sellerSkus });

export const fetchPrices = () =>
  api.post('/api/repricer/fetch-prices');

export const getRepricerRuns = (params) =>
  api.get('/api/repricer/runs', { params });

export const getRepricerRun = (id, includeChanges = true) =>
  api.get(`/api/repricer/runs/${id}`, { params: { include_changes: includeChanges } });

// Export
export const exportHistory = (sellerSkus) => {
  const params = sellerSkus ? { seller_skus: sellerSkus.join(',') } : {};
  return api.get('/api/export/history', {
    params,
    responseType: 'blob',
  });
};

export const exportRun = (runId) =>
  api.get(`/api/export/run/${runId}`, {
    responseType: 'blob',
  });

export const exportProducts = (filters = {}) => {
  const params = {};
  if (filters.status && filters.status !== 'all') params.status_filter = filters.status;
  if (filters.strategy && filters.strategy !== 'all') params.strategy_filter = filters.strategy;
  return api.get('/api/export/products', { params, responseType: 'blob' });
};

export const downloadTemplate = (type) =>
  api.get(`/api/export/template/${type}`, { responseType: 'blob' });

// Profile / change password
export const changePasswordSendCode = () =>
  api.post('/api/auth/change-password/send-code');

export const changePasswordConfirm = (code, new_password) =>
  api.post('/api/auth/change-password/confirm', { code, new_password });

// Schedule
export const getSchedule = () =>
  api.get('/api/schedule/');

export const updateSchedule = (data) =>
  api.put('/api/schedule/', data);

// Analytics
export const getPriceDynamics = (productId, days = 30) =>
  api.get(`/api/analytics/product/${productId}/price-dynamics`, { params: { days } });

export const getSavings = (days = 30) =>
  api.get('/api/analytics/savings', { params: { days } });

export const getCompetitorTrends = (days = 30) =>
  api.get('/api/analytics/competitor-trends', { params: { days } });

export const getAnalyticsSummary = () =>
  api.get('/api/analytics/summary');

// Bulk operations
export const bulkStrategy = (productIds, strategy, config) =>
  api.post('/api/products/bulk/strategy', { product_ids: productIds, strategy, config });

export const bulkActivate = (productIds, isActive) =>
  api.post('/api/products/bulk/activate', { product_ids: productIds, is_active: isActive });

export const bulkDelete = (productIds) =>
  api.post('/api/products/bulk/delete', { product_ids: productIds });

// Activity log
export const getActivityLog = (params) =>
  api.get('/api/activity/', { params });

export const exportActivityLog = () =>
  api.get('/api/activity/export', { responseType: 'blob' });

// Onboarding
export const completeOnboarding = () =>
  api.post('/api/auth/complete-onboarding');

// Admin
export const getAdminUsers = () =>
  api.get('/api/admin/users');

export const assignPlan = (userId, planCode) =>
  api.put(`/api/admin/users/${userId}/plan`, { plan_code: planCode });

export const getPlans = () =>
  api.get('/api/admin/plans');

// Settings
export const getSettings = () =>
  api.get('/api/settings');

export const updateSettings = (data) =>
  api.put('/api/settings', data);

// Import
export const importStrategy = (file) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post('/api/export/import/strategy', formData, {
    headers: {
      'Content-Type': 'multipart/form-data',
    },
  });
};

export const getTaskStatus = (taskId) => api.get(`/api/export/task/${taskId}`);

export const importProducts = (file) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post('/api/export/import/products', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const importCosts = (file) => {
  const formData = new FormData();
  formData.append('file', file);
  return api.post('/api/export/import/costs', formData, {
    headers: { 'Content-Type': 'multipart/form-data' },
  });
};

export const downloadCostsTemplate = () =>
  api.get('/api/export/template/costs', { responseType: 'blob' });

// Reports
export const generateReport = (days = 30) =>
  api.get('/api/reports/generate', { params: { days }, responseType: 'blob' });

// Dashboard widgets data
export const getDashboardData = () =>
  Promise.all([
    getAccounts().catch(() => ({ data: { accounts: [] } })),
    getProducts({ page: 1, page_size: 1000 }).catch(() => ({ data: { products: [] } })),
    getRepricerRuns({ limit: 5 }).catch(() => ({ data: { runs: [] } })),
    getSavings(7).catch(() => ({ data: { total_savings_rub: 0 } })),
    getActivityLog({ page: 1, page_size: 5 }).catch(() => ({ data: { items: [] } })),
  ]);

// --- Groups / Tags (F6) ---
export const getGroups = () => api.get('/api/groups');
export const createGroup = (data) => api.post('/api/groups', data);
export const updateGroup = (id, data) => api.put(`/api/groups/${id}`, data);
export const deleteGroup = (id) => api.delete(`/api/groups/${id}`);
export const addProductsToGroup = (groupId, productIds) =>
  api.post(`/api/groups/${groupId}/products`, { product_ids: productIds });
export const removeProductsFromGroup = (groupId, productIds) =>
  api.delete(`/api/groups/${groupId}/products`, { data: { product_ids: productIds } });

// --- Competitor Alerts (F5) ---
// skipAuthRedirect=true — 401 от этих запросов не выкидывает на /login (фоновый поллинг)
export const getAlerts = (params) => api.get('/api/alerts', { params, skipAuthRedirect: true });
export const getAlertCount = () => api.get('/api/alerts/count', { skipAuthRedirect: true });
export const markAlertRead = (id) => api.put(`/api/alerts/${id}/read`, {}, { skipAuthRedirect: true });
export const markAllAlertsRead = () => api.put('/api/alerts/read-all', {}, { skipAuthRedirect: true });

// --- Config Audit Log (F4) ---
export const getProductAuditLog = (productId) => api.get(`/api/audit/product/${productId}`);
export const getRecentAuditLog = (limit = 50) => api.get('/api/audit/recent', { params: { limit } });

// --- Product direct update ---
export const updateProductCost = (productId, costPrice) =>
  api.patch(`/api/products/${productId}/cost`, { cost_price: costPrice });

// --- Pricing Rules (F9) ---
export const getProductRules = (productId) => api.get(`/api/products/${productId}/rules`);
export const createProductRule = (productId, data) => api.post(`/api/products/${productId}/rules`, data);
export const updateProductRule = (productId, ruleId, data) => api.put(`/api/products/${productId}/rules/${ruleId}`, data);
export const deleteProductRule = (productId, ruleId) => api.delete(`/api/products/${productId}/rules/${ruleId}`);

export default api;

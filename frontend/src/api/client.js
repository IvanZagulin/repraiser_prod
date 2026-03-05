import axios from 'axios';

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const api = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Interceptor для добавления токена
api.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Interceptor для обработки ошибок
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      window.location.href = '/login';
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

// Accounts
export const getAccounts = () =>
  api.get('/api/accounts');

export const createAccount = (data) =>
  api.post('/api/accounts', data);

export const updateAccount = (id, data) =>
  api.put(`/api/accounts/${id}`, data);

export const deleteAccount = (id) =>
  api.delete(`/api/accounts/${id}`);

// Products
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

export default api;

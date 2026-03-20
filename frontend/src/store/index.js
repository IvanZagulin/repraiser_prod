import { create } from 'zustand';

function parseJwt(token) {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(atob(base64));
  } catch {
    return {};
  }
}

function tokenToUser(token) {
  if (!token) return null;
  const payload = parseJwt(token);
  return {
    id: payload.user_id ?? null,
    email: payload.sub ?? '',
    isAdmin: payload.is_admin ?? false,
    onboardingCompleted: null, // will be fetched from API
    plan: null,
    trialEndsAt: null,
  };
}

export const useAuthStore = create((set) => ({
  token: localStorage.getItem('token'),
  user: tokenToUser(localStorage.getItem('token')),

  login: (token) => {
    localStorage.setItem('token', token);
    set({ token, user: tokenToUser(token) });
  },

  logout: () => {
    console.error('[LOGOUT] called from:', new Error().stack);
    localStorage.removeItem('token');
    set({ token: null, user: null });
  },

  setUser: (user) => set({ user }),
}));

export const useProductsStore = create((set) => ({
  products: [],
  loading: false,
  filters: {
    account_id: null,
    strategy: null,
    is_active: null,
  },

  setProducts: (products) => set({ products }),
  setLoading: (loading) => set({ loading }),
  setFilters: (filters) => set((state) => ({
    filters: { ...state.filters, ...filters }
  })),

  addProduct: (product) => set((state) => ({
    products: [...state.products, product]
  })),

  removeProduct: (id) => set((state) => ({
    products: state.products.filter(p => p.id !== id)
  })),

  updateProduct: (id, updates) => set((state) => ({
    products: state.products.map(p =>
      p.id === id ? { ...p, ...updates } : p
    )
  })),
}));

export const useAccountsStore = create((set) => ({
  accounts: [],
  loading: false,

  setAccounts: (accounts) => set({ accounts }),
  setLoading: (loading) => set({ loading }),

  addAccount: (account) => set((state) => ({
    accounts: [...state.accounts, account]
  })),

  removeAccount: (id) => set((state) => ({
    accounts: state.accounts.filter(a => a.id !== id)
  })),

  updateAccount: (id, updates) => set((state) => ({
    accounts: state.accounts.map(a =>
      a.id === id ? { ...a, ...updates } : a
    )
  })),
}));

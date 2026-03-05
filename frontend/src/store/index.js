import { create } from 'zustand';

export const useAuthStore = create((set) => ({
  token: localStorage.getItem('token'),
  user: null,
  
  login: (token) => {
    localStorage.setItem('token', token);
    set({ token });
  },
  
  logout: () => {
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

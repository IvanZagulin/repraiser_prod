import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { useEffect, useState } from 'react';
import { useAuthStore } from './store';
import { verifyToken } from './api/client';
import Login from './pages/Login'
import RegisterPage from './pages/RegisterPage';
import VerifyEmailPage from './pages/VerifyEmailPage';
import ForgotPasswordPage from './pages/ForgotPasswordPage';
import OnboardingPage from './pages/OnboardingPage';
import Layout from './components/Layout';
import Dashboard from './pages/Dashboard';
import Products from './pages/Products';
import Accounts from './pages/Accounts';
import Runs from './pages/Runs';
import ImportExport from './pages/ImportExport';
import SettingsPage from './pages/SettingsPage';
import Analytics from './pages/Analytics';
import SchedulePage from './pages/SchedulePage';
import ReportsPage from './pages/ReportsPage';
import ProductDetailPage from './pages/ProductDetailPage';
import ActivityLogPage from './pages/ActivityLogPage';

function ScrollToTop() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0 });
  }, [pathname]);
  return null;
}

function ProtectedRoute({ children }) {
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const login = useAuthStore((state) => state.login);
  const [checking, setChecking] = useState(true);
  const [authenticated, setAuthenticated] = useState(false);
  const [onboardingDone, setOnboardingDone] = useState(true);

  useEffect(() => {
    // JWT хранится в httpOnly cookie — JS не может его прочитать.
    // Единственный способ проверить авторизацию — запрос к серверу.
    verifyToken()
      .then((res) => {
        const data = res.data;
        setUser({
          ...user,
          id: data.user_id,
          email: data.email,
          isAdmin: data.is_admin,
          onboardingCompleted: data.onboarding_completed,
          plan: data.plan,
          trialEndsAt: data.trial_ends_at,
          usage: data.usage,
        });
        setOnboardingDone(data.onboarding_completed !== false);
        setAuthenticated(true);
      })
      .catch(() => {
        // 401 или сеть — не авторизован
        setAuthenticated(false);
      })
      .finally(() => setChecking(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (checking) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="spinner" />
      </div>
    );
  }

  if (!authenticated) {
    return <Navigate to="/login" replace />;
  }

  if (!onboardingDone) {
    return <Navigate to="/onboarding" replace />;
  }

  return children;
}

function App() {
  return (
    <BrowserRouter>
      <ScrollToTop />
      <Toaster
        position="top-right"
        toastOptions={{
          duration: 3000,
          style: {
            background: 'hsl(var(--card))',
            color: 'hsl(var(--card-foreground))',
            border: '1px solid hsl(var(--border))',
            borderRadius: '6px',
            fontSize: '14px',
          },
        }}
      />

      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route path="/verify-email" element={<VerifyEmailPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/onboarding" element={<OnboardingPage />} />

        <Route
          path="/"
          element={
            <ProtectedRoute>
              <Layout />
            </ProtectedRoute>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="products" element={<Products />} />
          <Route path="products/:productId" element={<ProductDetailPage />} />
          <Route path="accounts" element={<Accounts />} />
          <Route path="runs" element={<Runs />} />
          <Route path="analytics" element={<Analytics />} />
          <Route path="schedule" element={<SchedulePage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="import-export" element={<ImportExport />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="activity" element={<ActivityLogPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;

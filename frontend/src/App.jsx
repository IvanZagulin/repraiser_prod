import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Toaster } from 'react-hot-toast';
import { useEffect, useState } from 'react';
import { useAuthStore } from './store';
import { verifyToken } from './api/client';
import Login from './pages/Login';
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
import ActivityLogPage from './pages/ActivityLogPage';
import ReportsPage from './pages/ReportsPage';
import ProductDetailPage from './pages/ProductDetailPage';

function isTokenExpired(token) {
  try {
    const base64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(base64));
    if (!payload.exp) return false;
    // 60s buffer to avoid edge-case race
    return payload.exp * 1000 < Date.now() - 60000;
  } catch {
    return true;
  }
}

function ProtectedRoute({ children }) {
  const token = useAuthStore((state) => state.token);
  const logout = useAuthStore((state) => state.logout);
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const [checking, setChecking] = useState(true);
  const [onboardingDone, setOnboardingDone] = useState(true);

  useEffect(() => {
    if (!token || isTokenExpired(token)) {
      if (token) logout();
      setChecking(false);
      return;
    }

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
      })
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);

  if (!token || isTokenExpired(token)) {
    if (token) logout();
    return <Navigate to="/login" replace />;
  }

  if (checking) {
    return (
      <div className="flex h-screen items-center justify-center">
        <div className="spinner" />
      </div>
    );
  }

  if (!onboardingDone) {
    return <Navigate to="/onboarding" replace />;
  }

  return children;
}

function App() {
  return (
    <BrowserRouter>
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
          <Route path="activity" element={<ActivityLogPage />} />
          <Route path="reports" element={<ReportsPage />} />
          <Route path="import-export" element={<ImportExport />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;

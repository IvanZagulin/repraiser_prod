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
    // JWT payload is base64url — need to add padding for atob()
    const b64 = token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - b64.length % 4) % 4);
    const payload = JSON.parse(atob(padded));
    if (!payload.exp) return false;
    // token is expired if exp was MORE than 60s ago
    return payload.exp * 1000 < Date.now() - 60000;
  } catch {
    // Can't parse → don't assume expired; let server decide via verifyToken
    return false;
  }
}

function ProtectedRoute({ children }) {
  const token = useAuthStore((state) => state.token);
  const logout = useAuthStore((state) => state.logout);
  const user = useAuthStore((state) => state.user);
  const setUser = useAuthStore((state) => state.setUser);
  const [checking, setChecking] = useState(true);
  const [onboardingDone, setOnboardingDone] = useState(true);
  const [shouldRedirect, setShouldRedirect] = useState(false);

  useEffect(() => {
    if (!token) {
      setChecking(false);
      return;
    }
    if (isTokenExpired(token)) {
      console.error('[LOGOUT] isTokenExpired=true, calling logout');
      logout();
      setChecking(false);
      return;
    }

    verifyToken()
      .then((res) => {
        const data = res.data;
        console.log('[VERIFY OK]', { onboarding_completed: data.onboarding_completed, user_id: data.user_id });
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
      .catch(() => {
        // verifyToken failed — let interceptor handle if 401, else stay logged in
      })
      .finally(() => setChecking(false));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Redirect to login if no token (no side effects in render)
  if (!token) {
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

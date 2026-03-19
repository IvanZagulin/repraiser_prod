import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Loader2, Zap } from 'lucide-react';
import { login } from '../api/client';
import { useAuthStore } from '../store';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';

export default function Login() {
  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [loading,  setLoading]  = useState(false);

  const navigate   = useNavigate();
  const loginStore = useAuthStore((state) => state.login);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      const response = await login(email, password);
      loginStore(response.data.access_token);
      toast.success('Добро пожаловать!');
      navigate('/');
    } catch (err) {
      const detail = err.response?.data?.detail;
      if (detail === 'Email не подтверждён') {
        toast.error('Сначала подтвердите email');
        navigate('/verify-email', { state: { email: email.trim().toLowerCase() } });
      } else {
        toast.error('Неверный email или пароль');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm animate-fade-in-up">

        {/* Brand */}
        <div className="mb-8 text-center">
          <div
            className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-xl"
            style={{
              background: 'linear-gradient(135deg, var(--neon), var(--accent-blue))',
              boxShadow: '0 2px 12px rgba(0,200,255,.3)',
            }}
          >
            <span className="w-2.5 h-2.5 rounded-full bg-white" />
          </div>
          <h1 className="text-2xl font-bold tracking-wider font-mono">QUASAR-X</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">Войдите в свой аккаунт</p>
        </div>

        {/* Form card */}
        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email"
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your@email.com"
                  required
                  autoComplete="email"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">Пароль</Label>
                <Input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="••••••••"
                  required
                  autoComplete="current-password"
                />
              </div>

              <Button type="submit" disabled={loading} className="w-full mt-2">
                {loading
                  ? <><Loader2 className="h-4 w-4 animate-spin" /> Вход...</>
                  : 'Войти'
                }
              </Button>
            </form>
          </CardContent>
        </Card>

        {/* Links */}
        <div className="mt-5 space-y-2 text-center text-sm text-muted-foreground">
          <p>
            <Link to="/forgot-password" className="text-primary hover:underline underline-offset-4">
              Забыли пароль?
            </Link>
          </p>
          <p>
            Нет аккаунта?{' '}
            <Link to="/register" className="font-medium text-primary hover:underline underline-offset-4">
              Зарегистрироваться
            </Link>
          </p>
        </div>

      </div>
    </div>
  );
}

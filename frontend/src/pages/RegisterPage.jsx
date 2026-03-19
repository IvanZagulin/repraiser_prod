import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Loader2, Zap } from 'lucide-react';
import { register } from '../api/client';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';

export default function RegisterPage() {
  const [email,    setEmail]    = useState('');
  const [password, setPassword] = useState('');
  const [confirm,  setConfirm]  = useState('');
  const [loading,  setLoading]  = useState(false);

  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (password !== confirm) { toast.error('Пароли не совпадают'); return; }
    if (password.length < 8)  { toast.error('Пароль должен быть минимум 8 символов'); return; }
    setLoading(true);
    try {
      await register(email, password);
      toast.success('Код подтверждения отправлен на ваш email');
      navigate('/verify-email', { state: { email: email.trim().toLowerCase() } });
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка регистрации');
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
          <p className="mt-1.5 text-sm text-muted-foreground">Создайте аккаунт</p>
        </div>

        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-4">
              <div className="space-y-1.5">
                <Label htmlFor="email">Email</Label>
                <Input
                  id="email" type="email" value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your@email.com" required autoComplete="email"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="password">Пароль</Label>
                <Input
                  id="password" type="password" value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Минимум 8 символов" required autoComplete="new-password"
                />
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="confirm">Подтвердите пароль</Label>
                <Input
                  id="confirm" type="password" value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="••••••••" required autoComplete="new-password"
                  className={confirm && confirm !== password ? 'border-destructive focus-visible:ring-destructive' : ''}
                />
                {confirm && confirm !== password && (
                  <p className="text-xs text-destructive">Пароли не совпадают</p>
                )}
              </div>

              <Button type="submit" disabled={loading} className="w-full mt-2">
                {loading
                  ? <><Loader2 className="h-4 w-4 animate-spin" /> Создание аккаунта...</>
                  : 'Зарегистрироваться'
                }
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="mt-5 text-center text-sm text-muted-foreground">
          Уже есть аккаунт?{' '}
          <Link to="/login" className="font-medium text-primary hover:underline underline-offset-4">
            Войти
          </Link>
        </p>

      </div>
    </div>
  );
}

import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import toast from 'react-hot-toast';
import {
  Zap, Eye, Bell, ArrowRight, Loader2, CheckCircle2, SkipForward,
} from 'lucide-react';
import { useAuthStore } from '../store';
import { createAccount } from '../api/client';
import api from '../api/client';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';

const TOTAL_STEPS = 3;

/* ─── Step indicator (1-2-3 dots) ─── */
function StepIndicator({ current }) {
  return (
    <div className="flex items-center justify-center gap-3 mb-8">
      {Array.from({ length: TOTAL_STEPS }, (_, i) => {
        const step = i + 1;
        const isActive = step === current;
        const isDone = step < current;
        return (
          <div key={step} className="flex items-center gap-3">
            {i > 0 && (
              <div
                className="h-px w-8 transition-colors duration-300"
                style={{
                  backgroundColor: isDone ? '#00c8ff' : 'hsl(var(--border))',
                }}
              />
            )}
            <div
              className="flex h-9 w-9 items-center justify-center rounded-full text-sm font-semibold transition-all duration-300"
              style={{
                background: isActive
                  ? 'linear-gradient(135deg, #00c8ff, #6366f1)'
                  : isDone
                    ? '#00c8ff'
                    : 'transparent',
                border: isActive || isDone ? 'none' : '2px solid hsl(var(--border))',
                color: isActive || isDone ? '#fff' : 'hsl(var(--muted-foreground))',
                boxShadow: isActive ? '0 2px 16px rgba(0,200,255,.35)' : 'none',
              }}
            >
              {isDone ? <CheckCircle2 className="h-4 w-4" /> : step}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Step 1: Welcome ─── */
function StepWelcome({ onNext }) {
  const features = [
    { icon: Zap, label: 'Автоматический репрайсинг', desc: 'Цены обновляются автоматически по вашим стратегиям' },
    { icon: Eye, label: 'Мониторинг конкурентов', desc: 'Отслеживайте цены конкурентов в реальном времени' },
    { icon: Bell, label: 'Telegram-уведомления', desc: 'Мгновенные оповещения о важных изменениях' },
  ];

  return (
    <div className="text-center">
      {/* Brand icon */}
      <div
        className="mx-auto mb-5 flex h-14 w-14 items-center justify-center rounded-2xl"
        style={{
          background: 'linear-gradient(135deg, #00c8ff, #6366f1)',
          boxShadow: '0 4px 24px rgba(0,200,255,.35)',
        }}
      >
        <Zap className="h-7 w-7 text-white" />
      </div>

      <h1 className="text-3xl font-bold tracking-tight mb-2">
        Добро пожаловать в{' '}
        <span
          className="font-mono tracking-wider"
          style={{
            background: 'linear-gradient(135deg, #00c8ff, #6366f1)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent',
          }}
        >
          QUASAR-X
        </span>
        !
      </h1>

      <p className="text-muted-foreground mb-8 max-w-md mx-auto">
        У вас 7 дней бесплатного доступа ко всем функциям тарифа{' '}
        <span className="font-semibold text-foreground">Бизнес</span>
      </p>

      <div className="space-y-4 text-left mb-8">
        {features.map(({ icon: Icon, label, desc }) => (
          <div
            key={label}
            className="flex items-start gap-4 p-4 rounded-xl border border-border/50 bg-card/60 backdrop-blur-sm"
          >
            <div
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg"
              style={{
                background: 'linear-gradient(135deg, rgba(0,200,255,.15), rgba(99,102,241,.1))',
              }}
            >
              <Icon className="h-5 w-5" style={{ color: '#00c8ff' }} />
            </div>
            <div>
              <p className="font-medium text-sm">{label}</p>
              <p className="text-xs text-muted-foreground mt-0.5">{desc}</p>
            </div>
          </div>
        ))}
      </div>

      <Button onClick={onNext} className="w-full gap-2" size="lg">
        Начать настройку <ArrowRight className="h-4 w-4" />
      </Button>
    </div>
  );
}

/* ─── Step 2: Connect WB Account ─── */
function StepConnect({ onNext, onSkip }) {
  const [name, setName] = useState('');
  const [token, setToken] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!name.trim()) { toast.error('Введите название аккаунта'); return; }
    if (!token.trim()) { toast.error('Вставьте API-токен'); return; }
    setLoading(true);
    try {
      await createAccount({ name: name.trim(), wb_api_token: token.trim() });
      toast.success('Аккаунт Wildberries подключён!');
      onNext();
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка подключения аккаунта');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <div className="text-center mb-6">
        <h2 className="text-2xl font-bold mb-1">Подключите аккаунт WB</h2>
        <p className="text-sm text-muted-foreground">
          Создайте API-токен в личном кабинете Wildberries &rarr; Настройки &rarr; API
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        <div className="space-y-1.5">
          <Label htmlFor="account-name">Название аккаунта</Label>
          <Input
            id="account-name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Мой магазин"
            required
          />
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="wb-token">API-токен Wildberries</Label>
          <textarea
            id="wb-token"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Вставьте токен из личного кабинета WB"
            required
            rows={4}
            className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50 resize-none"
          />
        </div>

        <div
          className="rounded-lg p-3 text-xs text-muted-foreground"
          style={{ backgroundColor: 'rgba(0,200,255,.06)', border: '1px solid rgba(0,200,255,.15)' }}
        >
          Токен нужен для получения данных о товарах и управления ценами. Мы не храним его в открытом виде.
        </div>

        <div className="flex flex-col gap-2 pt-2">
          <Button type="submit" disabled={loading} className="w-full gap-2">
            {loading
              ? <><Loader2 className="h-4 w-4 animate-spin" /> Подключение...</>
              : <>Подключить аккаунт <ArrowRight className="h-4 w-4" /></>
            }
          </Button>
          <Button
            type="button"
            variant="ghost"
            onClick={onSkip}
            className="w-full gap-2 text-muted-foreground"
          >
            <SkipForward className="h-4 w-4" /> Пропустить
          </Button>
        </div>
      </form>
    </div>
  );
}

/* ─── Step 3: Done ─── */
function StepDone() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const handleFinish = async () => {
    setLoading(true);
    try {
      await api.post('/api/auth/complete-onboarding');
      navigate('/');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Ошибка завершения настройки');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="text-center">
      {/* Success icon */}
      <div
        className="mx-auto mb-6 flex h-20 w-20 items-center justify-center rounded-full animate-bounce-slow"
        style={{
          background: 'linear-gradient(135deg, rgba(0,200,255,.15), rgba(99,102,241,.1))',
          boxShadow: '0 4px 32px rgba(0,200,255,.2)',
        }}
      >
        <CheckCircle2 className="h-10 w-10" style={{ color: '#00c8ff' }} />
      </div>

      <h2 className="text-2xl font-bold mb-2">Всё готово!</h2>
      <p className="text-muted-foreground mb-8 max-w-sm mx-auto">
        Переходите к управлению ценами. Добавьте товары, настройте стратегии репрайсинга и запустите автоматизацию.
      </p>

      <Button onClick={handleFinish} disabled={loading} className="w-full gap-2" size="lg">
        {loading
          ? <><Loader2 className="h-4 w-4 animate-spin" /> Переход...</>
          : <>Перейти в панель <ArrowRight className="h-4 w-4" /></>
        }
      </Button>
    </div>
  );
}

/* ─── Main Onboarding Page ─── */
export default function OnboardingPage() {
  const [step, setStep] = useState(1);

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-lg">
        <StepIndicator current={step} />

        <Card
          className="overflow-hidden"
          style={{ boxShadow: '0 4px 32px rgba(0,200,255,.08)' }}
        >
          <CardContent className="p-8">
            <div
              key={step}
              className="animate-fade-in-up"
            >
              {step === 1 && <StepWelcome onNext={() => setStep(2)} />}
              {step === 2 && (
                <StepConnect
                  onNext={() => setStep(3)}
                  onSkip={() => setStep(3)}
                />
              )}
              {step === 3 && <StepDone />}
            </div>
          </CardContent>
        </Card>

        <p className="mt-6 text-center text-xs text-muted-foreground">
          QUASAR-X &mdash; интеллектуальный репрайсер для Wildberries
        </p>
      </div>
    </div>
  );
}

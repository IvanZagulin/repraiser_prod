import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Loader2, Zap, ArrowLeft } from 'lucide-react';
import { verifyEmail, resendCode } from '../api/client';
import { useAuthStore } from '../store';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';

export default function VerifyEmailPage() {
  const location   = useLocation();
  const navigate   = useNavigate();
  const loginStore = useAuthStore((state) => state.login);

  const email = location.state?.email;
  const [code,        setCode]        = useState(['', '', '', '', '', '']);
  const [loading,     setLoading]     = useState(false);
  const [resendTimer, setResendTimer] = useState(60);
  const inputRefs = useRef([]);

  useEffect(() => {
    if (!email) navigate('/register', { replace: true });
  }, [email, navigate]);

  useEffect(() => {
    if (resendTimer > 0) {
      const t = setTimeout(() => setResendTimer((s) => s - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [resendTimer]);

  const handleChange = (index, value) => {
    const digit = value.replace(/\D/g, '').slice(-1);
    const next = [...code]; next[index] = digit; setCode(next);
    if (digit && index < 5) inputRefs.current[index + 1]?.focus();
  };
  const handleKeyDown = (index, e) => {
    if (e.key === 'Backspace' && !code[index] && index > 0)
      inputRefs.current[index - 1]?.focus();
  };
  const handlePaste = (e) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    if (!pasted) return;
    const next = [...code];
    for (let i = 0; i < 6; i++) next[i] = pasted[i] || '';
    setCode(next);
    inputRefs.current[Math.min(pasted.length, 5)]?.focus();
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    const fullCode = code.join('');
    if (fullCode.length !== 6) { toast.error('Введите все 6 цифр кода'); return; }
    setLoading(true);
    try {
      const response = await verifyEmail(email, fullCode);
      loginStore(response.data.access_token);
      toast.success('Email подтверждён! Добро пожаловать!');
      navigate('/');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Неверный или истёкший код');
      setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } finally { setLoading(false); }
  };

  const handleResend = async () => {
    try {
      await resendCode(email);
      toast.success('Новый код отправлен на ' + email);
      setResendTimer(60); setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } catch { toast.error('Ошибка отправки кода'); }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm animate-fade-in-up">

        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-primary">
            <Zap className="h-5 w-5 text-primary-foreground" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Подтверждение email</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            Введите код, отправленный на{' '}
            <span className="text-foreground font-medium">{email}</span>
          </p>
        </div>

        <Card>
          <CardContent className="pt-6">
            <form onSubmit={handleSubmit} className="space-y-5">
              <div className="space-y-2">
                <Label>Код подтверждения</Label>
                <div className="flex gap-2 justify-center">
                  {code.map((digit, i) => (
                    <input
                      key={i}
                      ref={(el) => (inputRefs.current[i] = el)}
                      type="text" inputMode="numeric" maxLength={1}
                      value={digit}
                      onChange={(e) => handleChange(i, e.target.value)}
                      onKeyDown={(e) => handleKeyDown(i, e)}
                      onPaste={handlePaste}
                      autoFocus={i === 0}
                      className={cn(
                        'h-12 w-10 rounded-md border text-center text-lg font-bold font-mono',
                        'bg-background text-foreground transition-colors',
                        'focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
                        digit ? 'border-primary' : 'border-input'
                      )}
                    />
                  ))}
                </div>
              </div>

              <Button type="submit" disabled={loading} className="w-full">
                {loading
                  ? <><Loader2 className="h-4 w-4 animate-spin" /> Проверяем...</>
                  : 'Подтвердить'
                }
              </Button>

              <p className="text-center text-xs text-muted-foreground">
                {resendTimer > 0
                  ? `Отправить повторно через ${resendTimer} сек.`
                  : (
                    <button
                      type="button" onClick={handleResend}
                      className="text-primary hover:underline underline-offset-4"
                    >
                      Отправить код повторно
                    </button>
                  )
                }
              </p>
            </form>
          </CardContent>
        </Card>

        <p className="mt-5 text-center text-sm">
          <Link to="/login" className="flex items-center justify-center gap-1.5 text-muted-foreground hover:text-foreground transition-colors">
            <ArrowLeft className="h-3.5 w-3.5" /> Вернуться ко входу
          </Link>
        </p>

      </div>
    </div>
  );
}

import { useState, useEffect, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { Loader2, Zap, ArrowLeft } from 'lucide-react';
import { forgotPassword, resetPassword } from '../api/client';
import { useAuthStore } from '../store';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Card, CardContent } from '@/components/ui/card';
import { cn } from '@/lib/utils';

export default function ForgotPasswordPage() {
  const navigate   = useNavigate();
  const loginStore = useAuthStore((state) => state.login);

  const [step,            setStep]            = useState('email');
  const [email,           setEmail]           = useState('');
  const [code,            setCode]            = useState(['', '', '', '', '', '']);
  const [newPassword,     setNewPassword]     = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading,         setLoading]         = useState(false);
  const [resendTimer,     setResendTimer]     = useState(0);
  const inputRefs = useRef([]);

  useEffect(() => {
    if (resendTimer > 0) {
      const t = setTimeout(() => setResendTimer((s) => s - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [resendTimer]);

  const handleSendCode = async (e) => {
    e.preventDefault();
    if (!email.trim()) { toast.error('Введите email'); return; }
    setLoading(true);
    try {
      await forgotPassword(email.trim().toLowerCase());
      toast.success('Код отправлен на ' + email);
      setStep('reset'); setResendTimer(60);
      setTimeout(() => inputRefs.current[0]?.focus(), 100);
    } catch { toast.error('Ошибка отправки. Попробуйте позже.'); }
    finally { setLoading(false); }
  };

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
  const handleResend = async () => {
    try {
      await forgotPassword(email);
      toast.success('Новый код отправлен');
      setResendTimer(60); setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } catch { toast.error('Ошибка отправки кода'); }
  };

  const handleReset = async (e) => {
    e.preventDefault();
    const fullCode = code.join('');
    if (fullCode.length !== 6)    { toast.error('Введите все 6 цифр кода'); return; }
    if (newPassword.length < 8)   { toast.error('Пароль минимум 8 символов'); return; }
    if (newPassword !== confirmPassword) { toast.error('Пароли не совпадают'); return; }
    setLoading(true);
    try {
      const response = await resetPassword(email, fullCode, newPassword);
      loginStore(response.data.access_token);
      toast.success('Пароль успешно изменён!');
      navigate('/');
    } catch (err) {
      toast.error(err.response?.data?.detail || 'Неверный или истёкший код');
      setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } finally { setLoading(false); }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-sm animate-fade-in-up">

        {/* Brand */}
        <div className="mb-8 text-center">
          <div className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-xl bg-primary">
            <Zap className="h-5 w-5 text-primary-foreground" />
          </div>
          <h1 className="text-2xl font-semibold tracking-tight">Repraiser</h1>
          <p className="mt-1.5 text-sm text-muted-foreground">
            {step === 'email' ? 'Восстановление пароля' : 'Новый пароль'}
          </p>
        </div>

        <Card>
          <CardContent className="pt-6">
            {step === 'email' ? (
              <form onSubmit={handleSendCode} className="space-y-4">
                <p className="text-sm text-muted-foreground">
                  Введите email — пришлём код для сброса пароля
                </p>
                <div className="space-y-1.5">
                  <Label htmlFor="email">Email</Label>
                  <Input
                    id="email" type="email" value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    placeholder="your@email.com" required autoFocus
                  />
                </div>
                <Button type="submit" disabled={loading} className="w-full">
                  {loading ? <><Loader2 className="h-4 w-4 animate-spin" /> Отправляем...</> : 'Отправить код'}
                </Button>
              </form>
            ) : (
              <form onSubmit={handleReset} className="space-y-4">
                <p className="text-sm text-muted-foreground text-center">
                  Код отправлен на <span className="text-foreground font-medium">{email}</span>
                </p>

                {/* Digit inputs */}
                <div className="space-y-1.5">
                  <Label>Код из письма</Label>
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

                <div className="space-y-1.5">
                  <Label htmlFor="newpw">Новый пароль</Label>
                  <Input
                    id="newpw" type="password" value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    placeholder="Минимум 8 символов" required
                  />
                </div>

                <div className="space-y-1.5">
                  <Label htmlFor="confirmpw">Подтвердите пароль</Label>
                  <Input
                    id="confirmpw" type="password" value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="Повторите пароль" required
                  />
                </div>

                <Button type="submit" disabled={loading} className="w-full">
                  {loading
                    ? <><Loader2 className="h-4 w-4 animate-spin" /> Сохраняем...</>
                    : 'Сохранить новый пароль'
                  }
                </Button>

                {/* Resend */}
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
            )}
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

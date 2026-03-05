import { useState, useEffect, useRef } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { forgotPassword, resetPassword } from '../api/client';
import { useAuthStore } from '../store';

const cardStyle = {
  minHeight: '100vh',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'linear-gradient(135deg, #0f0f1a 0%, #1a1a2e 50%, #16213e 100%)',
  padding: '1rem',
};

const glassStyle = {
  background: 'rgba(255,255,255,0.03)',
  backdropFilter: 'blur(20px)',
  borderRadius: '1.5rem',
  border: '1px solid rgba(0,240,255,0.15)',
  padding: '2.5rem',
  width: '100%',
  maxWidth: '420px',
  boxShadow: '0 25px 50px rgba(0,0,0,0.5)',
};

const inputStyle = {
  width: '100%',
  padding: '0.75rem 1rem',
  background: 'rgba(255,255,255,0.05)',
  border: '1.5px solid rgba(0,240,255,0.15)',
  borderRadius: '0.75rem',
  color: '#f9fafb',
  fontSize: '0.9375rem',
  outline: 'none',
  boxSizing: 'border-box',
};

const digitInputStyle = {
  width: '48px',
  height: '56px',
  textAlign: 'center',
  fontSize: '1.5rem',
  fontWeight: '700',
  fontFamily: 'monospace',
  background: 'rgba(255,255,255,0.05)',
  border: '1.5px solid rgba(0,240,255,0.2)',
  borderRadius: '0.75rem',
  color: '#f9fafb',
  outline: 'none',
  caretColor: '#00f0ff',
};

const labelStyle = {
  display: 'block',
  color: '#9ca3af',
  fontSize: '0.8125rem',
  marginBottom: '0.5rem',
  fontWeight: '500',
};

const btnStyle = (disabled) => ({
  width: '100%',
  padding: '0.875rem',
  background: disabled
    ? 'rgba(124,58,237,0.3)'
    : 'linear-gradient(135deg, #00f0ff, #7c3aed)',
  color: '#fff',
  fontWeight: '700',
  fontSize: '0.9375rem',
  border: 'none',
  borderRadius: '0.75rem',
  cursor: disabled ? 'not-allowed' : 'pointer',
  letterSpacing: '0.5px',
  marginTop: '1.5rem',
});

export default function ForgotPasswordPage() {
  const navigate = useNavigate();
  const loginStore = useAuthStore((state) => state.login);

  const [step, setStep] = useState('email'); // 'email' | 'reset'
  const [email, setEmail] = useState('');
  const [code, setCode] = useState(['', '', '', '', '', '']);
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [resendTimer, setResendTimer] = useState(0);
  const inputRefs = useRef([]);

  useEffect(() => {
    if (resendTimer > 0) {
      const t = setTimeout(() => setResendTimer((s) => s - 1), 1000);
      return () => clearTimeout(t);
    }
  }, [resendTimer]);

  // ── Step 1: send code ──────────────────────────────────────────────────────

  const handleSendCode = async (e) => {
    e.preventDefault();
    if (!email.trim()) { toast.error('Введите email'); return; }
    setLoading(true);
    try {
      await forgotPassword(email.trim().toLowerCase());
      toast.success('Код отправлен на ' + email);
      setStep('reset');
      setResendTimer(60);
      setTimeout(() => inputRefs.current[0]?.focus(), 100);
    } catch {
      toast.error('Ошибка отправки. Попробуйте позже.');
    } finally {
      setLoading(false);
    }
  };

  // ── Step 2: code digits ────────────────────────────────────────────────────

  const handleChange = (index, value) => {
    const digit = value.replace(/\D/g, '').slice(-1);
    const next = [...code];
    next[index] = digit;
    setCode(next);
    if (digit && index < 5) inputRefs.current[index + 1]?.focus();
  };

  const handleKeyDown = (index, e) => {
    if (e.key === 'Backspace' && !code[index] && index > 0) {
      inputRefs.current[index - 1]?.focus();
    }
  };

  const handlePaste = (e) => {
    e.preventDefault();
    const pasted = e.clipboardData.getData('text').replace(/\D/g, '').slice(0, 6);
    if (!pasted) return;
    const next = [...code];
    for (let i = 0; i < 6; i++) next[i] = pasted[i] || '';
    setCode(next);
    const lastFilled = Math.min(pasted.length, 5);
    inputRefs.current[lastFilled]?.focus();
  };

  const handleResend = async () => {
    try {
      await forgotPassword(email);
      toast.success('Новый код отправлен');
      setResendTimer(60);
      setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } catch {
      toast.error('Ошибка отправки кода');
    }
  };

  // ── Step 2: reset password ─────────────────────────────────────────────────

  const handleReset = async (e) => {
    e.preventDefault();
    const fullCode = code.join('');
    if (fullCode.length !== 6) { toast.error('Введите все 6 цифр кода'); return; }
    if (newPassword.length < 6) { toast.error('Пароль минимум 6 символов'); return; }
    if (newPassword !== confirmPassword) { toast.error('Пароли не совпадают'); return; }

    setLoading(true);
    try {
      const response = await resetPassword(email, fullCode, newPassword);
      loginStore(response.data.access_token);
      toast.success('Пароль успешно изменён!');
      navigate('/');
    } catch (err) {
      const detail = err.response?.data?.detail;
      toast.error(detail || 'Неверный или истёкший код');
      setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } finally {
      setLoading(false);
    }
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div style={cardStyle}>
      <div style={glassStyle}>
        {/* Logo */}
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <div style={{
            fontSize: '1.75rem', fontWeight: '900', letterSpacing: '3px',
            background: 'linear-gradient(135deg, #00f0ff, #7c3aed)',
            WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
          }}>
            QUASAR-X
          </div>
          <div style={{ color: '#6b7280', fontSize: '0.6875rem', letterSpacing: '4px', marginTop: '4px' }}>
            REPRICER
          </div>
        </div>

        {step === 'email' ? (
          <>
            <h2 style={{ color: '#f9fafb', fontSize: '1.25rem', fontWeight: '700', margin: '0 0 0.5rem', textAlign: 'center' }}>
              Восстановление пароля
            </h2>
            <p style={{ color: '#6b7280', fontSize: '0.875rem', textAlign: 'center', margin: '0 0 2rem' }}>
              Введите email — пришлём код для сброса
            </p>

            <form onSubmit={handleSendCode}>
              <div style={{ marginBottom: '1rem' }}>
                <label style={labelStyle}>Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="your@email.com"
                  required
                  autoFocus
                  style={inputStyle}
                />
              </div>
              <button type="submit" style={btnStyle(loading)} disabled={loading}>
                {loading ? 'Отправляем...' : 'Отправить код'}
              </button>
            </form>
          </>
        ) : (
          <>
            <h2 style={{ color: '#f9fafb', fontSize: '1.25rem', fontWeight: '700', margin: '0 0 0.5rem', textAlign: 'center' }}>
              Новый пароль
            </h2>
            <p style={{ color: '#6b7280', fontSize: '0.875rem', textAlign: 'center', margin: '0 0 1.5rem' }}>
              Код отправлен на <span style={{ color: '#00f0ff' }}>{email}</span>
            </p>

            <form onSubmit={handleReset}>
              {/* Code digits */}
              <div style={{ marginBottom: '1.5rem' }}>
                <label style={labelStyle}>Код из письма</label>
                <div style={{ display: 'flex', gap: '8px', justifyContent: 'center' }}>
                  {code.map((digit, i) => (
                    <input
                      key={i}
                      ref={(el) => (inputRefs.current[i] = el)}
                      type="text"
                      inputMode="numeric"
                      maxLength={1}
                      value={digit}
                      onChange={(e) => handleChange(i, e.target.value)}
                      onKeyDown={(e) => handleKeyDown(i, e)}
                      onPaste={handlePaste}
                      style={{
                        ...digitInputStyle,
                        borderColor: digit ? 'rgba(0,240,255,0.6)' : 'rgba(0,240,255,0.2)',
                        boxShadow: digit ? '0 0 8px rgba(0,240,255,0.2)' : 'none',
                      }}
                    />
                  ))}
                </div>
              </div>

              {/* New password */}
              <div style={{ marginBottom: '1rem' }}>
                <label style={labelStyle}>Новый пароль</label>
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Минимум 6 символов"
                  required
                  style={inputStyle}
                />
              </div>

              {/* Confirm */}
              <div style={{ marginBottom: '0.5rem' }}>
                <label style={labelStyle}>Подтверждение пароля</label>
                <input
                  type="password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="Повторите пароль"
                  required
                  style={inputStyle}
                />
              </div>

              <button type="submit" style={btnStyle(loading)} disabled={loading}>
                {loading ? 'Сохраняем...' : 'Сохранить новый пароль'}
              </button>
            </form>

            {/* Resend */}
            <div style={{ textAlign: 'center', marginTop: '1.25rem' }}>
              {resendTimer > 0 ? (
                <span style={{ color: '#4b5563', fontSize: '0.8125rem' }}>
                  Отправить повторно через {resendTimer} сек.
                </span>
              ) : (
                <button
                  onClick={handleResend}
                  style={{
                    background: 'none', border: 'none', color: '#7c3aed',
                    fontSize: '0.8125rem', cursor: 'pointer', fontWeight: '600',
                  }}
                >
                  Отправить код повторно
                </button>
              )}
            </div>
          </>
        )}

        <div style={{
          borderTop: '1px solid rgba(255,255,255,0.06)',
          marginTop: '1.5rem', paddingTop: '1.5rem',
          textAlign: 'center', fontSize: '0.8125rem', color: '#4b5563',
        }}>
          <Link to="/login" style={{ color: '#6b7280', textDecoration: 'none' }}>
            Вернуться ко входу
          </Link>
        </div>
      </div>
    </div>
  );
}

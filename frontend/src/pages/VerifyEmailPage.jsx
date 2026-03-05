import { useState, useEffect, useRef } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { verifyEmail, resendCode } from '../api/client';
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

export default function VerifyEmailPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const loginStore = useAuthStore((state) => state.login);

  const email = location.state?.email;
  const [code, setCode] = useState(['', '', '', '', '', '']);
  const [loading, setLoading] = useState(false);
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
    const next = [...code];
    next[index] = digit;
    setCode(next);
    if (digit && index < 5) {
      inputRefs.current[index + 1]?.focus();
    }
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

  const handleSubmit = async (e) => {
    e.preventDefault();
    const fullCode = code.join('');
    if (fullCode.length !== 6) {
      toast.error('Введите все 6 цифр кода');
      return;
    }
    setLoading(true);
    try {
      const response = await verifyEmail(email, fullCode);
      loginStore(response.data.access_token);
      toast.success('Email подтверждён! Добро пожаловать!');
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

  const handleResend = async () => {
    try {
      await resendCode(email);
      toast.success('Новый код отправлен на ' + email);
      setResendTimer(60);
      setCode(['', '', '', '', '', '']);
      inputRefs.current[0]?.focus();
    } catch {
      toast.error('Ошибка отправки кода');
    }
  };

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

        <h2 style={{ color: '#f9fafb', fontSize: '1.25rem', fontWeight: '700', margin: '0 0 0.5rem', textAlign: 'center' }}>
          Подтверждение email
        </h2>
        <p style={{ color: '#6b7280', fontSize: '0.875rem', textAlign: 'center', margin: '0 0 2rem' }}>
          Введите 6-значный код, отправленный на<br />
          <span style={{ color: '#00f0ff' }}>{email}</span>
        </p>

        <form onSubmit={handleSubmit}>
          {/* 6 digit inputs */}
          <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', marginBottom: '0.5rem' }}>
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
                autoFocus={i === 0}
              />
            ))}
          </div>

          <button type="submit" style={btnStyle(loading)} disabled={loading}>
            {loading ? 'Проверяем...' : 'Подтвердить'}
          </button>
        </form>

        {/* Resend */}
        <div style={{ textAlign: 'center', marginTop: '1.5rem' }}>
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

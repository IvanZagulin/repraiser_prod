import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import toast from 'react-hot-toast';
import { register } from '../api/client';

export default function RegisterPage() {
  const [email, setEmail]       = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm]   = useState('');
  const [loading, setLoading]   = useState(false);

  const navigate = useNavigate();

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (password !== confirm) {
      toast.error('Пароли не совпадают');
      return;
    }
    if (password.length < 6) {
      toast.error('Пароль должен быть минимум 6 символов');
      return;
    }
    setLoading(true);
    try {
      await register(email, password);
      toast.success('Код подтверждения отправлен на ваш email');
      navigate('/verify-email', { state: { email: email.trim().toLowerCase() } });
    } catch (err) {
      const detail = err.response?.data?.detail;
      toast.error(detail || 'Ошибка регистрации');
    } finally {
      setLoading(false);
    }
  };

  const inputStyle = {
    display: 'block', width: '100%',
    padding: '.65rem 1rem',
    background: 'rgba(10,10,18,0.9)',
    border: '1px solid rgba(0,240,255,0.18)',
    borderRadius: '.625rem',
    color: '#e8e8f0', fontSize: '.9rem',
    outline: 'none', fontFamily: 'Inter, system-ui, sans-serif',
    transition: 'border-color .18s, box-shadow .18s',
    boxSizing: 'border-box',
  };

  const focusHandler = (e) => {
    e.target.style.borderColor = '#00f0ff';
    e.target.style.boxShadow   = '0 0 0 3px rgba(0,240,255,0.1), 0 0 14px rgba(0,240,255,0.15)';
  };
  const blurHandler = (e) => {
    e.target.style.borderColor = 'rgba(0,240,255,0.18)';
    e.target.style.boxShadow   = 'none';
  };

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        background: '#0a0a12',
        backgroundImage:
          'radial-gradient(ellipse at 15% 60%, rgba(0,240,255,0.07) 0%, transparent 50%),' +
          'radial-gradient(ellipse at 85% 20%, rgba(255,0,212,0.07) 0%, transparent 50%),' +
          'radial-gradient(ellipse at 50% 100%, rgba(0,102,255,0.05) 0%, transparent 50%)',
        padding: '1rem',
        fontFamily: 'Inter, system-ui, sans-serif',
      }}
    >
      {/* Scanlines overlay */}
      <div
        style={{
          position: 'fixed', inset: 0, pointerEvents: 'none', zIndex: 0,
          backgroundImage:
            'repeating-linear-gradient(0deg, transparent, transparent 2px, rgba(0,0,0,0.06) 2px, rgba(0,0,0,0.06) 4px)',
        }}
      />

      <div style={{ maxWidth: '400px', width: '100%', position: 'relative', zIndex: 1 }}>

        {/* Brand */}
        <div style={{ textAlign: 'center', marginBottom: '2rem' }}>
          <h1
            style={{
              fontFamily: "'Orbitron', sans-serif",
              fontSize: '2.5rem', fontWeight: 900,
              letterSpacing: '0.12em', textTransform: 'uppercase',
              background: 'linear-gradient(135deg, #00f0ff 0%, #0066ff 50%, #ff00d4 100%)',
              WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent', backgroundClip: 'text',
              margin: 0, lineHeight: 1.1,
              filter: 'drop-shadow(0 0 28px rgba(0,240,255,0.4))',
            }}
          >
            QUASAR-X
          </h1>
          <p
            style={{
              color: '#555577', fontSize: '0.6875rem', fontWeight: 600,
              letterSpacing: '0.28em', textTransform: 'uppercase', marginTop: '0.625rem',
            }}
          >
            Создать аккаунт
          </p>
        </div>

        {/* Card */}
        <div
          style={{
            background: 'rgba(26,26,46,0.85)',
            border: '1px solid rgba(0,240,255,0.12)',
            borderRadius: '1.375rem',
            padding: '2rem',
            backdropFilter: 'blur(28px)',
            WebkitBackdropFilter: 'blur(28px)',
            boxShadow:
              '0 0 40px rgba(0,240,255,0.04),' +
              '0 24px 64px rgba(0,0,0,0.7),' +
              'inset 0 1px 0 rgba(0,240,255,0.08)',
          }}
        >
          <form onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: '1.25rem' }}>

            {/* Email */}
            <div>
              <label style={{
                display: 'block', fontSize: '0.6875rem', fontWeight: 700,
                color: '#555577', marginBottom: '0.4rem',
                letterSpacing: '0.14em', textTransform: 'uppercase',
              }}>
                Email
              </label>
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="your@email.com"
                required
                style={inputStyle}
                onFocus={focusHandler}
                onBlur={blurHandler}
              />
            </div>

            {/* Пароль */}
            <div>
              <label style={{
                display: 'block', fontSize: '0.6875rem', fontWeight: 700,
                color: '#555577', marginBottom: '0.4rem',
                letterSpacing: '0.14em', textTransform: 'uppercase',
              }}>
                Пароль
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Минимум 6 символов"
                required
                style={inputStyle}
                onFocus={focusHandler}
                onBlur={blurHandler}
              />
            </div>

            {/* Подтверждение */}
            <div>
              <label style={{
                display: 'block', fontSize: '0.6875rem', fontWeight: 700,
                color: '#555577', marginBottom: '0.4rem',
                letterSpacing: '0.14em', textTransform: 'uppercase',
              }}>
                Подтвердите пароль
              </label>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                placeholder="••••••••"
                required
                style={{
                  ...inputStyle,
                  borderColor: confirm && confirm !== password
                    ? 'rgba(255,60,60,0.5)'
                    : 'rgba(0,240,255,0.18)',
                }}
                onFocus={focusHandler}
                onBlur={blurHandler}
              />
              {confirm && confirm !== password && (
                <p style={{ color: '#ff6060', fontSize: '0.75rem', marginTop: '0.3rem' }}>
                  Пароли не совпадают
                </p>
              )}
            </div>

            {/* Submit */}
            <button
              type="submit"
              disabled={loading}
              style={{
                marginTop: '0.25rem',
                width: '100%', padding: '.8rem',
                border: 'none', borderRadius: '.875rem',
                cursor: loading ? 'not-allowed' : 'pointer',
                background: loading
                  ? 'rgba(0,240,255,0.08)'
                  : 'linear-gradient(135deg, #00f0ff 0%, #0066ff 55%, #ff00d4 100%)',
                color: loading ? '#555577' : '#0a0a12',
                fontWeight: 800, fontSize: '.9375rem',
                letterSpacing: '0.1em', textTransform: 'uppercase',
                fontFamily: 'Inter, system-ui, sans-serif',
                boxShadow: loading ? 'none' : '0 4px 24px rgba(0,240,255,0.4)',
                transition: 'all .2s',
              }}
            >
              {loading ? 'Создание аккаунта...' : 'Зарегистрироваться'}
            </button>
          </form>

          {/* Divider */}
          <div style={{
            margin: '1.5rem 0 1rem',
            height: '1px',
            background: 'linear-gradient(90deg, transparent, rgba(0,240,255,0.18), transparent)',
          }} />

          <p style={{
            textAlign: 'center', fontSize: '0.6875rem',
            color: '#555577', letterSpacing: '0.05em',
          }}>
            Уже есть аккаунт?{' '}
            <Link
              to="/login"
              style={{ color: '#00f0ff', textDecoration: 'none', fontWeight: 700 }}
            >
              Войти
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}

/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        primary: {
          50:  '#faf5ff',
          100: '#f3e8ff',
          200: '#e9d5ff',
          300: '#d8b4fe',
          400: '#c084fc',
          500: '#a855f7',
          600: '#9333ea',
          700: '#7e22ce',
          800: '#6b21a8',
          900: '#581c87',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
      },
      animation: {
        'gradient-shift': 'gradientShift 5s ease infinite',
        'float':          'float 6s ease-in-out infinite',
        'float-slow':     'floatSlow 10s ease-in-out infinite',
        'fade-in':        'fadeIn .3s ease backwards',
        'fade-in-up':     'fadeInUp .4s ease backwards',
        'fade-in-scale':  'fadeInScale .25s ease backwards',
        'slide-in-left':  'slideInLeft .3s ease backwards',
        'number-pop':     'numberPop .5s cubic-bezier(0.34,1.56,0.64,1) backwards',
        'orb-float':      'orbFloat 12s ease-in-out infinite',
        'shimmer':        'shimmer 1.6s ease-in-out infinite',
        'border-pulse':   'borderPulse 2s ease-in-out infinite',
        'spin-slow':      'spin 2s linear infinite',
      },
      keyframes: {
        gradientShift: {
          '0%,100%': { backgroundPosition: '0% 50%' },
          '50%':      { backgroundPosition: '100% 50%' },
        },
        float: {
          '0%,100%': { transform: 'translateY(0px)' },
          '50%':     { transform: 'translateY(-8px)' },
        },
        floatSlow: {
          '0%,100%': { transform: 'translateY(0px)' },
          '50%':     { transform: 'translateY(-14px)' },
        },
        fadeIn: {
          from: { opacity: '0' },
          to:   { opacity: '1' },
        },
        fadeInUp: {
          from: { opacity: '0', transform: 'translateY(18px)' },
          to:   { opacity: '1', transform: 'none' },
        },
        fadeInScale: {
          from: { opacity: '0', transform: 'scale(.96)' },
          to:   { opacity: '1', transform: 'none' },
        },
        slideInLeft: {
          from: { opacity: '0', transform: 'translateX(-16px)' },
          to:   { opacity: '1', transform: 'none' },
        },
        shimmer: {
          from: { transform: 'translateX(-100%)' },
          to:   { transform: 'translateX(200%)' },
        },
        numberPop: {
          from: { opacity: '0', transform: 'translateY(8px) scale(.9)' },
          to:   { opacity: '1', transform: 'none' },
        },
        orbFloat: {
          '0%,100%': { transform: 'translate(0,0) scale(1)' },
          '33%':     { transform: 'translate(20px,-15px) scale(1.05)' },
          '66%':     { transform: 'translate(-10px,10px) scale(.97)' },
        },
        borderPulse: {
          '0%,100%': { borderColor: 'rgba(147,51,234,0.25)' },
          '50%':     { borderColor: 'rgba(147,51,234,0.6)' },
        },
      },
      backgroundSize: {
        '200%': '200% 200%',
      },
      backdropBlur: {
        xs: '2px',
      },
    },
  },
  plugins: [],
}

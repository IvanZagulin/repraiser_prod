# WB Repricer Frontend

React приложение для управления системой автоматического ценообразования.

## Технологии

- **React 18** - UI фреймворк
- **Vite** - Build tool
- **Tailwind CSS** - Стилизация
- **React Router** - Роутинг
- **Zustand** - State management
- **Axios** - HTTP клиент
- **React Hot Toast** - Уведомления
- **Lucide React** - Иконки
- **date-fns** - Работа с датами

## Структура

```
src/
├── main.jsx              # Точка входа
├── App.jsx               # Главный компонент с роутингом
├── index.css             # Глобальные стили + Tailwind
│
├── api/
│   └── client.js         # API клиент (axios)
│
├── store/
│   └── index.js          # Zustand stores
│
├── components/
│   ├── Layout.jsx        # Layout с сайдбаром
│   ├── ProductModal.jsx  # Добавление товара
│   └── ConfigModal.jsx   # Настройка репрайсера
│
└── pages/
    ├── Login.jsx         # Страница входа
    ├── Dashboard.jsx     # Главная
    ├── Products.jsx      # Управление товарами
    ├── Accounts.jsx      # Аккаунты WB
    ├── Runs.jsx          # Прогоны репрайсера
    └── ImportExport.jsx  # Импорт/Экспорт Excel
```

## Быстрый старт

### Локальная разработка (без Docker)

```bash
cd frontend

# Установить зависимости
npm install

# Запустить dev server
npm run dev
```

Приложение будет доступно на http://localhost:5173

### С Docker

```bash
# Из корня проекта
docker-compose up frontend
```

## Конфигурация

Создайте `.env` файл:

```bash
VITE_API_URL=http://localhost:8000
```

## Основные возможности

### 1. Авторизация
- JWT токены
- LocalStorage для хранения
- Автоматический редирект

### 2. Управление товарами
- Список с фильтрацией
- Добавление/удаление
- Настройка стратегий (RRP/Competitor)
- Ручной запуск репрайсера

### 3. Аккаунты WB
- Добавление аккаунтов
- Мониторинг срока токенов
- Предупреждения об истечении

### 4. Прогоны
- История запусков
- Статистика результатов
- Экспорт в Excel

### 5. Импорт/Экспорт
- Массовая загрузка стратегий
- Массовое добавление товаров
- Экспорт истории цен

## Компоненты

### Layout
Фиксированный сайдбар с навигацией:
- Главная
- Товары
- Прогоны
- Аккаунты
- Импорт/Экспорт

### ProductModal
Форма добавления товара:
- Выбор аккаунта
- nmID (Wildberries)
- SKU продавца
- Название

### ConfigModal
Настройка стратегии ценообразования:

**РРЦ:**
- Рекомендованная цена
- Допуск (tolerance)
- Min/Max цена

**Конкурент:**
- nmID конкурента
- Направление (выше/ниже)
- Смещение (рубли/проценты)
- Min/Max цена

## State Management

**useAuthStore:**
- token
- user
- login()
- logout()

**useProductsStore:**
- products
- filters
- setProducts()
- addProduct()
- removeProduct()

**useAccountsStore:**
- accounts
- setAccounts()
- addAccount()
- removeAccount()

## API Integration

Все запросы через `src/api/client.js`:

```javascript
// Авторизация
login(username, password)
verifyToken()

// Товары
getProducts(params)
createProduct(data)
createProductConfig(productId, data)
runRepricer(sellerSkus)

// Аккаунты
getAccounts()
createAccount(data)

// Экспорт
exportHistory(sellerSkus)
importStrategy(file)
```

## Стилизация

**Tailwind Utility Classes:**
- Используются везде для консистентности
- Кастомные компоненты в `index.css`

**Основные классы:**
```css
.btn              # Кнопки
.btn-primary      # Основная кнопка
.btn-secondary    # Вторичная кнопка
.btn-danger       # Опасная кнопка
.btn-sm           # Маленькая кнопка

.input            # Поле ввода
.card             # Карточка
.badge            # Бейдж
.badge-success    # Зеленый бейдж
.badge-warning    # Желтый бейдж
.badge-error      # Красный бейдж
```

## Production Build

```bash
npm run build
```

Сборка в папке `dist/`

## Troubleshooting

### Проблема: CORS ошибки

Убедитесь что backend запущен и CORS настроен:

```python
# backend/app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    ...
)
```

### Проблема: API не отвечает

Проверьте `.env` файл и убедитесь что `VITE_API_URL` правильный.

### Проблема: Токен не сохраняется

Очистите localStorage:

```javascript
localStorage.clear()
```

## Дефолтные креды

```
Логин: admin
Пароль: admin123
```

**⚠️ Измените в продакшене!**

FROM node:18-alpine

WORKDIR /app

# Копирование package файлов
COPY package*.json ./

# Установка зависимостей
RUN npm install

# Копирование исходников
COPY . .

EXPOSE 5173

# По умолчанию — build + preview (production)
# docker-compose может переопределить command если нужно
CMD ["sh", "-c", "npm run build && npm run preview -- --host 0.0.0.0 --port 5173"]

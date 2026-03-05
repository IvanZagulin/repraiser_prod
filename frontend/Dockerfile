FROM node:18-alpine

WORKDIR /app

# Копирование package файлов
COPY package*.json ./

# Установка зависимостей
RUN npm install

# Копирование исходников
COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev"]

# Stage 1: Build
FROM node:18-alpine AS builder

WORKDIR /app

# VITE_API_URL вшивается в JS бандл на этапе сборки
ARG VITE_API_URL=https://qvaz.quasar-x.ru
ENV VITE_API_URL=$VITE_API_URL

COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Stage 2: Serve with nginx
FROM nginx:alpine

# SPA routing: все 404 → index.html
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

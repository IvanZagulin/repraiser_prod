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

RUN printf 'server {\n\
    listen 80;\n\
    root /usr/share/nginx/html;\n\
    index index.html;\n\
\n\
    # gzip\n\
    gzip on;\n\
    gzip_types text/plain text/css application/json application/javascript text/xml application/xml image/svg+xml;\n\
\n\
    # Disable cache for JS/CSS to ensure fresh code on every deploy\n\
    location ~* \\.(js|css)$ {\n\
        expires -1;\n\
        add_header Cache-Control "no-store, no-cache, must-revalidate";\n\
    }\n\
\n\
    # Cache images/fonts\n\
    location ~* \\.(png|jpg|jpeg|gif|ico|svg|woff2?)$ {\n\
        expires 1y;\n\
        add_header Cache-Control "public, immutable";\n\
    }\n\
\n\
    # SPA fallback\n\
    location / {\n\
        try_files $uri $uri/ /index.html;\n\
    }\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80

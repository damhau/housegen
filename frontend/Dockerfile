# Frontend image: static Vite build served by nginx, which also proxies
# /api, /scenes and /kit to the backend service (same-origin, no CORS).
#
# Build from the repo root:  docker build -f frontend/Dockerfile -t housegen-frontend .

FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run api:gen && npm run build

FROM nginx:1.27-alpine
COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80

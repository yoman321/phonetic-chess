# --- Stage 1: build the React/Vite bundle with Node ---
FROM node:20-alpine AS build

WORKDIR /app

# Install deps first for layer caching.
COPY package.json package-lock.json ./
RUN npm ci

# Vite inlines VITE_* env vars at build time, not runtime.
# Pass them as build args so the same image works for any env.
ARG VITE_API_BASE=/api
ARG VITE_SOCKET_URL=
ENV VITE_API_BASE=$VITE_API_BASE \
    VITE_SOCKET_URL=$VITE_SOCKET_URL

COPY . .
RUN npm run build

# --- Stage 2: serve the static bundle with Nginx ---
FROM nginx:1.27-alpine

# Replace the default nginx config with one that handles SPA routing
# and reverse-proxies /api and /socket.io to the backend container.
COPY nginx.conf /etc/nginx/conf.d/default.conf

# Only the built static files end up in the final image — no Node, no source.
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

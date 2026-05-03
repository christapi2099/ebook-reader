FROM node:22-alpine AS builder

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

# Production stage
FROM nginx:alpine

RUN rm -rf /etc/nginx/conf.d/*
COPY --from=builder /app/build /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

RUN adduser -D -u 1000 nginxuser && \
    chown -R nginxuser:nginxuser /var/cache/nginx /var/log/nginx /usr/share/nginx/html && \
    touch /var/run/nginx.pid && \
    chown nginxuser:nginxuser /var/run/nginx.pid

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost/health || exit 1

USER nginxuser
CMD ["nginx", "-g", "daemon off;"]

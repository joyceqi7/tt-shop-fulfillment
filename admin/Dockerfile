# syntax=docker/dockerfile:1.7

# === 1) deps：只装 prod deps，cache 友好 ===
FROM node:20-bookworm-slim AS deps
WORKDIR /app
COPY package.json ./
# 没 lockfile 时用 npm install；后续要稳定可加 npm ci
RUN npm install --omit=dev --no-fund --no-audit --loglevel=error

# === 2) builder：装 dev deps + 跑 next build ===
FROM node:20-bookworm-slim AS builder
WORKDIR /app
COPY package.json ./
RUN npm install --no-fund --no-audit --loglevel=error
COPY . .
# next.config.mjs 里的 rewrites destination 在 build 期被烘焙进 routes-manifest.json
# 运行时改 process.env.API_BASE_URL 已经无效，必须 build 期就设好
# 容器运行时 admin 在 docker compose 内网通过服务名访问 api 服务
ARG API_BASE_URL=http://api:8000
ENV NEXT_TELEMETRY_DISABLED=1 \
    API_BASE_URL=$API_BASE_URL
RUN npm run build

# === 3) runner：极薄运行时 ===
FROM node:20-bookworm-slim AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 非 root 用户跑应用；与后端容器对齐
RUN groupadd --system --gid 1001 nodejs \
 && useradd --system --uid 1001 --gid nodejs nextjs

# Next 14 standalone 输出已经把所需的 node_modules + server.js 拼好
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs
EXPOSE 3000

# Docker healthcheck 直接打 / ，redirect /dashboard 由内部完成
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD node -e "fetch('http://127.0.0.1:3000/login').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"

CMD ["node", "server.js"]

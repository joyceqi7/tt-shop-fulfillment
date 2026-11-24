/**
 * @type {import('next').NextConfig}
 *
 * standalone：让 next build 把所有运行时依赖打到 .next/standalone，
 *            Docker 镜像可以只 COPY 这一份，最终镜像 ~150MB（vs ~1GB full deps）
 *
 * rewrites：把 /api/admin/* 转发到后端 FastAPI（同源访问，cookie 直接生效，
 *           避免 cors 和 SameSite=Lax 在跨子域时的踩坑）。
 *           prod 用 caddy 在边界做路由；dev 用 next 自带 rewrite。
 */
const API_BASE = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

const nextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  async rewrites() {
    return [
      {
        source: "/api/admin/:path*",
        destination: `${API_BASE}/admin/:path*`,
      },
    ];
  },
};

export default nextConfig;

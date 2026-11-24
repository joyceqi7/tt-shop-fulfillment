import { NextResponse, type NextRequest } from "next/server";

const SESSION_COOKIE = "tts_admin_session";

const PUBLIC_PATHS = ["/login"];

/**
 * 第一道守卫：边缘层只看 cookie 是否存在，不做完整签名校验
 * （那个交给 (dashboard)/layout.tsx 里 SSR 调 /admin/auth/me 二次确认）。
 *
 * 这样设计的取舍：
 * - middleware 里要做 HMAC 校验得 import Node.js crypto，会触发 edge runtime 报错；
 *   即便切到 nodejs runtime 也意味着每次请求多一次 fetch 后端。
 * - 仅靠"cookie 存在"做粗筛已经能挡掉绝大多数未授权请求；
 *   伪造 cookie 走到 layout 那层会被 401 抛回，无安全风险。
 */
export function middleware(req: NextRequest) {
  const { pathname } = req.nextUrl;

  if (
    PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`)) ||
    pathname.startsWith("/api/") ||
    pathname.startsWith("/_next/") ||
    pathname === "/favicon.ico"
  ) {
    return NextResponse.next();
  }

  const cookie = req.cookies.get(SESSION_COOKIE);
  if (!cookie) {
    const url = req.nextUrl.clone();
    url.pathname = "/login";
    url.searchParams.set("next", pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};

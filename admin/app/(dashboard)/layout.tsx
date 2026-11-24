import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { Sidebar } from "@/components/layout/sidebar";
import { Topbar } from "@/components/layout/topbar";

const SESSION_COOKIE = "tts_admin_session";

/**
 * 服务端渲染时直接调后端 /admin/auth/me 验证 cookie。
 * 失败 → 重定向到 /login（中间件已经做过一次粗筛，这里是最终的防线）。
 *
 * 注意：在 SSR 里调后端必须用绝对地址，next.config 的 rewrite 只对浏览器侧生效。
 */
async function verifySession(): Promise<{ username: string } | null> {
  const cookie = cookies().get(SESSION_COOKIE);
  if (!cookie) return null;

  const apiBase = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  try {
    const res = await fetch(`${apiBase}/admin/auth/me`, {
      headers: {
        Cookie: `${SESSION_COOKIE}=${cookie.value}`,
      },
      cache: "no-store",
    });
    if (!res.ok) return null;
    return (await res.json()) as { username: string };
  } catch {
    return null;
  }
}

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await verifySession();
  if (!session) {
    redirect("/login");
  }

  return (
    <div className="flex min-h-screen">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar username={session.username} />
        <main className="flex-1 overflow-x-hidden p-6">{children}</main>
      </div>
    </div>
  );
}

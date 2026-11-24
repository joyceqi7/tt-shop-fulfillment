import { cookies } from "next/headers";
import { redirect } from "next/navigation";

/**
 * Server Component 专用 fetch：
 * - 直连后端绝对地址（API_BASE_URL，docker 内是 http://api:8000）
 * - 自动透传 tts_admin_session cookie
 * - 401 直接 redirect /login
 * - 不缓存（cache: "no-store"）
 */

const SESSION_COOKIE = "tts_admin_session";

export async function serverApi<T = unknown>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const apiBase = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";

  const cookieStore = cookies();
  const session = cookieStore.get(SESSION_COOKIE);

  const headers: Record<string, string> = {
    ...((init.headers as Record<string, string>) ?? {}),
  };
  if (session) {
    headers.Cookie = `${SESSION_COOKIE}=${session.value}`;
  }

  const url = path.startsWith("http") ? path : `${apiBase}${path}`;
  const res = await fetch(url, {
    ...init,
    headers,
    cache: "no-store",
  });

  if (res.status === 401) {
    redirect("/login");
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { detail?: string };
      if (typeof body?.detail === "string") {
        detail = body.detail;
      }
    } catch {
      // ignore
    }
    throw new Error(`serverApi ${path} failed: ${detail}`);
  }

  return (await res.json()) as T;
}

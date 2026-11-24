"use client";

/**
 * 客户端 fetch wrapper：
 * - 所有 admin API 都走 /api/admin/* 前缀（next.config.mjs 里 rewrite 到后端 /admin/*）
 * - cookie 自动带（同源）
 * - 401 自动跳 /login（除非 silent401: true，登录页自己处理）
 * - 错误统一抛 ApiError，含 status / detail
 */

const BASE = "/api/admin";

export class ApiError extends Error {
  status: number;
  detail: string;
  body?: unknown;

  constructor(status: number, detail: string, body?: unknown) {
    super(detail || `HTTP ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

export interface ApiInit extends Omit<RequestInit, "body"> {
  /** 自动 JSON.stringify；传字符串或 FormData 直接透传 */
  body?: unknown;
  /** true 则 401 不自动跳转，留给调用方处理（登录页用） */
  silent401?: boolean;
}

export async function api<T = unknown>(
  path: string,
  init: ApiInit = {},
): Promise<T> {
  const { body, silent401, headers, ...rest } = init;

  const finalHeaders: Record<string, string> = {
    ...((headers as Record<string, string>) ?? {}),
  };
  let finalBody: BodyInit | undefined;
  if (body !== undefined) {
    if (
      typeof body === "string" ||
      body instanceof FormData ||
      body instanceof Blob ||
      body instanceof URLSearchParams
    ) {
      finalBody = body as BodyInit;
    } else {
      finalBody = JSON.stringify(body);
      if (!finalHeaders["Content-Type"] && !finalHeaders["content-type"]) {
        finalHeaders["Content-Type"] = "application/json";
      }
    }
  }

  const res = await fetch(BASE + path, {
    ...rest,
    headers: finalHeaders,
    body: finalBody,
    credentials: "include",
  });

  if (res.status === 401 && !silent401) {
    if (typeof window !== "undefined") {
      const next = window.location.pathname + window.location.search;
      window.location.href = `/login?next=${encodeURIComponent(next)}`;
    }
  }

  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    let parsed: unknown;
    try {
      parsed = await res.json();
      const obj = parsed as Record<string, unknown>;
      if (typeof obj?.detail === "string") {
        detail = obj.detail;
      } else if (typeof obj?.message === "string") {
        detail = obj.message;
      }
    } catch {
      // body 不是 JSON，保留原 status
    }
    throw new ApiError(res.status, detail, parsed);
  }

  if (res.status === 204) {
    return undefined as T;
  }
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    return (await res.json()) as T;
  }
  return (await res.text()) as unknown as T;
}

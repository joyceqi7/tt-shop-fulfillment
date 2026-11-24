"use client";

import { Suspense, useState } from "react";
import Image from "next/image";
import { useRouter, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, api } from "@/lib/api";

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") || "/dashboard";

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api("/auth/login", {
        method: "POST",
        body: { username, password },
        silent401: true,
      });
      router.replace(next);
    } catch (err) {
      const msg =
        err instanceof ApiError ? err.detail : "网络异常，稍后重试";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }

  return (
    <form
      onSubmit={onSubmit}
      className="w-full max-w-sm space-y-5 rounded-xl border border-border bg-card p-7 shadow-sm animate-fade-in"
    >
      <div className="flex flex-col items-center gap-2">
        <div className="rounded-lg bg-white p-2">
          <Image
            src="/logo.png"
            alt="Game Code Hub"
            width={220}
            height={220}
            priority
            unoptimized
            className="h-auto w-[200px]"
          />
        </div>
        <p className="text-xs text-muted-foreground">内部运营管理 · Internal</p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="username">用户名</Label>
        <Input
          id="username"
          name="username"
          autoComplete="username"
          autoFocus
          required
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />
      </div>
      <div className="space-y-2">
        <Label htmlFor="password">密码</Label>
        <Input
          id="password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      ) : null}

      <Button type="submit" className="w-full" loading={loading}>
        登录
      </Button>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        多次失败会被临时锁定。如忘记密码，请联系系统管理员重新生成 bcrypt 哈希。
      </p>
    </form>
  );
}

export default function LoginPage() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </div>
  );
}

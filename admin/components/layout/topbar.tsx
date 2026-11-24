"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Boxes, Clapperboard, LogOut, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";

interface TopbarProps {
  username: string;
}

export function Topbar({ username }: TopbarProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [loggingOut, setLoggingOut] = useState(false);
  const isContent = pathname.startsWith("/content");

  async function handleLogout() {
    setLoggingOut(true);
    try {
      await api("/auth/logout", { method: "POST", silent401: true });
    } catch {
      // 即使后端报错也按本地登出处理
    } finally {
      setLoggingOut(false);
      router.replace("/login");
      router.refresh();
    }
  }

  function hardNavigate(
    e: React.MouseEvent<HTMLAnchorElement>,
    href: string,
  ) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    window.location.assign(href);
  }

  return (
    <header className="sticky top-0 z-30 flex h-12 items-center justify-between gap-4 border-b border-border bg-background/80 px-4 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="flex rounded-md border border-border bg-card p-0.5">
          <Link
            href="/dashboard"
            onClick={(e) => hardNavigate(e, "/dashboard")}
            className={
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors " +
              (!isContent ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
            }
          >
            <Boxes className="size-3.5" aria-hidden />
            库存管理
          </Link>
          <Link
            href="/content/ingest"
            onClick={(e) => hardNavigate(e, "/content/ingest")}
            className={
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs transition-colors " +
              (isContent ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground")
            }
          >
            <Clapperboard className="size-3.5" aria-hidden />
            内容创作
          </Link>
        </div>
        <div className="hidden items-center gap-2 text-xs text-muted-foreground lg:flex">
          <Search className="size-3.5" aria-hidden />
          <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-[10px]">
            ⌘K
          </kbd>
          <span>全局搜索（即将上线）</span>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <span className="text-xs text-muted-foreground">
          <span className="opacity-60">用户 ·</span>{" "}
          <span className="font-medium text-foreground">{username}</span>
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={handleLogout}
          loading={loggingOut}
          className="gap-1.5"
        >
          <LogOut className="size-3.5" aria-hidden />
          登出
        </Button>
      </div>
    </header>
  );
}

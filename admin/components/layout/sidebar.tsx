"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  ShoppingBag,
  Boxes,
  Tags,
  PackageSearch,
  Truck,
  Receipt,
  Wallet,
  ScrollText,
  Clapperboard,
  Film,
  Library,
  Wand2,
  ListChecks,
} from "lucide-react";

import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "看板", icon: LayoutDashboard },
  { href: "/orders", label: "订单", icon: ShoppingBag },
  { href: "/inventory", label: "库存", icon: Boxes },
  { href: "/skus", label: "SKU 映射", icon: Tags },
  { href: "/tts-products", label: "TT 商品", icon: PackageSearch },
  { href: "/suppliers", label: "供货商", icon: Truck },
  { href: "/purchases", label: "采购单", icon: Receipt },
  { href: "/finance", label: "财务", icon: Wallet },
  { href: "/audit", label: "审计日志", icon: ScrollText },
] as const;

const CONTENT_NAV = [
  { href: "/content/ingest", label: "素材采集", icon: Film },
  { href: "/content/library", label: "素材库", icon: Library },
  { href: "/content/generate", label: "视频生成", icon: Wand2 },
  { href: "/content/jobs", label: "任务监控", icon: ListChecks },
] as const;

interface SidebarProps {
  role?: string;
}

export function Sidebar({ role: _role }: SidebarProps = {}) {
  const pathname = usePathname();
  const isContent = pathname.startsWith("/content");
  const nav = isContent ? CONTENT_NAV : NAV;

  function hardNavigate(
    e: React.MouseEvent<HTMLAnchorElement>,
    href: string,
  ) {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
    e.preventDefault();
    window.location.assign(href);
  }

  return (
    <aside className="hidden w-56 shrink-0 border-r border-border bg-card md:flex md:flex-col">
      <div className="flex h-14 items-center gap-2 border-b border-border px-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md bg-white">
          <Image
            src="/logo.png"
            alt="Game Code Hub"
            width={64}
            height={64}
            unoptimized
            className="h-7 w-7 object-contain"
          />
        </div>
        <div className="min-w-0 leading-tight">
          <p className="truncate text-sm font-semibold tracking-tight">
            Game Code Hub
          </p>
          <p className="truncate text-[10px] uppercase tracking-wider text-muted-foreground">
            Admin Console
          </p>
        </div>
      </div>
      <nav className="flex-1 space-y-0.5 p-2">
        {isContent && (
          <div className="mb-2 flex items-center gap-2 px-2 py-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
            <Clapperboard className="size-3.5" aria-hidden />
            内容创作
          </div>
        )}
        {nav.map(({ href, label, icon: Icon }) => {
          const active =
            pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              onClick={(e) => hardNavigate(e, href)}
              className={cn(
                "group flex items-center gap-2.5 rounded-md px-2.5 py-1.5 text-sm transition-colors",
                active
                  ? "bg-accent text-accent-foreground"
                  : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
              )}
            >
              <Icon className="size-4" aria-hidden />
              <span>{label}</span>
            </Link>
          );
        })}
      </nav>
      <div className="border-t border-border p-3">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
          v0.1 · admin
        </p>
      </div>
    </aside>
  );
}

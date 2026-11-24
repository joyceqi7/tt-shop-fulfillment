"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  { href: "/inventory/summary", label: "SKU 汇总" },
  { href: "/inventory/batches", label: "导入批次" },
  { href: "/inventory/codes", label: "卡密明细" },
  { href: "/inventory/logs", label: "库存流水" },
] as const;

export function InventoryTabs() {
  const pathname = usePathname();
  return (
    <div className="flex gap-1 border-b border-border">
      {TABS.map((t) => {
        const active = pathname.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={cn(
              "border-b-2 px-3 py-2 text-xs transition-colors",
              active
                ? "border-foreground text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t.label}
          </Link>
        );
      })}
    </div>
  );
}

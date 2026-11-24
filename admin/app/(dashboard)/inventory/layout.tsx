import Link from "next/link";

import { PageHeader } from "@/components/layout/page-header";

import { InventoryTabs } from "./tabs";

export default function InventoryLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-4">
      <PageHeader
        title="库存"
        description="按 SKU 聚合的卡密库；导入 / 作废 / 解锁僵尸码都在这里。"
        actions={
          <Link
            href="/purchases"
            className="text-xs text-muted-foreground hover:underline"
          >
            导入前请先建采购单 →
          </Link>
        }
      />
      <InventoryTabs />
      {children}
    </div>
  );
}

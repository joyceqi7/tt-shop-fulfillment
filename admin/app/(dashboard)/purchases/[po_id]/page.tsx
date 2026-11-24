import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/layout/page-header";
import {
  PAYMENT_STATUS_LABEL,
  PAYMENT_STATUS_VARIANT,
  variantFor,
} from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { PurchaseRow } from "@/lib/types";
import { formatCents, formatDateTime, formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function PurchaseDetailPage({
  params,
}: {
  params: { po_id: string };
}) {
  const p = await serverApi<PurchaseRow>(`/admin/purchases/${params.po_id}`);
  const remaining = p.total_cost - p.amount_paid;

  return (
    <div className="space-y-6">
      <PageHeader
        title={`采购单 ${p.po_number}`}
        description={
          <>
            <Badge variant={variantFor(PAYMENT_STATUS_VARIANT, p.payment_status)}>
              {PAYMENT_STATUS_LABEL[p.payment_status] ?? p.payment_status}
            </Badge>
            {" · "}
            {p.supplier_name ?? `supplier #${p.supplier_id}`}
          </>
        }
        actions={
          <Link
            href="/purchases"
            className="text-xs text-muted-foreground hover:underline"
          >
            ← 返回列表
          </Link>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="数量" value={formatNumber(p.quantity)} />
        <Stat label="单价" value={formatCents(p.unit_cost)} />
        <Stat label="总成本" value={formatCents(p.total_cost)} />
        <Stat
          label="剩余应付"
          value={formatCents(remaining)}
          accent={remaining > 0 ? "warning" : "success"}
        />
        <Stat label="采购日期" value={p.purchase_date} />
        <Stat label="到期日" value={p.payment_due_date ?? "—"} />
        <Stat label="SKU / 面额" value={`${p.sku_id} · ${formatCents(p.face_value)}`} />
        <Stat
          label="批次"
          value={
            p.batch_id ? (
              <Link
                href={`/inventory/codes?batch_id=${p.batch_id}`}
                className="font-mono text-xs hover:underline"
              >
                {p.batch_id.slice(0, 8)}…
              </Link>
            ) : (
              <span className="text-xs text-muted-foreground">未导入</span>
            )
          }
        />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>备注</CardTitle>
          <CardDescription>创建于 {formatDateTime(p.created_at)}</CardDescription>
        </CardHeader>
        <CardContent>
          <p className="whitespace-pre-wrap text-sm text-muted-foreground">
            {p.note?.trim() ? p.note : "—"}
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  accent?: "warning" | "success";
}) {
  const c =
    accent === "warning"
      ? "text-warning"
      : accent === "success"
        ? "text-success"
        : "";
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <div className={`mt-1 text-sm font-medium tabular-nums ${c}`}>{value}</div>
    </div>
  );
}

import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { PageHeader } from "@/components/layout/page-header";
import { Pagination } from "@/components/ui/pagination";
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  PAYMENT_STATUS_LABEL,
  PAYMENT_STATUS_VARIANT,
  variantFor,
} from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { PurchaseRow, SupplierRow } from "@/lib/types";
import { formatCents, formatDateTime, formatNumber } from "@/lib/utils";

import { CreatePurchaseDialog } from "./create-dialog";
import { PurchaseRowActions } from "./row-actions";

export const dynamic = "force-dynamic";

interface SearchParams {
  payment_status?: string;
  supplier_id?: string;
  sku_id?: string;
  offset?: string;
  limit?: string;
}

interface ListResponse {
  items: PurchaseRow[];
  total: number;
  limit: number;
  offset: number;
}

export default async function PurchasesPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const limit = Math.min(200, Math.max(1, Number(searchParams.limit) || 50));
  const offset = Math.max(0, Number(searchParams.offset) || 0);

  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (searchParams.payment_status)
    qs.set("payment_status", searchParams.payment_status);
  if (searchParams.supplier_id) qs.set("supplier_id", searchParams.supplier_id);
  if (searchParams.sku_id) qs.set("sku_id", searchParams.sku_id);

  const [data, suppliersResp] = await Promise.all([
    serverApi<ListResponse>(`/admin/purchases?${qs.toString()}`),
    serverApi<{ items: SupplierRow[] }>("/admin/suppliers?enabled=true"),
  ]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="采购单"
        description={`共 ${formatNumber(data.total)} 张 PO；导入卡密前必须先在这里建一张并填进价。`}
        actions={<CreatePurchaseDialog suppliers={suppliersResp.items} />}
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>PO 号</TableHead>
            <TableHead>供货商</TableHead>
            <TableHead>SKU / 面额</TableHead>
            <TableHead className="text-right">数量 × 单价</TableHead>
            <TableHead className="text-right">总成本</TableHead>
            <TableHead className="text-right">已付</TableHead>
            <TableHead>付款状态</TableHead>
            <TableHead>到期</TableHead>
            <TableHead>批次</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>没有匹配的采购单</TableEmpty>
          ) : (
            data.items.map((p) => (
              <TableRow key={p.id}>
                <TableCell>
                  <Link
                    href={`/purchases/${p.id}`}
                    className="font-mono text-xs hover:underline"
                  >
                    {p.po_number}
                  </Link>
                </TableCell>
                <TableCell className="text-xs">
                  {p.supplier_name ?? `#${p.supplier_id}`}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {p.sku_id} · {formatCents(p.face_value)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-xs">
                  {formatNumber(p.quantity)} × {formatCents(p.unit_cost)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCents(p.total_cost)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCents(p.amount_paid)}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={variantFor(PAYMENT_STATUS_VARIANT, p.payment_status)}
                  >
                    {PAYMENT_STATUS_LABEL[p.payment_status] ?? p.payment_status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {p.payment_due_date ?? "—"}
                </TableCell>
                <TableCell>
                  {p.batch_id ? (
                    <Link
                      href={`/inventory/codes?batch_id=${p.batch_id}`}
                      className="font-mono text-[10px] hover:underline"
                    >
                      {p.batch_id.slice(0, 8)}…
                    </Link>
                  ) : (
                    <span className="text-[10px] text-muted-foreground">
                      未导入
                    </span>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <PurchaseRowActions purchase={p} />
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>

      <Pagination total={data.total} limit={data.limit} offset={data.offset} />
    </div>
  );
}

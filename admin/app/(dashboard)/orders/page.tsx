import Link from "next/link";

import { Badge } from "@/components/ui/badge";
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
import { PageHeader } from "@/components/layout/page-header";
import { ORDER_STATUS_VARIANT, variantFor } from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { OrderListResponse } from "@/lib/types";
import { formatCents, formatDateTime, formatNumber } from "@/lib/utils";

import { OrdersFilters } from "./filters";
import { SyncOrdersButton } from "./sync-orders-button";

export const dynamic = "force-dynamic";

interface SearchParams {
  status?: string;
  q?: string;
  buyer_email?: string;
  buyer_tt_id?: string;
  product_name?: string;
  tt_settlement_status?: string;
  only_failed?: string;
  offset?: string;
  limit?: string;
}

export default async function OrdersPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const limit = Math.min(200, Math.max(1, Number(searchParams.limit) || 50));
  const offset = Math.max(0, Number(searchParams.offset) || 0);

  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (searchParams.status) qs.set("status", searchParams.status);
  if (searchParams.q) qs.set("q", searchParams.q);
  if (searchParams.buyer_email) qs.set("buyer_email", searchParams.buyer_email);
  if (searchParams.buyer_tt_id) qs.set("buyer_tt_id", searchParams.buyer_tt_id);
  if (searchParams.product_name) qs.set("product_name", searchParams.product_name);
  if (searchParams.tt_settlement_status) {
    qs.set("tt_settlement_status", searchParams.tt_settlement_status);
  }
  if (searchParams.only_failed === "1") qs.set("only_failed", "true");

  const data = await serverApi<OrderListResponse>(
    `/admin/orders?${qs.toString()}`,
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="订单"
        description={`共 ${formatNumber(data.total)} 条订单（按 update_time_tt 倒序）`}
        actions={<SyncOrdersButton />}
      />

      <OrdersFilters initial={searchParams} />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>订单号</TableHead>
            <TableHead>顾客</TableHead>
            <TableHead>产品</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>TT结款</TableHead>
            <TableHead className="text-right">行</TableHead>
            <TableHead className="text-right">金额</TableHead>
            <TableHead>更新</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>没有匹配的订单</TableEmpty>
          ) : (
            data.items.map((o) => (
              <TableRow key={o.order_id}>
                <TableCell>
                  <Link
                    href={`/orders/${o.order_id}`}
                    className="font-mono text-xs hover:underline"
                  >
                    {o.order_id}
                  </Link>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col">
                    <span className="text-xs">{o.buyer_email ?? "—"}</span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      {o.buyer_tt_id ?? "—"}
                    </span>
                  </div>
                </TableCell>
                <TableCell className="text-xs">{o.product_name ?? "—"}</TableCell>
                <TableCell>
                  <Badge variant={variantFor(ORDER_STATUS_VARIANT, o.status)}>
                    {o.status}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {o.tt_settlement_status ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  <span className="text-muted-foreground">{o.line_count}</span>
                  {o.failed_lines > 0 ? (
                    <span className="ml-1 text-destructive">
                      ({o.failed_lines}失败)
                    </span>
                  ) : null}
                  {o.pending_lines > 0 ? (
                    <span className="ml-1 text-warning">
                      ({o.pending_lines}待发)
                    </span>
                  ) : null}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatCents(o.total_amount, o.currency ?? "USD")}
                </TableCell>
                <TableCell className="tabular-nums text-xs text-muted-foreground">
                  {o.update_time_tt
                    ? formatDateTime(new Date(o.update_time_tt * 1000).toISOString())
                    : "—"}
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

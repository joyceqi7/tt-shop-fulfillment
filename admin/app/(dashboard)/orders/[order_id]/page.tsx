import Link from "next/link";
import { Boxes, PackageSearch, RotateCw, Tags } from "lucide-react";

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
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import {
  LINE_FAILED_REASON_LABEL,
  LINE_STATUS_VARIANT,
  ORDER_STATUS_VARIANT,
  variantFor,
} from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { OrderDetailResponse } from "@/lib/types";
import { formatCents, formatDateTime } from "@/lib/utils";

import { ReshipDialog } from "./reship-dialog";
import { ManualCodeDialog } from "./manual-code-dialog";

export const dynamic = "force-dynamic";

function skuSearch(ln: { seller_sku: string | null; tts_sku_id: string | null; tts_product_id: string | null }) {
  return ln.seller_sku ?? ln.tts_sku_id ?? ln.tts_product_id ?? "";
}

function buildMatchHref(ln: {
  seller_sku: string | null;
  tts_product_id: string | null;
  tts_sku_id: string | null;
  unit_price: number | null;
}) {
  const params = new URLSearchParams();
  params.set("from_tts", "1");
  if (ln.seller_sku) params.set("seller_sku", ln.seller_sku);
  if (ln.tts_product_id) params.set("tts_product_id", ln.tts_product_id);
  if (ln.tts_sku_id) params.set("tts_sku_id", ln.tts_sku_id);
  if (ln.unit_price !== null) params.set("list_price_cents", String(ln.unit_price));
  return `/skus?${params.toString()}`;
}

function actionForFailedLine(
  orderId: string,
  ln: OrderDetailResponse["lines"][number],
) {
  const reason = ln.failed_reason ?? "";
  const search = skuSearch(ln);
  if (reason === "sku_not_mapped") {
    return {
      label: "去匹配 SKU",
      href: buildMatchHref(ln),
      icon: Tags,
      hint: "先建立 TT 商品和内部库存 SKU 的关系，再重发订单。",
    };
  }
  if (reason === "no_stock") {
    return {
      label: "去补库存",
      href: search ? `/inventory/summary?sku_id=${encodeURIComponent(search)}` : "/inventory/summary",
      icon: Boxes,
      hint: "急单可直接手动补码；常规处理是补库存后再重发订单。",
    };
  }
  if (reason.includes("supplier") || reason.includes("wgcards")) {
    return {
      label: "看供应商采购",
      href: `/supplier-orders?tt_order_id=${encodeURIComponent(orderId)}`,
      icon: PackageSearch,
      hint: "查看 API 采购失败原因，处理供货商问题后再重发订单。",
    };
  }
  return {
    label: "查看 SKU",
    href: search ? `/skus?q=${encodeURIComponent(search)}` : "/skus",
    icon: Tags,
    hint: "确认映射、库存和供货商链接后再重发订单。",
  };
}

export default async function OrderDetailPage({
  params,
}: {
  params: { order_id: string };
}) {
  const data = await serverApi<OrderDetailResponse>(
    `/admin/orders/${encodeURIComponent(params.order_id)}`,
  );
  const failedLines = data.lines.filter((ln) => ln.status === "failed");

  return (
    <div className="space-y-6">
      <PageHeader
        title={`订单 ${data.order_id}`}
        description={
          <>
            <span className="font-mono">{data.shop_id}</span>
            {data.shop_name ? <> · {data.shop_name}</> : null}
          </>
        }
        actions={
          <>
            <Link
              href="/orders"
              className="text-xs text-muted-foreground hover:underline"
            >
              ← 返回列表
            </Link>
            <ReshipDialog orderId={data.order_id} />
          </>
        }
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="订单状态" value={
          <Badge variant={variantFor(ORDER_STATUS_VARIANT, data.status)}>
            {data.status}
          </Badge>
        } />
        <Stat label="金额" value={formatCents(data.total_amount, data.currency ?? "USD")} />
        <Stat
          label="创建时间"
          value={
            data.create_time_tt
              ? formatDateTime(new Date(data.create_time_tt * 1000).toISOString())
              : "—"
          }
        />
        <Stat
          label="更新时间"
          value={
            data.update_time_tt
              ? formatDateTime(new Date(data.update_time_tt * 1000).toISOString())
              : "—"
          }
        />
      </div>

      {failedLines.length > 0 ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>处理建议</CardTitle>
            <CardDescription>先处理失败原因，再点击右上角重发</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {failedLines.map((ln) => {
              const action = actionForFailedLine(data.order_id, ln);
              const Icon = action.icon;
              return (
                <div
                  key={ln.order_line_id}
                  className="flex flex-col gap-2 rounded-md border border-border p-3 md:flex-row md:items-center md:justify-between"
                >
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="font-mono text-xs">
                        {ln.seller_sku ?? ln.tts_sku_id ?? ln.order_line_id}
                      </span>
                      <Badge variant="destructive">
                        {ln.failed_reason
                          ? (LINE_FAILED_REASON_LABEL[ln.failed_reason] ??
                            ln.failed_reason)
                          : "履约失败"}
                      </Badge>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      {action.hint}
                    </p>
                  </div>
                  <Button size="sm" variant="outline" asChild>
                    <Link href={action.href}>
                      <Icon />
                      {action.label}
                    </Link>
                  </Button>
                  {ln.failed_reason === "no_stock" ? (
                    <ManualCodeDialog
                      orderId={data.order_id}
                      orderLineId={ln.order_line_id}
                    />
                  ) : null}
                </div>
              );
            })}
          </CardContent>
        </Card>
      ) : null}

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>子单（{data.lines.length}）</CardTitle>
          <CardDescription>每行对应一份待发卡密</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>order_line_id</TableHead>
                <TableHead>seller_sku</TableHead>
                <TableHead className="text-right">数量</TableHead>
                <TableHead className="text-right">单价</TableHead>
                <TableHead>状态</TableHead>
                <TableHead>已用码</TableHead>
                <TableHead>失败原因</TableHead>
                <TableHead className="text-right">处理</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.lines.length === 0 ? (
                <TableEmpty>该订单没有子单</TableEmpty>
              ) : (
                data.lines.map((ln) => {
                  const action =
                    ln.status === "failed"
                      ? actionForFailedLine(data.order_id, ln)
                      : null;
                  const Icon = action?.icon ?? RotateCw;
                  return (
                    <TableRow key={ln.order_line_id}>
                      <TableCell className="font-mono text-[11px]">
                        {ln.order_line_id}
                      </TableCell>
                      <TableCell className="font-mono text-[11px]">
                        {ln.seller_sku ?? "—"}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {ln.quantity}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCents(ln.unit_price, ln.currency ?? "USD")}
                      </TableCell>
                      <TableCell>
                        <Badge variant={variantFor(LINE_STATUS_VARIANT, ln.status)}>
                          {ln.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-[11px] tabular-nums">
                        {ln.redeemed_code_id ?? "—"}
                      </TableCell>
                      <TableCell className="text-xs text-destructive">
                        {ln.failed_reason
                          ? (LINE_FAILED_REASON_LABEL[ln.failed_reason] ??
                            ln.failed_reason)
                          : "—"}
                      </TableCell>
                      <TableCell className="text-right">
                        {action ? (
                          <div className="flex flex-wrap justify-end gap-2">
                            <Button size="sm" variant="ghost" asChild>
                              <Link href={action.href}>
                                <Icon />
                                {action.label}
                              </Link>
                            </Button>
                            {ln.failed_reason === "no_stock" ? (
                              <ManualCodeDialog
                                orderId={data.order_id}
                                orderLineId={ln.order_line_id}
                                label="补码"
                                variant="ghost"
                              />
                            ) : null}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">—</span>
                        )}
                      </TableCell>
                    </TableRow>
                  );
                })
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>履约调用日志</CardTitle>
          <CardDescription>近 50 次 fulfillment_logs，越上越新</CardDescription>
        </CardHeader>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>时间</TableHead>
                <TableHead>endpoint</TableHead>
                <TableHead>HTTP</TableHead>
                <TableHead>code</TableHead>
                <TableHead>message</TableHead>
                <TableHead>line</TableHead>
                <TableHead className="text-right">耗时</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.fulfillment_logs.length === 0 ? (
                <TableEmpty>暂无 API 调用记录</TableEmpty>
              ) : (
                data.fulfillment_logs.map((l) => (
                  <TableRow key={l.id}>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {formatDateTime(l.created_at)}
                    </TableCell>
                    <TableCell className="font-mono text-[11px]">
                      {l.api_method} {l.api_endpoint}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {l.http_status ?? "—"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {l.api_code ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {l.api_message ?? l.error ?? "—"}
                    </TableCell>
                    <TableCell className="font-mono text-[10px]">
                      {l.order_line_id ?? "—"}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">
                      {l.latency_ms !== null ? `${l.latency_ms}ms` : "—"}
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <div className="mt-1 text-sm">{value}</div>
    </div>
  );
}

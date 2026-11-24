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
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ORDER_STATUS_VARIANT, variantFor } from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { DashboardResponse } from "@/lib/types";
import { formatCents, formatNumber } from "@/lib/utils";

import { TrendChart } from "./trend-chart";

export const dynamic = "force-dynamic";

export default async function DashboardPage() {
  const data = await serverApi<DashboardResponse>("/admin/dashboard/overview");
  const { kpis, trend_14d, low_stock, recent_failed_orders, supplier_sales_30d } =
    data;

  return (
    <div className="space-y-6">
      <PageHeader
        title="数据看板"
        description="今日运营节奏 + 近 14 日 GMV 趋势 + 低库存与失败订单告警。"
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <KpiCard
          label="今日 GMV"
          value={formatCents(kpis.gmv_today)}
          hint={`${formatNumber(kpis.orders_today)} 单`}
        />
        <KpiCard
          label="待发货行"
          value={formatNumber(kpis.pending_lines)}
          hint="pending lines"
          accent={kpis.pending_lines > 0 ? "warning" : undefined}
        />
        <KpiCard
          label="今日失败"
          value={formatNumber(kpis.failed_today)}
          hint="failed lines"
          accent={kpis.failed_today > 0 ? "destructive" : undefined}
        />
        <KpiCard
          label="今日发出"
          value={formatNumber(kpis.used_today)}
          hint="used codes"
          accent="success"
        />
        <KpiCard
          label="可用库存"
          value={formatNumber(kpis.free_codes_total)}
          hint="free codes"
        />
        <KpiCard
          label="低库存项"
          value={formatNumber(low_stock.length)}
          hint={low_stock.length ? "≤ 阈值" : "全部充足"}
          accent={low_stock.length > 0 ? "warning" : "success"}
        />
        <KpiCard
          label="用户待支付"
          value={formatNumber(kpis.pending_user_payments)}
          hint="订单待付款"
          accent={kpis.pending_user_payments > 0 ? "warning" : undefined}
        />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>近 14 日 GMV 趋势</CardTitle>
          <CardDescription>按天聚合，剔除 CANCELLED / RETURN</CardDescription>
        </CardHeader>
        <CardContent>
          <TrendChart points={trend_14d} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>供货商销售（近30天）</CardTitle>
            <CardDescription>按已发码聚合的销量、销售额与估算毛利</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>供货商</TableHead>
                  <TableHead className="text-right">销量</TableHead>
                  <TableHead className="text-right">销售额</TableHead>
                  <TableHead className="text-right">毛利(估)</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {supplier_sales_30d.length === 0 ? (
                  <TableEmpty>近 30 天暂无供货商销量数据</TableEmpty>
                ) : (
                  supplier_sales_30d.map((row) => (
                    <TableRow key={row.supplier_id}>
                      <TableCell>{row.supplier_name}</TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatNumber(row.sold_codes)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCents(row.gmv_estimated)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCents(row.gross_profit_estimated)}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle>低库存预警</CardTitle>
            <CardDescription>
              free 码低于 stock_alert_threshold 的 SKU
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>SKU</TableHead>
                  <TableHead>面额</TableHead>
                  <TableHead className="text-right">可用 / 阈值</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {low_stock.length === 0 ? (
                  <TableEmpty>所有 SKU 库存充足</TableEmpty>
                ) : (
                  low_stock.map((row) => (
                    <TableRow key={`${row.sku_id}:${row.face_value}`}>
                      <TableCell className="font-mono">{row.sku_id}</TableCell>
                      <TableCell className="tabular-nums">
                        {formatCents(row.face_value)}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        <span
                          className={
                            row.free === 0
                              ? "text-destructive font-medium"
                              : "text-warning font-medium"
                          }
                        >
                          {row.free}
                        </span>
                        <span className="text-muted-foreground"> / {row.threshold}</span>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle>最近失败订单</CardTitle>
            <CardDescription>有 failed line 的订单（最多 5 条）</CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>订单</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">失败行</TableHead>
                  <TableHead className="text-right">金额</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {recent_failed_orders.length === 0 ? (
                  <TableEmpty>近期没有失败订单</TableEmpty>
                ) : (
                  recent_failed_orders.map((o) => (
                    <TableRow key={o.order_id}>
                      <TableCell>
                        <Link
                          className="font-mono text-xs hover:underline"
                          href={`/orders/${o.order_id}`}
                        >
                          {o.order_id}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <Badge variant={variantFor(ORDER_STATUS_VARIANT, o.status)}>
                          {o.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right tabular-nums text-destructive">
                        {o.failed_lines}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {formatCents(o.total_amount, o.currency ?? "USD")}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function KpiCard({
  label,
  value,
  hint,
  accent,
}: {
  label: string;
  value: string;
  hint?: string;
  accent?: "success" | "warning" | "destructive";
}) {
  const accentColor =
    accent === "success"
      ? "text-success"
      : accent === "warning"
        ? "text-warning"
        : accent === "destructive"
          ? "text-destructive"
          : "text-foreground";
  return (
    <Card>
      <CardContent className="space-y-1.5 p-4">
        <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
          {label}
        </p>
        <p className={`text-2xl font-semibold tabular-nums ${accentColor}`}>
          {value}
        </p>
        {hint ? (
          <p className="text-[11px] text-muted-foreground">{hint}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}

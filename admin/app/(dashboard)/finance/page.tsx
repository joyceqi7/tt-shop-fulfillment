import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { PageHeader } from "@/components/layout/page-header";
import { serverApi } from "@/lib/server-api";
import type { FinanceOverview, MonthlyPoint } from "@/lib/types";
import { formatCents, formatNumber } from "@/lib/utils";

import { MonthlyChart } from "./monthly-chart";

export const dynamic = "force-dynamic";

export default async function FinancePage() {
  const [overview, monthly] = await Promise.all([
    serverApi<FinanceOverview>("/admin/finance/overview?range_days=30"),
    serverApi<{ months: MonthlyPoint[] }>("/admin/finance/monthly?months_back=6"),
  ]);

  const ccy = overview.gmv_currency || "USD";

  return (
    <div className="space-y-6">
      <PageHeader
        title="财务"
        description={`近 ${overview.range_days} 天的口径（GMV / 平台抽成估算 ${(overview.commission_rate * 100).toFixed(1)}% / 已售卡密成本）`}
      />

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
        <Stat
          label="GMV"
          value={formatCents(overview.gmv, ccy)}
          hint="不含 CANCELLED / RETURN"
        />
        <Stat
          label="平台抽成估算"
          value={formatCents(overview.platform_commission_estimated, ccy)}
          hint={`${(overview.commission_rate * 100).toFixed(1)}% × GMV`}
          accent="warning"
        />
        <Stat
          label="已售卡密成本"
          value={formatCents(overview.sold_code_cost, ccy)}
          hint="codes.status=used，需关联 PO"
        />
        <Stat
          label="毛利"
          value={formatCents(overview.gross_profit, ccy)}
          hint="GMV - 抽成 - 已售成本"
          accent={overview.gross_profit >= 0 ? "success" : "destructive"}
        />
        <Stat
          label="在库成本"
          value={formatCents(overview.inventory_cost, ccy)}
          hint="free + locked"
        />
        <Stat
          label="应付总额"
          value={formatCents(overview.payable, ccy)}
          accent="warning"
          hint="未结清的 PO 余额"
        />
        <Stat
          label="逾期应付"
          value={formatCents(overview.payable_overdue, ccy)}
          accent={overview.payable_overdue > 0 ? "destructive" : undefined}
          hint="到期日 < 今天"
        />
        <Stat
          label="无成本归属码"
          value={formatNumber(overview.legacy_codes_no_cost)}
          accent={overview.legacy_codes_no_cost > 0 ? "warning" : undefined}
          hint="cutover 前导入的历史码"
        />
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle>月度走势</CardTitle>
          <CardDescription>近 6 个月的 GMV 与采购成本对比</CardDescription>
        </CardHeader>
        <CardContent>
          <MonthlyChart points={monthly.months} />
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({
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
  const c =
    accent === "success"
      ? "text-success"
      : accent === "warning"
        ? "text-warning"
        : accent === "destructive"
          ? "text-destructive"
          : "text-foreground";
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${c}`}>{value}</p>
      {hint ? (
        <p className="mt-0.5 text-[10px] text-muted-foreground">{hint}</p>
      ) : null}
    </div>
  );
}

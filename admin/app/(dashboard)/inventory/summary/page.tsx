import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableEmpty,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { serverApi } from "@/lib/server-api";
import type { InventorySkuRow } from "@/lib/types";
import { formatCents, formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function SummaryPage() {
  const data = await serverApi<{ rows: InventorySkuRow[] }>(
    "/admin/inventory/summary",
  );

  const totals = data.rows.reduce(
    (acc, r) => {
      acc.total += r.total;
      acc.free += r.free;
      acc.locked += r.locked;
      acc.used += r.used;
      return acc;
    },
    { total: 0, free: 0, locked: 0, used: 0 },
  );

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="总码量" value={formatNumber(totals.total)} />
        <Stat label="可用 (free)" value={formatNumber(totals.free)} accent="success" />
        <Stat label="占用 (locked)" value={formatNumber(totals.locked)} accent="warning" />
        <Stat label="已用 (used)" value={formatNumber(totals.used)} />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>SKU</TableHead>
            <TableHead>商品名</TableHead>
            <TableHead>面额</TableHead>
            <TableHead>分类</TableHead>
            <TableHead className="text-right">阈值</TableHead>
            <TableHead className="text-right">总数</TableHead>
            <TableHead className="text-right text-success">free</TableHead>
            <TableHead className="text-right text-warning">locked</TableHead>
            <TableHead className="text-right">used</TableHead>
            <TableHead className="text-right text-destructive">void</TableHead>
            <TableHead className="text-right">expired</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.rows.length === 0 ? (
            <TableEmpty>还没有任何码导入</TableEmpty>
          ) : (
            data.rows.map((r) => (
              <TableRow key={`${r.sku_id}:${r.face_value}`}>
                <TableCell className="font-mono">{r.sku_id}</TableCell>
                <TableCell>{r.product_name ?? "—"}</TableCell>
                <TableCell className="tabular-nums">
                  {formatCents(r.face_value)}
                </TableCell>
                <TableCell>
                  <Badge
                    variant={
                      r.inventory_tag === "zero"
                        ? "destructive"
                        : r.inventory_tag === "low"
                          ? "warning"
                          : r.inventory_tag === "stagnant"
                            ? "muted"
                            : "success"
                    }
                  >
                    {r.inventory_tag === "zero"
                      ? "零库存"
                      : r.inventory_tag === "low"
                        ? "低库存"
                        : r.inventory_tag === "stagnant"
                          ? "滞销"
                          : "正常"}
                  </Badge>
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {formatNumber(r.threshold)}
                </TableCell>
                <TableCell className="text-right tabular-nums font-medium">
                  {formatNumber(r.total)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-success">
                  {formatNumber(r.free)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-warning">
                  {formatNumber(r.locked)}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(r.used)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-destructive">
                  {formatNumber(r.void)}
                </TableCell>
                <TableCell className="text-right tabular-nums text-muted-foreground">
                  {formatNumber(r.expired)}
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

function Stat({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: "success" | "warning";
}) {
  const c =
    accent === "success"
      ? "text-success"
      : accent === "warning"
        ? "text-warning"
        : "text-foreground";
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </p>
      <p className={`mt-1 text-xl font-semibold tabular-nums ${c}`}>{value}</p>
    </div>
  );
}

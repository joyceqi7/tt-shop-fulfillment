import Link from "next/link";

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
import type { BatchRow } from "@/lib/types";
import { formatCents, formatDateTime, formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

export default async function BatchesPage({
  searchParams,
}: {
  searchParams: { sku_id?: string };
}) {
  const qs = new URLSearchParams();
  if (searchParams.sku_id) qs.set("sku_id", searchParams.sku_id);
  const data = await serverApi<{ items: BatchRow[] }>(
    `/admin/inventory/batches${qs.size ? `?${qs.toString()}` : ""}`,
  );

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>批次 batch_id</TableHead>
          <TableHead>SKU</TableHead>
          <TableHead>面额</TableHead>
          <TableHead className="text-right">总 / free / locked / used</TableHead>
          <TableHead>采购单</TableHead>
          <TableHead>导入时间</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {data.items.length === 0 ? (
          <TableEmpty>暂无批次</TableEmpty>
        ) : (
          data.items.map((b) => (
            <TableRow key={b.batch_id}>
              <TableCell>
                <Link
                  href={`/inventory/codes?batch_id=${b.batch_id}`}
                  className="font-mono text-[11px] hover:underline"
                >
                  {b.batch_id.slice(0, 8)}…
                </Link>
              </TableCell>
              <TableCell className="font-mono">{b.sku_id}</TableCell>
              <TableCell className="tabular-nums">
                {formatCents(b.face_value)}
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <span>{formatNumber(b.total)}</span>
                <span className="text-success"> / {b.free}</span>
                <span className="text-warning"> / {b.locked}</span>
                <span className="text-muted-foreground"> / {b.used}</span>
              </TableCell>
              <TableCell>
                {b.purchase_order_id ? (
                  <Link
                    href={`/purchases/${b.purchase_order_id}`}
                    className="font-mono text-xs hover:underline"
                  >
                    {b.po_number ?? `#${b.purchase_order_id}`}
                  </Link>
                ) : (
                  <span className="text-xs text-muted-foreground">— 历史码 —</span>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground tabular-nums">
                {formatDateTime(b.earliest_imported)}
              </TableCell>
            </TableRow>
          ))
        )}
      </TableBody>
    </Table>
  );
}

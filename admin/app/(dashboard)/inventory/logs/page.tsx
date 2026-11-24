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
import { serverApi } from "@/lib/server-api";
import type { InventoryLogListResponse } from "@/lib/types";
import { formatDateTime, formatNumber } from "@/lib/utils";

export const dynamic = "force-dynamic";

interface SearchParams {
  change_type?: string;
  offset?: string;
  limit?: string;
}

export default async function InventoryLogsPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const limit = Math.min(200, Math.max(1, Number(searchParams.limit) || 50));
  const offset = Math.max(0, Number(searchParams.offset) || 0);
  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (searchParams.change_type) qs.set("change_type", searchParams.change_type);

  const data = await serverApi<InventoryLogListResponse>(
    `/admin/inventory/logs?${qs.toString()}`,
  );

  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>ID</TableHead>
            <TableHead>类型</TableHead>
            <TableHead className="text-right">数量</TableHead>
            <TableHead>操作人</TableHead>
            <TableHead>对象</TableHead>
            <TableHead>关联订单</TableHead>
            <TableHead>备注</TableHead>
            <TableHead>时间</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>暂无库存变更流水</TableEmpty>
          ) : (
            data.items.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="font-mono text-xs">{r.id}</TableCell>
                <TableCell>{r.change_type}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {formatNumber(r.quantity)}
                </TableCell>
                <TableCell>{r.operator}</TableCell>
                <TableCell className="font-mono text-xs">
                  {r.target_type ?? "—"}:{r.target_id ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {r.order_id ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {r.note ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">
                  {formatDateTime(r.created_at)}
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


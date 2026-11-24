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
import { CODE_STATUS_VARIANT, variantFor } from "@/lib/status";
import { serverApi } from "@/lib/server-api";
import type { CodeRow } from "@/lib/types";
import { formatCents, formatDateTime } from "@/lib/utils";

import { CodesFilters } from "./filters";
import { CodeRowActions } from "./row-actions";

export const dynamic = "force-dynamic";

interface SearchParams {
  status?: string;
  sku_id?: string;
  batch_id?: string;
  offset?: string;
  limit?: string;
}

interface CodesResponse {
  items: CodeRow[];
  total: number;
  limit: number;
  offset: number;
}

export default async function CodesPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const limit = Math.min(500, Math.max(1, Number(searchParams.limit) || 100));
  const offset = Math.max(0, Number(searchParams.offset) || 0);

  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (searchParams.status) qs.set("status", searchParams.status);
  if (searchParams.sku_id) qs.set("sku_id", searchParams.sku_id);
  if (searchParams.batch_id) qs.set("batch_id", searchParams.batch_id);

  const data = await serverApi<CodesResponse>(
    `/admin/inventory/codes?${qs.toString()}`,
  );

  return (
    <div className="space-y-4">
      <CodesFilters initial={searchParams} />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>id</TableHead>
            <TableHead>SKU</TableHead>
            <TableHead>面额</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>批次</TableHead>
            <TableHead>order_line_id</TableHead>
            <TableHead>导入</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>没有匹配的码</TableEmpty>
          ) : (
            data.items.map((c) => (
              <TableRow key={c.id}>
                <TableCell className="font-mono tabular-nums text-xs">
                  {c.id}
                </TableCell>
                <TableCell className="font-mono text-xs">{c.sku_id}</TableCell>
                <TableCell className="tabular-nums">
                  {formatCents(c.face_value)}
                </TableCell>
                <TableCell>
                  <Badge variant={variantFor(CODE_STATUS_VARIANT, c.status)}>
                    {c.status}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-[10px]">
                  {c.batch_id.slice(0, 8)}…
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {c.order_line_id ?? "—"}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(c.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <CodeRowActions code={c} />
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

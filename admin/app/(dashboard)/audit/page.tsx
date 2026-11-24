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
import { serverApi } from "@/lib/server-api";
import type { AuditRow } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

export const dynamic = "force-dynamic";

interface ListResponse {
  items: AuditRow[];
  total: number;
  limit: number;
  offset: number;
}

interface SearchParams {
  operator?: string;
  action?: string;
  target_type?: string;
  offset?: string;
  limit?: string;
}

const ACTION_VARIANT: Record<string, "success" | "warning" | "destructive" | "muted"> = {
  login: "muted",
  reship: "warning",
  void: "destructive",
  unlock: "warning",
  import: "success",
  sku_create: "success",
  sku_update: "warning",
  sku_delete: "destructive",
  supplier_create: "success",
  supplier_update: "warning",
  purchase_create: "success",
  purchase_update: "warning",
  purchase_delete: "destructive",
  purchase_pay: "success",
};

export default async function AuditPage({
  searchParams,
}: {
  searchParams: SearchParams;
}) {
  const limit = Math.min(200, Math.max(1, Number(searchParams.limit) || 50));
  const offset = Math.max(0, Number(searchParams.offset) || 0);

  const qs = new URLSearchParams();
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (searchParams.operator) qs.set("operator", searchParams.operator);
  if (searchParams.action) qs.set("action", searchParams.action);
  if (searchParams.target_type) qs.set("target_type", searchParams.target_type);

  const data = await serverApi<ListResponse>(
    `/admin/audit?${qs.toString()}`,
  );

  return (
    <div className="space-y-4">
      <PageHeader
        title="审计日志"
        description="所有敏感操作都会落库；本页只读，按时间倒序。"
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>时间</TableHead>
            <TableHead>操作员</TableHead>
            <TableHead>动作</TableHead>
            <TableHead>对象</TableHead>
            <TableHead>详情</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>暂无审计记录</TableEmpty>
          ) : (
            data.items.map((r) => (
              <TableRow key={r.id}>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(r.created_at)}
                </TableCell>
                <TableCell className="font-mono text-xs">{r.operator}</TableCell>
                <TableCell>
                  <Badge variant={ACTION_VARIANT[r.action] ?? "muted"}>
                    {r.action}
                  </Badge>
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {r.target_type ? `${r.target_type}:${r.target_id ?? ""}` : "—"}
                </TableCell>
                <TableCell>
                  <details className="text-xs">
                    <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
                      查看
                    </summary>
                    <pre className="mt-2 max-w-xl overflow-auto rounded-sm bg-muted p-2 font-mono text-[10px]">
                      {JSON.stringify(r.details ?? {}, null, 2)}
                    </pre>
                  </details>
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

import { Badge } from "@/components/ui/badge";
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
import { serverApi } from "@/lib/server-api";
import type { SupplierRow } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

import { CreateSupplierDialog } from "./create-dialog";
import { SupplierRowActions } from "./row-actions";

export const dynamic = "force-dynamic";

export default async function SuppliersPage() {
  const data = await serverApi<{ items: SupplierRow[] }>("/admin/suppliers");

  return (
    <div className="space-y-4">
      <PageHeader
        title="供货商"
        description="供货商档案 + 默认账期；账户停用前必须先结清未付的采购单。"
        actions={<CreateSupplierDialog />}
      />

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>名称</TableHead>
            <TableHead>类型</TableHead>
            <TableHead>联系人</TableHead>
            <TableHead>电话</TableHead>
            <TableHead className="text-right">默认账期</TableHead>
            <TableHead className="text-right">采购单</TableHead>
            <TableHead className="text-right">未付</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>创建</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>还没有供货商</TableEmpty>
          ) : (
            data.items.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-medium">{s.name}</TableCell>
                <TableCell>
                  <Badge
                    variant={
                      (s.supplier_type ?? (s.is_wgcards ? "api" : "manual")) ===
                      "api"
                        ? "default"
                        : "muted"
                    }
                  >
                    {(s.supplier_type ?? (s.is_wgcards ? "api" : "manual")) ===
                    "api"
                      ? "API"
                      : "手动"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs">
                  {s.contact_person ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {s.contact_phone ?? "—"}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {s.default_payment_terms_days} 天
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  {s.po_count}
                </TableCell>
                <TableCell className="text-right tabular-nums">
                  <span
                    className={
                      s.unpaid_count > 0 ? "text-warning" : "text-muted-foreground"
                    }
                  >
                    {s.unpaid_count}
                  </span>
                </TableCell>
                <TableCell>
                  <Badge variant={s.enabled ? "success" : "muted"}>
                    {s.enabled ? "active" : "disabled"}
                  </Badge>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(s.created_at)}
                </TableCell>
                <TableCell className="text-right">
                  <SupplierRowActions supplier={s} />
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

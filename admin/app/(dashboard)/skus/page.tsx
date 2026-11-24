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
import type { SkuRow, SupplierRow } from "@/lib/types";
import { formatCents, formatDateTime } from "@/lib/utils";

import { SkuRowActions } from "./row-actions";
import { CreateSkuDialog } from "./create-dialog";
import type { SkuFormState } from "./sku-form";

export const dynamic = "force-dynamic";

function firstParam(value: string | string[] | undefined) {
  return Array.isArray(value) ? value[0] : value;
}

function buildInitialSku(
  searchParams: Record<string, string | string[] | undefined>,
): Partial<SkuFormState> | undefined {
  if (firstParam(searchParams.from_tts) !== "1") return undefined;
  const productName = firstParam(searchParams.product_name) ?? "";
  const skuName = firstParam(searchParams.sku_name) ?? "";
  const displayName =
    productName && skuName && productName !== skuName
      ? `${productName} - ${skuName}`
      : productName || skuName;
  return {
    product_name: displayName,
    seller_sku: firstParam(searchParams.seller_sku) ?? "",
    tts_product_id: firstParam(searchParams.tts_product_id) ?? "",
    tts_sku_id: firstParam(searchParams.tts_sku_id) ?? "",
    list_price_cents: firstParam(searchParams.list_price_cents) ?? "",
  };
}

function buildTtsLookup(
  searchParams: Record<string, string | string[] | undefined>,
) {
  if (firstParam(searchParams.from_tts) !== "1") return null;
  const sellerSku = firstParam(searchParams.seller_sku)?.trim() ?? "";
  const ttsProductId = firstParam(searchParams.tts_product_id)?.trim() ?? "";
  const ttsSkuId = firstParam(searchParams.tts_sku_id)?.trim() ?? "";
  if (!ttsProductId || !ttsSkuId) return null;
  return {
    seller_sku: sellerSku,
    tts_product_id: ttsProductId,
    tts_sku_id: ttsSkuId,
  };
}

function findExactTtsMapping(
  items: SkuRow[],
  lookup: ReturnType<typeof buildTtsLookup>,
) {
  if (!lookup) return null;
  return (
    items.find(
      (item) =>
        item.tts_product_id === lookup.tts_product_id &&
        item.tts_sku_id === lookup.tts_sku_id &&
        (!lookup.seller_sku || item.seller_sku === lookup.seller_sku),
    ) ?? null
  );
}

export default async function SkusPage({
  searchParams,
}: {
  searchParams: Record<string, string | string[] | undefined>;
}) {
  const ttsLookup = buildTtsLookup(searchParams);
  const q = firstParam(searchParams.q)?.trim() || ttsLookup?.tts_product_id;
  const [data, suppliersResp] = await Promise.all([
    serverApi<{ items: SkuRow[]; total: number }>(
      q ? `/admin/skus?q=${encodeURIComponent(q)}` : "/admin/skus",
    ),
    serverApi<{ items: SupplierRow[] }>("/admin/suppliers?enabled=true"),
  ]);

  const apiSuppliers = suppliersResp.items.filter(
    (s) => s.supplier_type === "api" || s.is_wgcards,
  );
  const existingTtsMapping = findExactTtsMapping(data.items, ttsLookup);
  const initialSku = existingTtsMapping
    ? undefined
    : buildInitialSku(searchParams);

  return (
    <div className="space-y-4">
      <PageHeader
        title="SKU 映射"
        description="把 TT 商品（seller_sku / product_id / sku_id）映射到内部 SKU + 面额。"
        actions={<CreateSkuDialog initial={initialSku} />}
      />

      {existingTtsMapping ? (
        <div className="rounded-md border border-warning/40 bg-warning/10 px-4 py-3 text-sm">
          <div className="font-medium text-foreground">
            这个 TT 商品已经有 SKU 映射，下面已筛出对应记录。
          </div>
          <div className="mt-1 text-muted-foreground">
            当前映射：{existingTtsMapping.internal_sku_id} /{" "}
            {formatCents(existingTtsMapping.face_value)}
            {existingTtsMapping.active ? "" : "（已停用，请编辑后启用）"}
          </div>
        </div>
      ) : null}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>internal_sku_id</TableHead>
            <TableHead>商品名</TableHead>
            <TableHead>seller_sku</TableHead>
            <TableHead>tts_product_id</TableHead>
            <TableHead>tts_sku_id</TableHead>
            <TableHead>面额</TableHead>
            <TableHead>售价</TableHead>
            <TableHead>API 链接</TableHead>
            <TableHead>安全库存</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>更新</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>还没有任何 SKU 映射</TableEmpty>
          ) : (
            data.items.map((s) => (
              <TableRow key={s.id}>
                <TableCell className="font-mono">{s.internal_sku_id}</TableCell>
                <TableCell>{s.product_name ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">
                  {s.seller_sku ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {s.tts_product_id ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {s.tts_sku_id ?? "—"}
                </TableCell>
                <TableCell className="tabular-nums">
                  {formatCents(s.face_value)}
                </TableCell>
                <TableCell className="tabular-nums">
                  {s.list_price_cents != null
                    ? formatCents(s.list_price_cents)
                    : "—"}
                </TableCell>
                <TableCell className="text-xs">
                  {s.api_link_count > 0 ? (
                    <span>
                      {s.api_link_count} 条
                      {s.has_auto_fulfill ? " · 自动" : ""}
                    </span>
                  ) : (
                    <span className="text-muted-foreground">—</span>
                  )}
                </TableCell>
                <TableCell className="tabular-nums">
                  {s.safety_stock_threshold ?? "全局"}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <Badge variant={s.active ? "success" : "muted"}>
                      {s.active ? "active" : "disabled"}
                    </Badge>
                    {s.inventory_locked === true ? (
                      <Badge
                        variant="destructive"
                        title={s.inventory_lock_reason ?? undefined}
                      >
                        锁库存
                      </Badge>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(s.updated_at)}
                </TableCell>
                <TableCell className="text-right">
                  <SkuRowActions
                    sku={s}
                    apiSuppliers={apiSuppliers}
                    autoOpen={existingTtsMapping?.id === s.id}
                  />
                </TableCell>
              </TableRow>
            ))
          )}
        </TableBody>
      </Table>
    </div>
  );
}

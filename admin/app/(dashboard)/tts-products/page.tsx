import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
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
import type {
  TTSProductListResponse,
  TTSProductSkuRow,
  TTSProductStatsResponse,
} from "@/lib/types";
import { formatCents, formatDateTime, formatNumber } from "@/lib/utils";

import { SyncTTSProductsButton } from "./sync-button";

export const dynamic = "force-dynamic";

const MATCH_LABEL: Record<string, string> = {
  matched: "已匹配",
  unmatched: "未匹配",
  conflict: "冲突",
};

function matchVariant(status: TTSProductSkuRow["match_status"]) {
  if (status === "matched") return "success" as const;
  if (status === "conflict") return "destructive" as const;
  return "warning" as const;
}

function buildSkuMatchHref(sku: TTSProductSkuRow) {
  const params = new URLSearchParams();
  params.set("from_tts", "1");
  params.set("tts_product_id", sku.tts_product_id);
  params.set("tts_sku_id", sku.tts_sku_id);
  if (sku.seller_sku) params.set("seller_sku", sku.seller_sku);
  if (sku.product_name) params.set("product_name", sku.product_name);
  if (sku.sku_name) params.set("sku_name", sku.sku_name);
  if (sku.price_cents !== null) {
    params.set("list_price_cents", String(sku.price_cents));
  }
  return `/skus?${params.toString()}`;
}

export default async function TTSProductsPage() {
  const [data, stats] = await Promise.all([
    serverApi<TTSProductListResponse>("/admin/tts-products?limit=200"),
    serverApi<TTSProductStatsResponse>("/admin/tts-products/stats"),
  ]);

  return (
    <div className="space-y-4">
      <PageHeader
        title="TT 商品中心"
        description="从 TT Shop 只读同步已上架商品，再匹配到内部库存 SKU。"
        actions={<SyncTTSProductsButton />}
      />

      <div className="grid gap-3 md:grid-cols-4">
        <Stat label="TT SKU" value={formatNumber(stats.total_skus)} />
        <Stat label="已匹配" value={formatNumber(stats.matched_skus)} tone="success" />
        <Stat label="未匹配" value={formatNumber(stats.unmatched_skus)} tone="warning" />
        <Stat
          label="最后同步"
          value={formatDateTime(stats.last_synced_at)}
          small
        />
      </div>

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>匹配</TableHead>
            <TableHead>商品 / SKU</TableHead>
            <TableHead>seller_sku</TableHead>
            <TableHead>TT product_id</TableHead>
            <TableHead>TT sku_id</TableHead>
            <TableHead>售价</TableHead>
            <TableHead>状态</TableHead>
            <TableHead>店铺</TableHead>
            <TableHead>同步时间</TableHead>
            <TableHead className="text-right">操作</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {data.items.length === 0 ? (
            <TableEmpty>还没有同步 TT 商品，点击右上角同步。</TableEmpty>
          ) : (
            data.items.map((sku) => (
              <TableRow key={sku.id}>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <Badge variant={matchVariant(sku.match_status)}>
                      {MATCH_LABEL[sku.match_status ?? "unmatched"] ?? "未匹配"}
                    </Badge>
                    {sku.match_note ? (
                      <span className="max-w-40 truncate text-[11px] text-muted-foreground">
                        {sku.match_note}
                      </span>
                    ) : null}
                  </div>
                </TableCell>
                <TableCell>
                  <div className="max-w-64 space-y-0.5">
                    <div className="truncate text-sm font-medium">
                      {sku.product_name ?? "(未命名商品)"}
                    </div>
                    <div className="truncate text-xs text-muted-foreground">
                      {sku.sku_name ?? "SKU 未命名"}
                    </div>
                  </div>
                </TableCell>
                <TableCell className="font-mono text-xs">
                  {sku.seller_sku ?? "—"}
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {sku.tts_product_id}
                </TableCell>
                <TableCell className="font-mono text-[11px]">
                  {sku.tts_sku_id}
                </TableCell>
                <TableCell className="tabular-nums">
                  {formatCents(sku.price_cents, sku.currency ?? "USD")}
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <Badge variant="muted">{sku.product_status ?? "product —"}</Badge>
                    <Badge variant="outline">{sku.status ?? "sku —"}</Badge>
                  </div>
                </TableCell>
                <TableCell className="max-w-36 truncate text-xs">
                  {sku.shop_name ?? sku.shop_id}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground tabular-nums">
                  {formatDateTime(sku.synced_at)}
                </TableCell>
                <TableCell className="text-right">
                  {sku.match_status === "matched" ? (
                    <Button size="sm" variant="ghost" asChild>
                      <Link
                        href={`/skus?q=${encodeURIComponent(
                          sku.seller_sku ?? sku.tts_sku_id,
                        )}`}
                      >
                        查看映射
                      </Link>
                    </Button>
                  ) : (
                    <Button size="sm" variant="outline" asChild>
                      <Link href={buildSkuMatchHref(sku)}>去匹配</Link>
                    </Button>
                  )}
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
  tone,
  small,
}: {
  label: string;
  value: string;
  tone?: "success" | "warning";
  small?: boolean;
}) {
  const toneClass =
    tone === "success"
      ? "text-success"
      : tone === "warning"
        ? "text-warning"
        : "text-foreground";
  return (
    <div className="rounded-md border border-border bg-card px-4 py-3">
      <div className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
        {label}
      </div>
      <div
        className={`mt-1 font-semibold tabular-nums ${toneClass} ${
          small ? "text-sm" : "text-xl"
        }`}
      >
        {value}
      </div>
    </div>
  );
}

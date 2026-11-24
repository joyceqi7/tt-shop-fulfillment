"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";
import { useParams } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PageHeader } from "@/components/layout/page-header";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import {
  formatFaceRange,
  spuTypeLabel,
  WGCARDS_SPU_TYPE_LABEL,
  type WGCardsCatalogPage,
} from "@/lib/wgcards-catalog";

export default function SupplierCatalogPage() {
  const params = useParams<{ id: string }>();
  const supplierId = Number(params.id);

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [spuType, setSpuType] = useState<string>("");
  const [giftCardOnly, setGiftCardOnly] = useState(false);
  const [page, setPage] = useState(1);
  const [data, setData] = useState<WGCardsCatalogPage | null>(null);

  const load = useCallback(async () => {
    if (!supplierId) return;
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        current: String(page),
        size: "30",
      });
      const q = search.trim();
      if (q) qs.set("q", q);
      if (spuType) qs.set("spu_type", spuType);
      if (giftCardOnly) qs.set("gift_card_only", "true");

      const resp = await api<WGCardsCatalogPage>(
        `/suppliers/${supplierId}/wgcards/catalog?${qs.toString()}`,
      );
      setData(resp);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "加载失败",
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [supplierId, page, search, spuType, giftCardOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="space-y-4">
      <PageHeader
        title={`商品目录 #${supplierId}`}
        description="浏览 API 供应商全量商品字段，支持 SKU ID 与商品名检索。"
        actions={
          <Button size="sm" variant="outline" asChild>
            <Link href="/suppliers">
              <ArrowLeft className="size-4" />
              返回供货商
            </Link>
          </Button>
        }
      />

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-[240px] flex-1 space-y-1">
          <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            搜索（SKU ID / 商品名）
          </label>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="输入 SKU ID 或商品名..."
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setPage(1);
                void load();
              }
            }}
          />
        </div>
        <div className="space-y-1">
          <label className="text-[10px] uppercase tracking-wider text-muted-foreground">
            spuType
          </label>
          <select
            value={spuType}
            onChange={(e) => {
              setSpuType(e.target.value);
              setPage(1);
            }}
            className="flex h-9 rounded-md border border-input bg-transparent px-3 py-1 text-sm"
          >
            <option value="">全部</option>
            {Object.entries(WGCARDS_SPU_TYPE_LABEL).map(([k, v]) => (
              <option key={k} value={k}>
                {k} · {v}
              </option>
            ))}
          </select>
        </div>
        <label className="flex h-9 items-center gap-2 text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={giftCardOnly}
            onChange={(e) => {
              setGiftCardOnly(e.target.checked);
              setPage(1);
            }}
          />
          仅礼品卡
        </label>
        <Button
          size="sm"
          variant="outline"
          disabled={loading}
          onClick={() => {
            setPage(1);
            void load();
          }}
        >
          搜索
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={loading}
          onClick={() => void load()}
        >
          <RefreshCw className={`size-4 ${loading ? "animate-spin" : ""}`} />
        </Button>
      </div>

      {error ? <p className="text-sm text-destructive">{error}</p> : null}

      <div className="overflow-x-auto rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>SKU ID</TableHead>
              <TableHead>SKU 名称</TableHead>
              <TableHead>Item ID</TableHead>
              <TableHead>商品名</TableHead>
              <TableHead>品牌</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>面值</TableHead>
              <TableHead>采购价</TableHead>
              <TableHead>库存</TableHead>
              <TableHead>币种</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {loading && !data ? (
              <TableRow>
                <TableCell
                  colSpan={10}
                  className="py-12 text-center text-muted-foreground"
                >
                  加载中...
                </TableCell>
              </TableRow>
            ) : !data?.records.length ? (
              <TableRow>
                <TableCell
                  colSpan={10}
                  className="py-12 text-center text-muted-foreground"
                >
                  暂无商品
                </TableCell>
              </TableRow>
            ) : (
              data.records.map((row) => (
                <TableRow key={`${row.itemId}-${row.skuId}`}>
                  <TableCell className="font-mono text-xs">
                    {row.skuId ?? "—"}
                  </TableCell>
                  <TableCell>{row.skuName ?? "—"}</TableCell>
                  <TableCell className="font-mono text-xs">
                    {row.itemId ?? "—"}
                  </TableCell>
                  <TableCell>{row.itemName ?? "—"}</TableCell>
                  <TableCell>{row.itemBrandName ?? "—"}</TableCell>
                  <TableCell>
                    <Badge variant="outline">{spuTypeLabel(row.spuType)}</Badge>
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {formatFaceRange(row)}
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {row.skuPrice != null
                      ? `${row.skuPrice} ${row.skuPriceCurrency ?? ""}`
                      : "—"}
                  </TableCell>
                  <TableCell className="font-mono tabular-nums">
                    {row.stock ?? "—"}
                  </TableCell>
                  <TableCell>{row.currencyCode ?? "—"}</TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {data && data.pages > 1 ? (
        <div className="flex items-center justify-between text-xs text-muted-foreground">
          <span>
            第 {data.current} / {data.pages} 页 · 共 {data.total} 条 SKU
          </span>
          <div className="flex gap-2">
            <Button
              size="sm"
              variant="outline"
              disabled={loading || page <= 1}
              onClick={() => setPage((p) => Math.max(1, p - 1))}
            >
              上一页
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={loading || page >= data.pages}
              onClick={() => setPage((p) => p + 1)}
            >
              下一页
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

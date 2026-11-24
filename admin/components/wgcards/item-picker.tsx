"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { PackageSearch, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import {
  formatFaceRange,
  isGiftCardSpu,
  spuTypeLabel,
  type WGCardsCatalogPage,
} from "@/lib/wgcards-catalog";

interface WGCardsSkuRecord {
  skuId: string;
  skuName: string;
  minFaceValue: number;
  maxFaceValue: number;
  skuPrice?: number;
  skuPriceCurrency?: string;
}

interface WGCardsItemRecord {
  itemId: string;
  itemName: string;
  itemBrandName?: string;
  currencyCode?: string;
  spuType?: number;
  skus?: WGCardsSkuRecord[];
}

/** WGCards spuType: 见 admin/lib/wgcards-catalog.ts */

function isGiftCardItem(item: WGCardsItemRecord) {
  return isGiftCardSpu(item.spuType);
}

interface ItemsPage {
  current: number;
  pages: number;
  records: WGCardsItemRecord[];
}

function catalogPageToItemsPage(page: WGCardsCatalogPage): ItemsPage {
  const itemMap = new Map<string, WGCardsItemRecord>();
  for (const [idx, row] of page.records.entries()) {
    const itemId = row.itemId ?? `catalog-row-${idx}`;
    const key = `${itemId}:${row.itemName ?? ""}`;
    const existing =
      itemMap.get(key) ??
      ({
        itemId,
        itemName: row.itemName ?? row.skuName ?? "未知商品",
        itemBrandName: row.itemBrandName,
        currencyCode: row.currencyCode,
        spuType: row.spuType,
        skus: [],
      } satisfies WGCardsItemRecord);

    if (row.skuId) {
      existing.skus?.push({
        skuId: row.skuId,
        skuName: row.skuName ?? row.skuId,
        minFaceValue: row.minFaceValue ?? 0,
        maxFaceValue: row.maxFaceValue ?? 0,
        skuPrice: row.skuPrice,
        skuPriceCurrency: row.skuPriceCurrency,
      });
    }
    itemMap.set(key, existing);
  }

  return {
    current: page.current,
    pages: page.pages,
    records: Array.from(itemMap.values()),
  };
}

export interface WGCardsSkuSelection {
  skuId: string;
  skuName: string;
  faceValue: number | null;
  itemName: string;
}

export function WGCardsItemPicker({
  supplierId,
  supplierName,
  currencyCode = "CNY",
  disabled,
  onSelect,
}: {
  supplierId: number;
  supplierName: string;
  currencyCode?: string;
  disabled?: boolean;
  onSelect: (selection: WGCardsSkuSelection) => void;
}) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [giftCardOnly, setGiftCardOnly] = useState(true);
  const [data, setData] = useState<ItemsPage | null>(null);

  const load = useCallback(async () => {
    if (!supplierId) return;
    setLoading(true);
    setError(null);
    try {
      const qs = new URLSearchParams({
        currency_code: currencyCode,
        language: "zh",
        current: String(page),
        size: "20",
      });
      const q = search.trim();
      if (/^\d+$/.test(q)) {
        const catalogQs = new URLSearchParams({
          q,
          gift_card_only: "false",
          current: "1",
          size: "20",
        });
        const resp = await api<WGCardsCatalogPage>(
          `/suppliers/${supplierId}/wgcards/catalog?${catalogQs.toString()}`,
        );
        setData(catalogPageToItemsPage(resp));
        return;
      }
      if (q) qs.set("item_name", q);

      const resp = await api<ItemsPage>(
        `/suppliers/${supplierId}/wgcards/items?${qs.toString()}`,
      );
      setData(resp);
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "加载商品失败",
      );
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [supplierId, currencyCode, page, search]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const flatSkus = useMemo(() => {
    const rows: Array<
      WGCardsSkuRecord & { itemName: string; spuType?: number }
    > = [];
    for (const item of data?.records ?? []) {
      if (giftCardOnly && !isGiftCardItem(item)) continue;
      for (const sku of item.skus ?? []) {
        rows.push({ ...sku, itemName: item.itemName, spuType: item.spuType });
      }
    }
    return rows;
  }, [data, giftCardOnly]);

  const pick = (sku: WGCardsSkuRecord & { itemName: string }) => {
    const min = sku.minFaceValue;
    const max = sku.maxFaceValue;
    const face =
      min === max && min > 0 ? min : min > 0 && max <= 0 ? min : null;
    onSelect({
      skuId: sku.skuId,
      skuName: sku.skuName,
      faceValue: face,
      itemName: sku.itemName,
    });
    setOpen(false);
  };

  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="outline"
        disabled={disabled || !supplierId}
        onClick={() => {
          setPage(1);
          setOpen(true);
        }}
      >
        <PackageSearch className="size-4" />
        浏览商品
      </Button>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-hidden">
          <DialogHeader>
            <DialogTitle>API 商品目录 · {supplierName}</DialogTitle>
          </DialogHeader>

          <div className="flex gap-2">
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="按 SKU ID 或商品名搜索…"
              className="font-sans"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  setPage(1);
                  void load();
                }
              }}
            />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => {
                setPage(1);
                void load();
              }}
              disabled={loading}
            >
              搜索
            </Button>
          </div>

          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={giftCardOnly}
              onChange={(e) => setGiftCardOnly(e.target.checked)}
            />
            仅显示礼品卡（批量入库与来单自动采购仅支持礼品卡 SKU）
          </label>

          <div className="max-h-[50vh] overflow-y-auto rounded-md border">
            {loading ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                <RefreshCw className="size-4 animate-spin" />
                加载中…
              </div>
            ) : error ? (
              <p className="p-4 text-sm text-destructive">{error}</p>
            ) : flatSkus.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">暂无商品</p>
            ) : (
              <table className="w-full text-xs">
                <thead className="sticky top-0 bg-muted/80 backdrop-blur">
                  <tr className="text-left text-[10px] uppercase tracking-wider text-muted-foreground">
                    <th className="px-3 py-2">SKU ID</th>
                    <th className="px-3 py-2">名称</th>
                    <th className="px-3 py-2">类型</th>
                    <th className="px-3 py-2">面值</th>
                    <th className="px-3 py-2">参考价</th>
                    <th className="px-3 py-2" />
                  </tr>
                </thead>
                <tbody>
                  {flatSkus.map((sku) => (
                    <tr key={sku.skuId} className="border-t hover:bg-muted/40">
                      <td className="px-3 py-2 font-mono">{sku.skuId}</td>
                      <td className="px-3 py-2">
                        <p>{sku.skuName}</p>
                        <p className="text-[10px] text-muted-foreground">
                          {sku.itemName}
                        </p>
                      </td>
                      <td className="px-3 py-2 text-[10px]">
                        {spuTypeLabel(sku.spuType)}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums">
                        {formatFaceRange(sku)}
                      </td>
                      <td className="px-3 py-2 font-mono tabular-nums">
                        {sku.skuPrice != null
                          ? `${sku.skuPrice} ${sku.skuPriceCurrency ?? ""}`
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <Button
                          type="button"
                          size="sm"
                          variant="ghost"
                          disabled={sku.spuType != null && sku.spuType !== 2}
                          title={
                            sku.spuType != null && sku.spuType !== 2
                              ? "仅礼品卡可批量入库取卡密"
                              : undefined
                          }
                          onClick={() => pick(sku)}
                        >
                          选用
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {data && data.pages > 1 ? (
            <div className="flex items-center justify-between text-xs text-muted-foreground">
              <span>
                第 {data.current} / {data.pages} 页
              </span>
              <div className="flex gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={loading || page <= 1}
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                >
                  上一页
                </Button>
                <Button
                  type="button"
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

          <DialogFooter>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => void load()}
              disabled={loading}
            >
              <RefreshCw
                className={`mr-1 size-4 ${loading ? "animate-spin" : ""}`}
              />
              刷新
            </Button>
            <DialogClose asChild>
              <Button type="button" variant="ghost" size="sm">
                关闭
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

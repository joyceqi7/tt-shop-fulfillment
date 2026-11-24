/** WGCards spuType 映射（与供应商文档一致） */
export const WGCARDS_SPU_TYPE_LABEL: Record<number, string> = {
  1: "游戏",
  2: "礼品卡",
  3: "软件",
  4: "微软商品",
  5: "充值商品",
  7: "WWG 推荐",
  8: "充值兑换码",
  9: "Esim 商品",
  11: "任天堂游戏",
};

export function spuTypeLabel(spuType?: number | null): string {
  if (spuType == null) return "未知";
  return WGCARDS_SPU_TYPE_LABEL[spuType] ?? `类型${spuType}`;
}

export function isGiftCardSpu(spuType?: number | null): boolean {
  return spuType === 2;
}

export interface WGCardsCatalogRow {
  itemId?: string;
  itemName?: string;
  itemBrandName?: string;
  currencyCode?: string;
  spuType?: number;
  description?: string;
  skuId?: string;
  skuName?: string;
  minFaceValue?: number;
  maxFaceValue?: number;
  skuPrice?: number;
  skuPriceCurrency?: string;
  stock?: number;
}

export interface WGCardsCatalogPage {
  current: number;
  pages: number;
  total: number;
  records: WGCardsCatalogRow[];
}

export const CATALOG_COLUMNS: Array<{
  key: keyof WGCardsCatalogRow | "faceRange";
  label: string;
  mono?: boolean;
}> = [
  { key: "skuId", label: "SKU ID", mono: true },
  { key: "skuName", label: "SKU 名称" },
  { key: "itemId", label: "Item ID", mono: true },
  { key: "itemName", label: "商品名" },
  { key: "itemBrandName", label: "品牌" },
  { key: "spuType", label: "类型" },
  { key: "faceRange", label: "面值" },
  { key: "skuPrice", label: "采购价", mono: true },
  { key: "stock", label: "库存", mono: true },
  { key: "currencyCode", label: "币种" },
];

export function formatFaceRange(row: WGCardsCatalogRow): string {
  const min = row.minFaceValue;
  const max = row.maxFaceValue;
  if (min == null && max == null) return "—";
  if (min === max) return String(min ?? "—");
  return `${min ?? "?"}–${max ?? "?"}`;
}

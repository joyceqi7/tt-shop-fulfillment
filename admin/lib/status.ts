import type { BadgeProps } from "@/components/ui/badge";

/**
 * 把后端的状态字符串映射到 Badge 组件的色系。
 * 集中维护，避免散落到各个页面。
 */

type Variant = NonNullable<BadgeProps["variant"]>;

/** TikTok Shop 订单状态 */
export const ORDER_STATUS_VARIANT: Record<string, Variant> = {
  UNPAID: "muted",
  ON_HOLD: "warning",
  AWAITING_SHIPMENT: "warning",
  AWAITING_COLLECTION: "warning",
  PARTIALLY_SHIPPING: "warning",
  IN_TRANSIT: "warning",
  DELIVERED: "success",
  COMPLETED: "success",
  CANCELLED: "destructive",
  RETURN: "destructive",
};

/** order_line 内部状态（履约视角） */
export const LINE_STATUS_VARIANT: Record<string, Variant> = {
  pending: "warning",
  shipped: "success",
  failed: "destructive",
  cancelled: "muted",
};

/** 卡密状态 */
export const CODE_STATUS_VARIANT: Record<string, Variant> = {
  free: "success",
  locked: "warning",
  used: "muted",
  void: "destructive",
  expired: "destructive",
};

/** 采购单付款状态（unpaid / partial / paid / overdue） */
export const PAYMENT_STATUS_VARIANT: Record<string, Variant> = {
  unpaid: "warning",
  partial: "warning",
  paid: "success",
  overdue: "destructive",
};

export const PAYMENT_STATUS_LABEL: Record<string, string> = {
  unpaid: "待付款",
  partial: "部分付款",
  paid: "已付清",
  overdue: "逾期",
};

/** order_line.failed_reason 中文 */
export const LINE_FAILED_REASON_LABEL: Record<string, string> = {
  sku_not_mapped: "SKU 未映射",
  no_stock: "库存不足",
  supplier_invalid: "API 供应商无效",
  supplier_realtime_failed: "API 实时采购失败",
  wgcards_supplier_invalid: "API 供应商无效",
  wgcards_realtime_failed: "API 实时采购失败",
  manual_code_redeem_failed: "手动补码回传失败",
};

/** 字典里没匹配的状态默认走 muted（中性灰） */
export function variantFor(
  map: Record<string, Variant>,
  key: string | null | undefined,
): Variant {
  if (!key) return "muted";
  return map[key] ?? "muted";
}

"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";

export interface SkuFormState {
  internal_sku_id: string;
  product_name: string;
  seller_sku: string;
  tts_product_id: string;
  tts_sku_id: string;
  face_value_cents: string;
  list_price_cents: string;
  safety_stock_threshold: string;
  active: boolean;
  inventory_locked: boolean;
}

export const emptySku: SkuFormState = {
  internal_sku_id: "",
  product_name: "",
  seller_sku: "",
  tts_product_id: "",
  tts_sku_id: "",
  face_value_cents: "",
  list_price_cents: "",
  safety_stock_threshold: "",
  active: true,
  inventory_locked: false,
};

export function useSkuForm(init: SkuFormState = emptySku) {
  const [state, setState] = useState<SkuFormState>(init);
  const update = <K extends keyof SkuFormState>(k: K, v: SkuFormState[K]) =>
    setState((s) => ({ ...s, [k]: v }));
  return { state, update, setState };
}

export function SkuForm({
  state,
  update,
  disableInternalId,
  source = "edit",
}: {
  state: SkuFormState;
  update: <K extends keyof SkuFormState>(k: K, v: SkuFormState[K]) => void;
  disableInternalId?: boolean;
  source?: "create" | "edit";
}) {
  const hasTtsContext = Boolean(
    state.seller_sku || state.tts_product_id || state.tts_sku_id,
  );
  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {source === "create" && hasTtsContext ? (
        <div className="col-span-full rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-muted-foreground">
          已带入 TT 商品信息。创建后订单会按这些 TT 字段命中该内部 SKU。
        </div>
      ) : null}
      <Field label="内部 SKU">
        <Input
          value={state.internal_sku_id}
          disabled={disableInternalId}
          onChange={(e) => update("internal_sku_id", e.target.value)}
          className="font-mono"
          placeholder="例如 GAME_GIFT_CARD"
        />
      </Field>
      <Field label="礼品卡面额（美分）">
        <Input
          type="number"
          min={0}
          value={state.face_value_cents}
          onChange={(e) => update("face_value_cents", e.target.value)}
          className="font-mono"
          placeholder="例如 1000 = $10"
        />
      </Field>
      <Field label="TT 售价（美分/分）">
        <Input
          type="number"
          min={0}
          value={state.list_price_cents}
          onChange={(e) => update("list_price_cents", e.target.value)}
          className="font-mono"
          placeholder="TT 挂牌价，用于调价预警"
        />
      </Field>
      <Field label="商品名称">
        <Input
          value={state.product_name}
          onChange={(e) => update("product_name", e.target.value)}
          placeholder="商品名（用于库存看板）"
        />
      </Field>
      <Field label="TT Seller SKU">
        <Input
          value={state.seller_sku}
          onChange={(e) => update("seller_sku", e.target.value)}
          className="font-mono"
          placeholder="TT 卖家自定义 SKU"
        />
      </Field>
      <Field label="TT Product ID">
        <Input
          value={state.tts_product_id}
          onChange={(e) => update("tts_product_id", e.target.value)}
          className="font-mono"
        />
      </Field>
      <Field label="TT SKU ID">
        <Input
          value={state.tts_sku_id}
          onChange={(e) => update("tts_sku_id", e.target.value)}
          className="font-mono"
        />
      </Field>
      <Field label="安全库存阈值（可选）">
        <Input
          type="number"
          min={0}
          value={state.safety_stock_threshold}
          onChange={(e) => update("safety_stock_threshold", e.target.value)}
          className="font-mono"
          placeholder="为空则使用全局阈值"
        />
      </Field>
      <Field label="状态">
        <div className="space-y-2">
          <label className="flex h-9 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={state.active}
              onChange={(e) => update("active", e.target.checked)}
            />
            active（取消勾选 = 软删）
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={state.inventory_locked}
              onChange={(e) => update("inventory_locked", e.target.checked)}
            />
            锁库存（调价预警或手动锁定）
          </label>
        </div>
      </Field>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

export function buildSkuPayload(state: SkuFormState): {
  internal_sku_id: string;
  product_name: string | null;
  seller_sku: string | null;
  tts_product_id: string | null;
  tts_sku_id: string | null;
  face_value: number;
  safety_stock_threshold: number | null;
  list_price_cents: number | null;
  active: boolean;
  inventory_locked: boolean;
} {
  const cents = Number(state.face_value_cents);
  const listRaw = state.list_price_cents.trim();
  const listPrice = listRaw ? Number(listRaw) : null;
  const thresholdRaw = state.safety_stock_threshold.trim();
  const threshold = thresholdRaw ? Number(thresholdRaw) : null;
  if (!state.internal_sku_id.trim()) throw new Error("internal_sku_id 必填");
  if (!Number.isFinite(cents) || cents < 0) throw new Error("face_value 非法");
  if (listPrice !== null && (!Number.isFinite(listPrice) || listPrice < 0)) {
    throw new Error("list_price 非法");
  }
  if (threshold !== null && (!Number.isFinite(threshold) || threshold < 0)) {
    throw new Error("safety_stock_threshold 非法");
  }
  return {
    internal_sku_id: state.internal_sku_id.trim(),
    product_name: state.product_name.trim() || null,
    seller_sku: state.seller_sku.trim() || null,
    tts_product_id: state.tts_product_id.trim() || null,
    tts_sku_id: state.tts_sku_id.trim() || null,
    face_value: Math.round(cents),
    list_price_cents: listPrice === null ? null : Math.round(listPrice),
    safety_stock_threshold: threshold === null ? null : Math.round(threshold),
    active: state.active,
    inventory_locked: state.inventory_locked,
  };
}

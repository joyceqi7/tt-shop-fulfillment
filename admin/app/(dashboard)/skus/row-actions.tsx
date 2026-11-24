"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Pencil, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, ApiError } from "@/lib/api";
import type { SkuRow, SupplierRow } from "@/lib/types";

import { SkuForm, buildSkuPayload, useSkuForm } from "./sku-form";
import { SupplierLinksEditor } from "./supplier-links-editor";

export function SkuRowActions({
  sku,
  apiSuppliers,
  autoOpen = false,
}: {
  sku: SkuRow;
  apiSuppliers: SupplierRow[];
  autoOpen?: boolean;
}) {
  const router = useRouter();
  const [editOpen, setEditOpen] = useState(autoOpen);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { state, update } = useSkuForm({
    internal_sku_id: sku.internal_sku_id,
    product_name: sku.product_name ?? "",
    seller_sku: sku.seller_sku ?? "",
    tts_product_id: sku.tts_product_id ?? "",
    tts_sku_id: sku.tts_sku_id ?? "",
    face_value_cents: String(sku.face_value),
    list_price_cents:
      sku.list_price_cents == null ? "" : String(sku.list_price_cents),
    safety_stock_threshold:
      sku.safety_stock_threshold === null
        ? ""
        : String(sku.safety_stock_threshold),
    active: sku.active,
    inventory_locked: sku.inventory_locked ?? false,
  });

  useEffect(() => {
    if (autoOpen) setEditOpen(true);
  }, [autoOpen]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = buildSkuPayload(state);
      await api(`/skus/${sku.id}`, { method: "PATCH", body: payload });
      setEditOpen(false);
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "保存失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const softDelete = async () => {
    if (!confirm(`软删 SKU "${sku.internal_sku_id}"？(不会真删，只置 active=false)`))
      return;
    try {
      await api(`/skus/${sku.id}`, { method: "DELETE" });
      router.refresh();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "删除失败");
    }
  };

  return (
    <div className="flex justify-end gap-1">
      <Dialog open={editOpen} onOpenChange={setEditOpen}>
        <Button size="sm" variant="outline" onClick={() => setEditOpen(true)}>
          <Pencil />
          编辑
        </Button>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>编辑 SKU #{sku.id}</DialogTitle>
          </DialogHeader>
          <SkuForm state={state} update={update} disableInternalId />
          <SupplierLinksEditor
            skuMappingId={sku.id}
            apiSuppliers={apiSuppliers}
          />
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost" size="sm" disabled={busy}>
                取消
              </Button>
            </DialogClose>
            <Button size="sm" onClick={save} loading={busy}>
              保存 SKU
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      {sku.active ? (
        <Button size="sm" variant="outline" onClick={softDelete}>
          <Trash2 />
          停用
        </Button>
      ) : null}
    </div>
  );
}

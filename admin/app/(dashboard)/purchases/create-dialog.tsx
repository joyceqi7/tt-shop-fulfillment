"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { SupplierRow } from "@/lib/types";

const today = () => new Date().toISOString().slice(0, 10);

export function CreatePurchaseDialog({
  suppliers,
}: {
  suppliers: SupplierRow[];
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [poNumber, setPoNumber] = useState("");
  const [supplierId, setSupplierId] = useState(
    suppliers.length > 0 ? String(suppliers[0].id) : "",
  );
  const [skuId, setSkuId] = useState("");
  const [faceValue, setFaceValue] = useState("");
  const [quantity, setQuantity] = useState("");
  const [unitCost, setUnitCost] = useState("");
  const [purchaseDate, setPurchaseDate] = useState(today());
  const [paymentDueDate, setPaymentDueDate] = useState("");
  const [note, setNote] = useState("");

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      if (!supplierId) throw new Error("请选择供货商");
      if (!skuId.trim()) throw new Error("SKU 必填");
      const payload = {
        po_number: poNumber.trim() || null,
        supplier_id: Number(supplierId),
        sku_id: skuId.trim(),
        face_value: Math.round(Number(faceValue)),
        quantity: Math.round(Number(quantity)),
        unit_cost: Math.round(Number(unitCost)),
        purchase_date: purchaseDate,
        payment_due_date: paymentDueDate || null,
        note: note.trim() || null,
      };
      if (!Number.isFinite(payload.face_value) || payload.face_value < 0)
        throw new Error("面额非法");
      if (!Number.isFinite(payload.quantity) || payload.quantity < 1)
        throw new Error("数量非法");
      if (!Number.isFinite(payload.unit_cost) || payload.unit_cost < 0)
        throw new Error("单价非法");

      await api("/purchases", { method: "POST", body: payload });
      setOpen(false);
      setPoNumber("");
      setSkuId("");
      setFaceValue("");
      setQuantity("");
      setUnitCost("");
      setPurchaseDate(today());
      setPaymentDueDate("");
      setNote("");
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "创建失败",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" disabled={suppliers.length === 0}>
          <Plus />
          {suppliers.length === 0 ? "请先添加供货商" : "新增采购单"}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>新增采购单</DialogTitle>
          <DialogDescription>
            进价以美分计；不填到期日会按供货商默认账期自动计算。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-3">
          <Field label="PO 号（留空则自动生成）">
            <Input
              value={poNumber}
              onChange={(e) => setPoNumber(e.target.value)}
              className="font-mono"
              placeholder="PO20260427-001"
            />
          </Field>
          <Field label="供货商">
            <Select
              value={supplierId}
              onChange={(e) => setSupplierId(e.target.value)}
            >
              {suppliers.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}（账期 {s.default_payment_terms_days}d）
                </option>
              ))}
            </Select>
          </Field>
          <Field label="internal_sku_id">
            <Input
              value={skuId}
              onChange={(e) => setSkuId(e.target.value)}
              className="font-mono"
            />
          </Field>
          <Field label="面额（美分）">
            <Input
              type="number"
              min={0}
              value={faceValue}
              onChange={(e) => setFaceValue(e.target.value)}
              className="font-mono"
              placeholder="1000 = $10"
            />
          </Field>
          <Field label="数量">
            <Input
              type="number"
              min={1}
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              className="font-mono"
            />
          </Field>
          <Field label="单价（美分）">
            <Input
              type="number"
              min={0}
              value={unitCost}
              onChange={(e) => setUnitCost(e.target.value)}
              className="font-mono"
              placeholder="进价（含税）"
            />
          </Field>
          <Field label="采购日期">
            <Input
              type="date"
              value={purchaseDate}
              onChange={(e) => setPurchaseDate(e.target.value)}
              className="font-mono"
            />
          </Field>
          <Field label="到期日（可空）">
            <Input
              type="date"
              value={paymentDueDate}
              onChange={(e) => setPaymentDueDate(e.target.value)}
              className="font-mono"
            />
          </Field>
        </div>
        <div className="space-y-1">
          <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
            备注
          </label>
          <Textarea
            rows={2}
            value={note}
            onChange={(e) => setNote(e.target.value)}
            className="font-sans"
          />
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm" disabled={busy}>
              取消
            </Button>
          </DialogClose>
          <Button size="sm" onClick={submit} loading={busy}>
            创建
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
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

"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { CreditCard, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { PurchaseRow } from "@/lib/types";
import { formatCents } from "@/lib/utils";

export function PurchaseRowActions({ purchase }: { purchase: PurchaseRow }) {
  const router = useRouter();
  const [payOpen, setPayOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const remaining = purchase.total_cost - purchase.amount_paid;
  const [amount, setAmount] = useState(String(remaining));
  const [note, setNote] = useState("");

  const pay = async () => {
    setBusy(true);
    setError(null);
    try {
      const cents = Math.round(Number(amount));
      if (!Number.isFinite(cents) || cents < 1) throw new Error("金额非法");
      await api(`/purchases/${purchase.id}/pay`, {
        method: "POST",
        body: { amount: cents, note: note.trim() || null },
      });
      setPayOpen(false);
      setNote("");
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "记录失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!confirm(`删除采购单 ${purchase.po_number}？仅未导入 + 无付款的 PO 可删。`))
      return;
    try {
      await api(`/purchases/${purchase.id}`, { method: "DELETE" });
      router.refresh();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "删除失败");
    }
  };

  return (
    <div className="flex justify-end gap-1">
      {purchase.payment_status !== "paid" ? (
        <Dialog open={payOpen} onOpenChange={setPayOpen}>
          <Button size="sm" variant="outline" onClick={() => setPayOpen(true)}>
            <CreditCard />
            记录付款
          </Button>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>记录付款 · {purchase.po_number}</DialogTitle>
              <DialogDescription>
                总额 {formatCents(purchase.total_cost)} · 已付{" "}
                {formatCents(purchase.amount_paid)} · 剩余{" "}
                <span className="font-medium text-warning">
                  {formatCents(remaining)}
                </span>
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-3">
              <div className="space-y-1">
                <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
                  本次付款（美分）
                </label>
                <Input
                  type="number"
                  min={1}
                  max={remaining}
                  value={amount}
                  onChange={(e) => setAmount(e.target.value)}
                  className="font-mono"
                />
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
                  placeholder="例如：转账凭证号 / 银行流水"
                />
              </div>
              {error ? <p className="text-xs text-destructive">{error}</p> : null}
            </div>
            <DialogFooter>
              <DialogClose asChild>
                <Button variant="ghost" size="sm" disabled={busy}>
                  取消
                </Button>
              </DialogClose>
              <Button size="sm" onClick={pay} loading={busy}>
                确认
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      ) : null}
      {purchase.batch_id === null && purchase.amount_paid === 0 ? (
        <Button size="sm" variant="outline" onClick={remove}>
          <Trash2 />
          删除
        </Button>
      ) : null}
    </div>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Upload } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { PurchaseRow } from "@/lib/types";
import { formatCents } from "@/lib/utils";

/**
 * 导入卡密的对话框：
 * 1. 必须先选一张未导入过的 PO（batch_id IS NULL）
 * 2. 粘贴明文（每行一张）+ 可选过期时间
 * 3. 后端去重 + Fernet 加密 + 回填 PO.batch_id
 */
export function ImportDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<PurchaseRow[]>([]);
  const [poId, setPoId] = useState<string>("");
  const [codes, setCodes] = useState("");
  const [expiredAt, setExpiredAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setError(null);
    setSuccess(null);
    api<{ items: PurchaseRow[] }>("/purchases?limit=200")
      .then((r) => {
        const eligible = r.items.filter((p) => p.batch_id === null);
        setPos(eligible);
        if (eligible.length > 0 && !poId) {
          setPoId(String(eligible[0].id));
        }
      })
      .catch((e: unknown) => {
        setError(e instanceof ApiError ? e.detail : "拉取采购单失败");
      });
  }, [open, poId]);

  const submit = async () => {
    if (!poId) {
      setError("请选择采购单");
      return;
    }
    const lines = codes
      .split(/\r?\n/)
      .map((s) => s.trim())
      .filter(Boolean);
    if (lines.length === 0) {
      setError("请粘贴至少一张码");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await api<{
        batch_id: string;
        total: number;
        inserted: number;
        skipped_duplicate: number;
      }>("/inventory/import", {
        method: "POST",
        body: {
          purchase_order_id: Number(poId),
          plaintext_codes: lines,
          expired_at: expiredAt ? new Date(expiredAt).toISOString() : null,
        },
      });
      setSuccess(
        `成功导入 ${r.inserted} 张（去重跳过 ${r.skipped_duplicate}）。批次 ${r.batch_id.slice(0, 8)}…`,
      );
      setCodes("");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "导入失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <Upload />
          导入卡密
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>批量导入卡密</DialogTitle>
          <DialogDescription>
            必须关联到一张未导入过的采购单。粘贴明文，每行一张；后端会自动 SHA-256 去重 + Fernet 加密。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
              采购单（PO）
            </label>
            <Select value={poId} onChange={(e) => setPoId(e.target.value)}>
              {pos.length === 0 ? (
                <option value="">— 没有可用 PO，请先到采购单创建 —</option>
              ) : (
                pos.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.po_number} · {p.sku_id} · {formatCents(p.face_value)} ×{" "}
                    {p.quantity}（{p.supplier_name ?? p.supplier_id}）
                  </option>
                ))
              )}
            </Select>
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
              卡密（每行一张）
            </label>
            <Textarea
              value={codes}
              onChange={(e) => setCodes(e.target.value)}
              rows={10}
              placeholder={"AAAA-BBBB-CCCC-DDDD\nEEEE-FFFF-GGGG-HHHH\n…"}
            />
          </div>
          <div>
            <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
              过期时间（可选）
            </label>
            <input
              type="datetime-local"
              value={expiredAt}
              onChange={(e) => setExpiredAt(e.target.value)}
              className="flex h-9 w-full rounded-md border border-border bg-background px-3 text-sm"
            />
          </div>
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          {success ? <p className="text-xs text-success">{success}</p> : null}
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm" disabled={busy}>
              取消
            </Button>
          </DialogClose>
          <Button size="sm" onClick={submit} loading={busy}>
            导入
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

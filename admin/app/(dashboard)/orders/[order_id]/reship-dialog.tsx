"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { RotateCw } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";

/**
 * 触发 fulfill_order 重发 —— 后端会幂等过滤已 shipped 的子单，只补 pending/failed。
 * 操作完成后 refresh 路由，让 SSR 详情页拿到最新 fulfillment_logs。
 */
export function ReshipDialog({ orderId }: { orderId: string }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api(`/orders/${encodeURIComponent(orderId)}/reship`, {
        method: "POST",
        body: { note: note.trim() || null },
      });
      setOpen(false);
      setNote("");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "重发失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm">
          <RotateCw />
          手动重发
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>重发订单 {orderId}</DialogTitle>
          <DialogDescription>
            触发 fulfill_order，已发出的子单会被幂等过滤，只补未发出的部分。
            操作会落入审计日志。
          </DialogDescription>
        </DialogHeader>
        <Textarea
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="可选备注：为什么要重发，例如 上次 redeem 5008 失败 / 客服工单 #123"
          rows={4}
        />
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm" disabled={submitting}>
              取消
            </Button>
          </DialogClose>
          <Button size="sm" onClick={submit} loading={submitting}>
            确认重发
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { KeyRound } from "lucide-react";

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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";

export function ManualCodeDialog({
  orderId,
  orderLineId,
  label = "手动补码",
  variant = "outline",
}: {
  orderId: string;
  orderLineId: string;
  label?: string;
  variant?: "default" | "outline" | "ghost";
}) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [note, setNote] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      await api(
        `/orders/${encodeURIComponent(orderId)}/lines/${encodeURIComponent(
          orderLineId,
        )}/manual-code`,
        {
          method: "POST",
          body: {
            plaintext_code: code.trim(),
            note: note.trim() || null,
          },
        },
      );
      setOpen(false);
      setCode("");
      setNote("");
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "补码失败");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button size="sm" variant={variant}>
          <KeyRound />
          {label}
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>手动补码</DialogTitle>
          <DialogDescription>
            系统会把这张 CDK 直接发给该子单，并记录库存、履约日志和审计日志。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">
              CDK
            </label>
            <Textarea
              value={code}
              onChange={(e) => setCode(e.target.value)}
              placeholder="粘贴供应商给你的 CDK"
              rows={4}
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1">
            <label className="text-xs font-medium text-muted-foreground">
              备注
            </label>
            <Textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="可选：客服工单号、供应商来源、补发原因"
              rows={3}
            />
          </div>
        </div>
        {error ? <p className="text-xs text-destructive">{error}</p> : null}
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost" size="sm" disabled={submitting}>
              取消
            </Button>
          </DialogClose>
          <Button
            size="sm"
            onClick={submit}
            loading={submitting}
            disabled={!code.trim()}
          >
            确认补发
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { api, ApiError } from "@/lib/api";

import {
  SupplierForm,
  buildSupplierPayload,
  emptySupplier,
  useSupplierForm,
} from "./supplier-form";

export function CreateSupplierDialog() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { state, update, setState } = useSupplierForm();

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = buildSupplierPayload(state);
      const { enabled, ...rest } = payload;
      void enabled;
      await api("/suppliers", { method: "POST", body: rest });
      setOpen(false);
      setState(emptySupplier);
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
        <Button size="sm">
          <Plus />
          新增供货商
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>新增供货商</DialogTitle>
        </DialogHeader>
        <SupplierForm state={state} update={update} />
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

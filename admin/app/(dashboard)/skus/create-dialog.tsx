"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Plus, Wand2 } from "lucide-react";

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
import { api, ApiError } from "@/lib/api";

import {
  SkuForm,
  buildSkuPayload,
  emptySku,
  type SkuFormState,
  useSkuForm,
} from "./sku-form";

function mergeInitial(initial?: Partial<SkuFormState>): SkuFormState {
  return { ...emptySku, ...initial };
}

export function CreateSkuDialog({
  initial,
}: {
  initial?: Partial<SkuFormState>;
}) {
  const router = useRouter();
  const isTtsMatch = Boolean(initial?.tts_product_id || initial?.tts_sku_id);
  const initialState = useMemo(() => mergeInitial(initial), [initial]);
  const [open, setOpen] = useState(isTtsMatch);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { state, update, setState } = useSkuForm(initialState);

  useEffect(() => {
    setState(initialState);
    if (isTtsMatch) setOpen(true);
  }, [initialState, isTtsMatch, setState]);

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = buildSkuPayload(state);
      await api("/skus", { method: "POST", body: payload });
      setOpen(false);
      setState(emptySku);
      if (isTtsMatch) {
        router.replace("/skus");
      }
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
          {isTtsMatch ? <Wand2 /> : <Plus />}
          {isTtsMatch ? "匹配 TT 商品" : "新增 SKU"}
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{isTtsMatch ? "匹配 TT 商品" : "新增 SKU 映射"}</DialogTitle>
          <DialogDescription>
            {isTtsMatch
              ? "TT 商品信息已自动带入，只需要确认内部 SKU、面额和安全库存。"
              : "internal_sku_id + face_value 是库存的主键；TT 三个字段任填一项即可让 webhook 命中映射。"}
          </DialogDescription>
        </DialogHeader>
        <SkuForm state={state} update={update} source="create" />
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

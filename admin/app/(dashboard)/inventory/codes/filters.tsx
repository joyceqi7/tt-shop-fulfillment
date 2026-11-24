"use client";

import { useRouter, usePathname } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

import { ImportDialog } from "../import-dialog";

const STATUSES = ["free", "locked", "used", "void", "expired", "recycled"];

export function CodesFilters({
  initial,
}: {
  initial: { status?: string; sku_id?: string; batch_id?: string };
}) {
  const router = useRouter();
  const pathname = usePathname();
  const [status, setStatus] = useState(initial.status ?? "");
  const [skuId, setSkuId] = useState(initial.sku_id ?? "");
  const [batchId, setBatchId] = useState(initial.batch_id ?? "");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    if (status) next.set("status", status);
    if (skuId.trim()) next.set("sku_id", skuId.trim());
    if (batchId.trim()) next.set("batch_id", batchId.trim());
    router.push(`${pathname}?${next.toString()}`);
  };

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <div className="min-w-[140px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          状态
        </label>
        <Select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </Select>
      </div>
      <div className="min-w-[160px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          SKU
        </label>
        <Input
          value={skuId}
          onChange={(e) => setSkuId(e.target.value)}
          placeholder="internal_sku_id"
          className="font-mono"
        />
      </div>
      <div className="min-w-[200px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          批次
        </label>
        <Input
          value={batchId}
          onChange={(e) => setBatchId(e.target.value)}
          placeholder="batch_id (UUID)"
          className="font-mono"
        />
      </div>
      <Button type="submit" size="sm">
        过滤
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={() => {
          setStatus("");
          setSkuId("");
          setBatchId("");
          router.push(pathname);
        }}
      >
        重置
      </Button>
      <div className="ml-auto">
        <ImportDialog />
      </div>
    </form>
  );
}

"use client";

import { useRouter, usePathname } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";

const STATUSES = [
  "AWAITING_SHIPMENT",
  "PARTIALLY_SHIPPING",
  "DELIVERED",
  "COMPLETED",
  "CANCELLED",
  "RETURN",
  "UNKNOWN",
] as const;

interface FiltersProps {
  initial: {
    status?: string;
    q?: string;
    buyer_email?: string;
    buyer_tt_id?: string;
    product_name?: string;
    tt_settlement_status?: string;
    only_failed?: string;
  };
}

/**
 * 表单提交时把值塞回 URL。Server component 重新渲染并拉新数据。
 * 不做"输入即过滤"的实时搜索，避免每个键击都打 DB；管理员场景下回车确认是常态。
 */
export function OrdersFilters({ initial }: FiltersProps) {
  const router = useRouter();
  const pathname = usePathname();
  const [q, setQ] = useState(initial.q ?? "");
  const [status, setStatus] = useState(initial.status ?? "");
  const [buyerEmail, setBuyerEmail] = useState(initial.buyer_email ?? "");
  const [buyerTtId, setBuyerTtId] = useState(initial.buyer_tt_id ?? "");
  const [productName, setProductName] = useState(initial.product_name ?? "");
  const [settlementStatus, setSettlementStatus] = useState(
    initial.tt_settlement_status ?? "",
  );
  const [onlyFailed, setOnlyFailed] = useState(initial.only_failed === "1");

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const next = new URLSearchParams();
    if (q.trim()) next.set("q", q.trim());
    if (status) next.set("status", status);
    if (buyerEmail.trim()) next.set("buyer_email", buyerEmail.trim());
    if (buyerTtId.trim()) next.set("buyer_tt_id", buyerTtId.trim());
    if (productName.trim()) next.set("product_name", productName.trim());
    if (settlementStatus.trim())
      next.set("tt_settlement_status", settlementStatus.trim());
    if (onlyFailed) next.set("only_failed", "1");
    router.push(`${pathname}?${next.toString()}`);
  };

  const reset = () => {
    setQ("");
    setStatus("");
    setBuyerEmail("");
    setBuyerTtId("");
    setProductName("");
    setSettlementStatus("");
    setOnlyFailed(false);
    router.push(pathname);
  };

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      <div className="grow min-w-[180px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          搜索 order_id
        </label>
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="前缀匹配"
          className="font-mono"
        />
      </div>
      <div className="min-w-[180px]">
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
      <div className="min-w-[180px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          顾客邮箱
        </label>
        <Input
          value={buyerEmail}
          onChange={(e) => setBuyerEmail(e.target.value)}
          placeholder="模糊匹配"
        />
      </div>
      <div className="min-w-[180px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          顾客 TT ID
        </label>
        <Input
          value={buyerTtId}
          onChange={(e) => setBuyerTtId(e.target.value)}
          placeholder="模糊匹配"
          className="font-mono"
        />
      </div>
      <div className="min-w-[180px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          产品名 / SKU
        </label>
        <Input
          value={productName}
          onChange={(e) => setProductName(e.target.value)}
          placeholder="模糊匹配"
        />
      </div>
      <div className="min-w-[180px]">
        <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
          TT 结款状态
        </label>
        <Input
          value={settlementStatus}
          onChange={(e) => setSettlementStatus(e.target.value)}
          placeholder="如 PAID / PROCESSING"
        />
      </div>
      <label className="flex h-9 items-center gap-2 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={onlyFailed}
          onChange={(e) => setOnlyFailed(e.target.checked)}
        />
        仅看失败
      </label>
      <Button type="submit" size="sm">
        过滤
      </Button>
      <Button type="button" variant="ghost" size="sm" onClick={reset}>
        重置
      </Button>
    </form>
  );
}

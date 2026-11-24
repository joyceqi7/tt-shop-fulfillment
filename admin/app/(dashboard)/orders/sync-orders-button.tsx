"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { SyncOrdersResponse } from "@/lib/types";

export function SyncOrdersButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const resp = await api<SyncOrdersResponse>("/orders/sync", {
        method: "POST",
      });
      setMessage(`已开始同步订单，任务 ${resp.job_id}。稍后刷新订单列表查看结果。`);
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "同步订单失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" variant="outline" onClick={sync} loading={busy}>
        {!busy ? <RefreshCw /> : null}
        同步订单
      </Button>
      {message ? (
        <p className="max-w-sm text-right text-xs text-success">{message}</p>
      ) : null}
      {error ? (
        <p className="max-w-sm text-right text-xs text-destructive">{error}</p>
      ) : null}
    </div>
  );
}

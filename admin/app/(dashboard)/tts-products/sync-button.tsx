"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { TTSProductSyncResponse } from "@/lib/types";

export function SyncTTSProductsButton() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sync() {
    setBusy(true);
    setMessage(null);
    setError(null);
    try {
      const resp = await api<TTSProductSyncResponse>("/tts-products/sync", {
        method: "POST",
        body: {
          page_size: 50,
          max_pages_per_shop: 20,
        },
      });
      const errors = resp.shop_results.filter((r) => r.error);
      setMessage(
        `同步完成：${resp.shops_synced}/${resp.shops_total} 个店，${resp.products_seen} 个商品，${resp.skus_seen} 个 SKU${
          errors.length > 0 ? `，${errors.length} 个店有错误` : ""
        }`,
      );
      router.refresh();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "同步失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col items-end gap-1">
      <Button size="sm" onClick={sync} loading={busy}>
        {!busy ? <RefreshCw /> : null}
        同步 TT 商品
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

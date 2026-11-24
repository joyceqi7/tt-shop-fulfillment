"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Ban, Unlock } from "lucide-react";

import { Button } from "@/components/ui/button";
import { api, ApiError } from "@/lib/api";
import type { CodeRow } from "@/lib/types";

export function CodeRowActions({ code }: { code: CodeRow }) {
  const router = useRouter();
  const [busy, setBusy] = useState<"void" | "unlock" | null>(null);

  const act = async (op: "void" | "unlock") => {
    if (busy) return;
    if (op === "void" && !confirm(`确定作废 code #${code.id}？此操作不可逆。`)) {
      return;
    }
    if (op === "unlock" && !confirm(`确定把 code #${code.id} 退回 free？`)) {
      return;
    }
    setBusy(op);
    try {
      await api(`/inventory/codes/${code.id}/${op}`, { method: "POST" });
      router.refresh();
    } catch (e) {
      alert(e instanceof ApiError ? e.detail : "操作失败");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex justify-end gap-1">
      {code.status === "locked" ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => act("unlock")}
          loading={busy === "unlock"}
        >
          <Unlock />
          解锁
        </Button>
      ) : null}
      {code.status !== "used" && code.status !== "void" ? (
        <Button
          size="sm"
          variant="outline"
          onClick={() => act("void")}
          loading={busy === "void"}
        >
          <Ban />
          作废
        </Button>
      ) : null}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { BookOpen, Pencil, RefreshCw, Wallet } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api, ApiError } from "@/lib/api";
import type { SupplierRow } from "@/lib/types";

import {
  SupplierForm,
  buildSupplierPayload,
  useSupplierForm,
} from "./supplier-form";

interface ApiAccount {
  wallet_id: string;
  currency: string;
  balance: number;
  effective: boolean;
}

export function SupplierRowActions({ supplier }: { supplier: SupplierRow }) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAccountModal, setShowAccountModal] = useState(false);
  const [accountLoading, setAccountLoading] = useState(false);
  const [accountInfo, setAccountInfo] = useState<ApiAccount | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);

  const isApi = supplier.supplier_type === "api" || supplier.is_wgcards;

  const { state, update } = useSupplierForm({
    name: supplier.name,
    contact_person: supplier.contact_person ?? "",
    contact_phone: supplier.contact_phone ?? "",
    default_payment_terms_days: String(supplier.default_payment_terms_days),
    note: supplier.note ?? "",
    enabled: supplier.enabled,
    supplier_type: isApi ? "api" : "manual",
    api_host: supplier.api_host ?? "",
    api_app_id: supplier.api_app_id ?? "",
    api_account_id: supplier.api_account_id ?? "",
    api_app_key: "",
    api_currency: supplier.api_currency ?? "CNY",
  });

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const payload = buildSupplierPayload(state);
      await api(`/suppliers/${supplier.id}`, {
        method: "PATCH",
        body: payload,
      });
      setOpen(false);
      router.refresh();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "保存失败",
      );
    } finally {
      setBusy(false);
    }
  };

  const fetchAccount = async () => {
    setAccountLoading(true);
    setAccountError(null);
    setAccountInfo(null);
    try {
      const data = await api<ApiAccount>(
        `/suppliers/${supplier.id}/wgcards/account`,
      );
      setAccountInfo(data);
    } catch (e) {
      setAccountError(
        e instanceof ApiError
          ? e.detail
          : e instanceof Error
            ? e.message
            : "获取失败",
      );
    } finally {
      setAccountLoading(false);
    }
  };

  return (
    <>
      <div className="flex items-center gap-1">
        {isApi ? (
          <>
            <Button size="sm" variant="ghost" asChild title="商品目录">
              <Link href={`/suppliers/${supplier.id}/catalog`}>
                <BookOpen className="size-4" />
              </Link>
            </Button>
            <Button
              size="sm"
              variant="ghost"
              title="API 账户余额"
              onClick={() => {
                setShowAccountModal(true);
                fetchAccount();
              }}
            >
              <Wallet className="size-4" />
            </Button>
          </>
        ) : null}
        <Button size="sm" variant="outline" onClick={() => setOpen(true)}>
          <Pencil />
          编辑
        </Button>
      </div>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>编辑供货商：{supplier.name}</DialogTitle>
          </DialogHeader>
          <SupplierForm state={state} update={update} showEnabledToggle />
          {error ? <p className="text-xs text-destructive">{error}</p> : null}
          <DialogFooter>
            <DialogClose asChild>
              <Button variant="ghost" size="sm" disabled={busy}>
                取消
              </Button>
            </DialogClose>
            <Button size="sm" onClick={save} loading={busy}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={showAccountModal} onOpenChange={setShowAccountModal}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>API 账户余额</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            {accountLoading ? (
              <div className="flex items-center justify-center py-8">
                <RefreshCw className="size-6 animate-spin" />
              </div>
            ) : accountError ? (
              <div className="rounded-lg border border-destructive p-4">
                <p className="text-sm text-destructive">{accountError}</p>
              </div>
            ) : accountInfo ? (
              <div className="space-y-3">
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <span className="text-muted-foreground">钱包ID</span>
                  <span className="font-mono">{accountInfo.wallet_id}</span>
                  <span className="text-muted-foreground">币种</span>
                  <span>{accountInfo.currency}</span>
                  <span className="text-muted-foreground">状态</span>
                  <Badge
                    variant={accountInfo.effective ? "success" : "destructive"}
                  >
                    {accountInfo.effective ? "有效" : "无效"}
                  </Badge>
                </div>
                <div className="rounded-lg bg-muted p-4">
                  <p className="text-sm text-muted-foreground">余额</p>
                  <p className="font-mono text-3xl font-bold">
                    {accountInfo.balance.toLocaleString("en-US", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 2,
                    })}{" "}
                    <span className="text-lg">{accountInfo.currency}</span>
                  </p>
                </div>
              </div>
            ) : null}
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              size="sm"
              onClick={fetchAccount}
              disabled={accountLoading}
            >
              <RefreshCw
                className={`mr-1 size-4 ${accountLoading ? "animate-spin" : ""}`}
              />
              刷新
            </Button>
            <DialogClose asChild>
              <Button variant="ghost" size="sm">
                关闭
              </Button>
            </DialogClose>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

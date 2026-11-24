"use client";

import { useState } from "react";

import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { cn } from "@/lib/utils";

export type SupplierType = "manual" | "api";

export interface SupplierFormState {
  name: string;
  contact_person: string;
  contact_phone: string;
  default_payment_terms_days: string;
  note: string;
  enabled: boolean;
  supplier_type: SupplierType;
  api_host: string;
  api_app_id: string;
  api_account_id: string;
  api_app_key: string;
  api_currency: string;
}

export const emptySupplier: SupplierFormState = {
  name: "",
  contact_person: "",
  contact_phone: "",
  default_payment_terms_days: "30",
  note: "",
  enabled: true,
  supplier_type: "manual",
  api_host: "",
  api_app_id: "",
  api_account_id: "",
  api_app_key: "",
  api_currency: "CNY",
};

export function useSupplierForm(init: SupplierFormState = emptySupplier) {
  const [state, setState] = useState<SupplierFormState>(init);
  const update = <K extends keyof SupplierFormState>(
    k: K,
    v: SupplierFormState[K],
  ) => setState((s) => ({ ...s, [k]: v }));
  return { state, update, setState };
}

export function SupplierForm({
  state,
  update,
  showEnabledToggle,
}: {
  state: SupplierFormState;
  update: <K extends keyof SupplierFormState>(
    k: K,
    v: SupplierFormState[K],
  ) => void;
  showEnabledToggle?: boolean;
}) {
  return (
    <div className="space-y-3">
      <Field label="名称">
        <Input
          value={state.name}
          onChange={(e) => update("name", e.target.value)}
          placeholder="供货商公司名称（唯一）"
        />
      </Field>
      <div className="grid grid-cols-2 gap-3">
        <Field label="联系人">
          <Input
            value={state.contact_person}
            onChange={(e) => update("contact_person", e.target.value)}
          />
        </Field>
        <Field label="电话">
          <Input
            value={state.contact_phone}
            onChange={(e) => update("contact_phone", e.target.value)}
            className="font-mono"
          />
        </Field>
      </div>
      <Field label="默认账期（天）">
        <Input
          type="number"
          min={0}
          max={365}
          value={state.default_payment_terms_days}
          onChange={(e) =>
            update("default_payment_terms_days", e.target.value)
          }
          className="font-mono"
        />
      </Field>
      <Field label="备注">
        <Textarea
          rows={3}
          value={state.note}
          onChange={(e) => update("note", e.target.value)}
          className="font-sans"
        />
      </Field>
      {showEnabledToggle ? (
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={state.enabled}
            onChange={(e) => update("enabled", e.target.checked)}
          />
          enabled（关闭前请确认无未付 PO）
        </label>
      ) : null}

      <div className="space-y-3 border-t pt-4">
        <Field label="供应商类型">
          <div className="grid grid-cols-2 gap-2">
            <ModeOption
              active={state.supplier_type === "manual"}
              title="手动采购"
              description="线下买卡、录采购单入库"
              onClick={() => update("supplier_type", "manual")}
            />
            <ModeOption
              active={state.supplier_type === "api"}
              title="API 供应商"
              description="批量入库或来单自动采购"
              onClick={() => update("supplier_type", "api")}
            />
          </div>
        </Field>

        {state.supplier_type === "api" ? (
          <>
            <h3 className="text-sm font-medium">API 凭证</h3>
            <Field label="API Host">
              <Input
                value={state.api_host}
                onChange={(e) => update("api_host", e.target.value)}
                placeholder="按 WGCards 正式文档填写 API Host"
              />
            </Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="App ID">
                <Input
                  value={state.api_app_id}
                  onChange={(e) => update("api_app_id", e.target.value)}
                />
              </Field>
              <Field label="Account ID">
                <Input
                  value={state.api_account_id}
                  onChange={(e) => update("api_account_id", e.target.value)}
                />
              </Field>
            </div>
            <Field label="App Key">
              <Input
                type="password"
                value={state.api_app_key}
                onChange={(e) => update("api_app_key", e.target.value)}
                placeholder="编辑时留空表示不修改"
              />
            </Field>
            <Field label="默认币种">
              <select
                value={state.api_currency}
                onChange={(e) => update("api_currency", e.target.value)}
                className="flex h-9 w-full rounded-md border border-input bg-transparent px-3 py-1 text-sm shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <option value="CNY">CNY</option>
                <option value="USD">USD</option>
                <option value="JPY">JPY</option>
                <option value="EUR">EUR</option>
              </select>
              <p className="text-[11px] text-muted-foreground">
                须与 API 钱包币种一致（查余额可确认），下单扣款用此币种。
              </p>
            </Field>
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            手动供货商无需 API 配置，创建后通过「采购单」录入卡密入库。
          </p>
        )}
      </div>
    </div>
  );
}

function ModeOption({
  active,
  title,
  description,
  onClick,
}: {
  active: boolean;
  title: string;
  description: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "rounded-md border px-3 py-2 text-left transition-colors",
        active
          ? "border-primary bg-primary/5"
          : "border-border hover:border-muted-foreground/40",
      )}
    >
      <p className="text-sm font-medium">{title}</p>
      <p className="text-[11px] text-muted-foreground">{description}</p>
    </button>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-1">
      <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

export function buildSupplierPayload(state: SupplierFormState) {
  const days = Number(state.default_payment_terms_days);
  if (!state.name.trim()) throw new Error("名称必填");
  if (!Number.isFinite(days) || days < 0 || days > 365)
    throw new Error("账期天数非法");

  const base = {
    name: state.name.trim(),
    supplier_type: state.supplier_type,
    contact_person: state.contact_person.trim() || null,
    contact_phone: state.contact_phone.trim() || null,
    default_payment_terms_days: Math.round(days),
    note: state.note.trim() || null,
    enabled: state.enabled,
  };

  if (state.supplier_type === "manual") {
    return base;
  }

  const payload: Record<string, unknown> = {
    ...base,
    api_host: state.api_host.trim() || null,
    api_app_id: state.api_app_id.trim() || null,
    api_account_id: state.api_account_id.trim() || null,
    api_currency: state.api_currency.trim() || null,
  };
  const key = state.api_app_key.trim();
  if (key) payload.api_app_key = key;
  return payload;
}

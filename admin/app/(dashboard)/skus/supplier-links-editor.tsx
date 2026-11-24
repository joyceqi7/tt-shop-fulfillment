"use client";

import { useCallback, useEffect, useState } from "react";
import { Plus, RefreshCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { WGCardsItemPicker } from "@/components/wgcards/item-picker";
import { api, ApiError } from "@/lib/api";
import type { SupplierRow, SupplierSkuLinkRow } from "@/lib/types";

interface DraftLink {
  key: string;
  id?: number;
  supplier_id: string;
  external_sku_id: string;
  external_face_value: string;
  auto_fulfill: boolean;
  supplier_stock?: number | null;
  supplier_stock_error?: string | null;
}

function draftKey() {
  return `new-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
}

export function SupplierLinksEditor({
  skuMappingId,
  apiSuppliers,
}: {
  skuMappingId: number;
  apiSuppliers: SupplierRow[];
}) {
  const [links, setLinks] = useState<DraftLink[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await api<{ items: SupplierSkuLinkRow[] }>(
        `/supplier-sku-links?sku_mapping_id=${skuMappingId}&include_stock=true`,
      );
      setLinks(
        resp.items.map((l) => ({
          key: String(l.id),
          id: l.id,
          supplier_id: String(l.supplier_id),
          external_sku_id: l.external_sku_id,
          external_face_value:
            l.external_face_value === null ? "" : String(l.external_face_value),
          auto_fulfill: l.auto_fulfill,
          supplier_stock: l.supplier_stock,
          supplier_stock_error: l.supplier_stock_error,
        })),
      );
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "加载链接失败");
    } finally {
      setLoading(false);
    }
  }, [skuMappingId]);

  useEffect(() => {
    void load();
  }, [load]);

  const addRow = () => {
    const defaultSupplier =
      apiSuppliers.length > 0 ? String(apiSuppliers[0].id) : "";
    setLinks((rows) => [
      ...rows,
      {
        key: draftKey(),
        supplier_id: defaultSupplier,
        external_sku_id: "",
        external_face_value: "",
        auto_fulfill: false,
      },
    ]);
  };

  const updateRow = (key: string, patch: Partial<DraftLink>) => {
    setLinks((rows) =>
      rows.map((r) => (r.key === key ? { ...r, ...patch } : r)),
    );
  };

  const refreshStock = async (row: DraftLink) => {
    if (!row.supplier_id || !row.external_sku_id.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const resp = await api<{ items: SupplierSkuLinkRow[] }>(
        `/supplier-sku-links?sku_mapping_id=${skuMappingId}&supplier_id=${encodeURIComponent(
          row.supplier_id,
        )}&include_stock=true`,
      );
      const matched = resp.items.find(
        (item) =>
          item.id === row.id ||
          item.external_sku_id === row.external_sku_id.trim(),
      );
      updateRow(row.key, {
        supplier_stock: matched?.supplier_stock ?? null,
        supplier_stock_error: matched?.supplier_stock_error ?? null,
      });
    } catch (e) {
      updateRow(row.key, {
        supplier_stock: null,
        supplier_stock_error: e instanceof ApiError ? e.detail : "刷新库存失败",
      });
    } finally {
      setBusy(false);
    }
  };

  const removeRow = async (row: DraftLink) => {
    if (row.id) {
      setBusy(true);
      try {
        await api(`/supplier-sku-links/${row.id}`, { method: "DELETE" });
        await load();
      } catch (e) {
        setError(e instanceof ApiError ? e.detail : "删除失败");
      } finally {
        setBusy(false);
      }
      return;
    }
    setLinks((rows) => rows.filter((r) => r.key !== row.key));
  };

  const saveAll = async () => {
    setBusy(true);
    setError(null);
    try {
      for (const row of links) {
        const faceRaw = row.external_face_value.trim();
        const face = faceRaw ? Number(faceRaw) : null;
        if (face !== null && (!Number.isFinite(face) || face < 0)) {
          throw new Error("外部面值非法");
        }
        if (!row.supplier_id) throw new Error("请选择 API 供应商");
        if (!row.external_sku_id.trim()) throw new Error("外部 SKU ID 必填");

        const body = {
          external_sku_id: row.external_sku_id.trim(),
          external_face_value: face,
          auto_fulfill: row.auto_fulfill,
        };

        if (row.id) {
          await api(`/supplier-sku-links/${row.id}`, {
            method: "PATCH",
            body,
          });
        } else {
          await api("/supplier-sku-links", {
            method: "POST",
            body: {
              sku_mapping_id: skuMappingId,
              supplier_id: Number(row.supplier_id),
              ...body,
            },
          });
        }
      }
      await load();
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

  if (apiSuppliers.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        暂无 API 类型供货商。请先在「供货商」页创建 API 供应商并填写凭证。
      </p>
    );
  }

  return (
    <div className="col-span-full space-y-3 border-t pt-4">
      <div>
        <h3 className="text-sm font-medium">API 采购链接（可选）</h3>
        <p className="text-xs text-muted-foreground">
          批量入库或来单自动采购前，必须先绑定外部 SKU。来单实时不会增加可售库存。
        </p>
      </div>

      {loading ? (
        <p className="text-xs text-muted-foreground">加载中…</p>
      ) : (
        <div className="space-y-2">
          {links.length === 0 ? (
            <p className="text-xs text-muted-foreground">尚未配置 API 链接</p>
          ) : (
            links.map((row) => (
              <div
                key={row.key}
                className="grid grid-cols-1 gap-2 rounded-md border p-3 md:grid-cols-12 md:items-end"
              >
                <Field label="API 供应商" className="md:col-span-2">
                  <Select
                    value={row.supplier_id}
                    onChange={(e) =>
                      updateRow(row.key, { supplier_id: e.target.value })
                    }
                    disabled={Boolean(row.id)}
                  >
                    <option value="">选择…</option>
                    {apiSuppliers.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label="外部 SKU ID" className="md:col-span-3">
                  <div className="flex gap-2">
                    <Input
                      value={row.external_sku_id}
                      onChange={(e) =>
                        updateRow(row.key, { external_sku_id: e.target.value })
                      }
                      className="font-mono"
                    />
                    {row.supplier_id ? (
                      <WGCardsItemPicker
                        supplierId={Number(row.supplier_id)}
                        supplierName={
                          apiSuppliers.find(
                            (s) => String(s.id) === row.supplier_id,
                          )?.name ?? "API"
                        }
                        currencyCode={
                          apiSuppliers.find(
                            (s) => String(s.id) === row.supplier_id,
                          )?.api_currency ?? "CNY"
                        }
                        onSelect={(sel) =>
                          updateRow(row.key, {
                            external_sku_id: sel.skuId,
                            external_face_value:
                              sel.faceValue === null
                                ? row.external_face_value
                                : String(sel.faceValue),
                          })
                        }
                      />
                    ) : null}
                  </div>
                </Field>
                <Field label="外部面值（可选）" className="md:col-span-2">
                  <Input
                    type="number"
                    min={0}
                    step="0.01"
                    value={row.external_face_value}
                    onChange={(e) =>
                      updateRow(row.key, {
                        external_face_value: e.target.value,
                      })
                    }
                    className="font-mono"
                    placeholder="留空用内部面额"
                  />
                </Field>
                <Field label="供应商库存" className="md:col-span-2">
                  <div className="flex h-9 items-center gap-2">
                    <span className="min-w-0 rounded-md border bg-muted px-2 py-1 font-mono text-xs">
                      {row.supplier_stock_error
                        ? "查询失败"
                        : row.supplier_stock === -1
                          ? "不限"
                          : row.supplier_stock ?? "—"}
                    </span>
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      onClick={() => void refreshStock(row)}
                      disabled={busy || !row.id}
                      title="刷新供应商实时库存"
                    >
                      <RefreshCw className="size-4" />
                    </Button>
                  </div>
                  {row.supplier_stock_error ? (
                    <p className="text-[10px] text-destructive">
                      {row.supplier_stock_error}
                    </p>
                  ) : null}
                </Field>
                <Field label="来单自动" className="md:col-span-2">
                  <label className="flex h-9 items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={row.auto_fulfill}
                      onChange={(e) =>
                        updateRow(row.key, { auto_fulfill: e.target.checked })
                      }
                    />
                    库存不足时自动采购
                  </label>
                </Field>
                <div className="md:col-span-1">
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => void removeRow(row)}
                    disabled={busy}
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              </div>
            ))
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" onClick={addRow}>
          <Plus className="size-4" />
          添加链接
        </Button>
        <Button type="button" size="sm" onClick={saveAll} loading={busy}>
          保存链接
        </Button>
      </div>
      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={`space-y-1 ${className ?? ""}`}>
      <label className="block text-[10px] uppercase tracking-wider text-muted-foreground">
        {label}
      </label>
      {children}
    </div>
  );
}

import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Tailwind 类名合并工具：clsx 处理条件、tailwind-merge 处理冲突。
 * 用法：cn("p-2", isActive && "bg-primary", className)
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

/** 千分位整数，null/undefined 显示 "—" */
export function formatNumber(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  return new Intl.NumberFormat("en-US").format(n);
}

/**
 * 美分（int）→ 货币字符串。后端所有金额字段都是 cents 整数。
 * formatCents(12345, "USD") => "$123.45"
 */
export function formatCents(
  cents: number | null | undefined,
  currency: string = "USD",
): string {
  if (cents === null || cents === undefined) return "—";
  const amount = cents / 100;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

/**
 * ISO 字符串 / unix 秒 → 本地时区 "YYYY-MM-DD HH:mm"。
 * 空值返回 "—"。
 */
export function formatDateTime(
  v: string | number | Date | null | undefined,
): string {
  if (v === null || v === undefined || v === "") return "—";
  let d: Date;
  if (v instanceof Date) {
    d = v;
  } else if (typeof v === "number") {
    d = new Date(v * 1000);
  } else {
    d = new Date(v);
  }
  if (Number.isNaN(d.getTime())) return "—";
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd} ${hh}:${mi}`;
}

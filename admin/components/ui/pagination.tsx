"use client";

import { useRouter, usePathname, useSearchParams } from "next/navigation";

import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/utils";

interface PaginationProps {
  total: number;
  limit: number;
  offset: number;
}

/**
 * 直接通过 URL 参数 ?offset= 翻页，server component 可直接读取 searchParams。
 * 不缓存翻页状态：刷新即重置（管理员场景下没人在乎"上次看到第几页"）。
 */
export function Pagination({ total, limit, offset }: PaginationProps) {
  const router = useRouter();
  const pathname = usePathname();
  const sp = useSearchParams();

  const page = Math.floor(offset / limit) + 1;
  const lastPage = Math.max(1, Math.ceil(total / limit));

  const goto = (newOffset: number) => {
    const next = new URLSearchParams(sp.toString());
    if (newOffset <= 0) {
      next.delete("offset");
    } else {
      next.set("offset", String(newOffset));
    }
    router.push(`${pathname}?${next.toString()}`);
  };

  return (
    <div className="flex items-center justify-between text-xs text-muted-foreground">
      <span>
        共 <span className="text-foreground tabular-nums">{formatNumber(total)}</span> 条 · 第{" "}
        <span className="text-foreground tabular-nums">{page}</span> /{" "}
        <span className="tabular-nums">{lastPage}</span> 页
      </span>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={offset <= 0}
          onClick={() => goto(Math.max(0, offset - limit))}
        >
          上一页
        </Button>
        <Button
          variant="outline"
          size="sm"
          disabled={offset + limit >= total}
          onClick={() => goto(offset + limit)}
        >
          下一页
        </Button>
      </div>
    </div>
  );
}

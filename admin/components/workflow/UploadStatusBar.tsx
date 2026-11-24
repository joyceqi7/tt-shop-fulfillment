"use client";

import Link from "next/link";
import { useSyncExternalStore } from "react";

import { workflowUploadStore } from "@/lib/workflow-upload-store";

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

export function UploadStatusBar() {
  const upload = useSyncExternalStore(
    workflowUploadStore.subscribe,
    workflowUploadStore.getSnapshot,
    workflowUploadStore.getSnapshot,
  );

  if (upload.status === "idle" || upload.files.length === 0) {
    return null;
  }

  const total = upload.files.reduce((sum, f) => sum + f.size, 0);
  const label =
    upload.status === "uploading"
      ? `素材上传中 ${upload.progress}%`
      : upload.status === "done"
        ? "素材上传完成，已进入处理队列"
        : `素材上传失败：${upload.error || "未知错误"}`;

  return (
    <div className="border-b border-border bg-card/95 px-4 py-2 text-xs">
      <div className="flex items-center gap-3">
        <Link href="/content/ingest" className="font-medium text-foreground hover:underline">
          {label}
        </Link>
        <span className="text-muted-foreground">
          {upload.files.length} 个文件 · {formatBytes(total)}
        </span>
        {upload.status === "uploading" && (
          <div className="h-1.5 min-w-32 flex-1 rounded bg-muted">
            <div
              className="h-full rounded bg-primary transition-all"
              style={{ width: `${upload.progress}%` }}
            />
          </div>
        )}
        {upload.status !== "uploading" && (
          <button
            type="button"
            onClick={() => workflowUploadStore.clear()}
            className="ml-auto text-muted-foreground hover:text-foreground"
          >
            清除
          </button>
        )}
      </div>
    </div>
  );
}

type UploadStatus = "idle" | "uploading" | "done" | "error";

type UploadState = {
  files: File[];
  progress: number;
  status: UploadStatus;
  error: string | null;
  completedAt: number | null;
};

const state: UploadState = {
  files: [],
  progress: 0,
  status: "idle",
  error: null,
  completedAt: null,
};

const listeners = new Set<() => void>();
let activeXhr: XMLHttpRequest | null = null;
let currentSnapshot: UploadState = {
  ...state,
  files: state.files,
};

function refreshSnapshot() {
  currentSnapshot = {
    ...state,
    files: state.files,
  };
}

function emit() {
  refreshSnapshot();
  for (const l of listeners) l();
}

function snapshot(): UploadState {
  return currentSnapshot;
}

export const workflowUploadStore = {
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },

  getSnapshot: snapshot,

  addFiles(files: File[]) {
    if (state.status === "uploading") return;
    // 新选文件时, 把上一轮失败/完成的状态彻底清掉
    // 否则之前那次"3 个文件 150.6 MB 失败"会一直挂在状态栏上误导用户
    const startingFresh = state.status === "error" || state.status === "done";
    state.files = startingFresh ? [...files] : [...state.files, ...files];
    state.error = null;
    state.progress = 0;
    state.completedAt = null;
    state.status = state.files.length ? "idle" : state.status;
    emit();
  },

  removeFile(index: number) {
    if (state.status === "uploading") return;
    state.files = state.files.filter((_, i) => i !== index);
    if (state.files.length === 0) state.progress = 0;
    emit();
  },

  clear() {
    if (state.status === "uploading") return;
    state.files = [];
    state.progress = 0;
    state.status = "idle";
    state.error = null;
    state.completedAt = null;
    emit();
  },

  start(extraFields?: Record<string, string | number>) {
    if (state.status === "uploading" || state.files.length === 0) return;

    state.status = "uploading";
    state.progress = 0;
    state.error = null;
    state.completedAt = null;
    emit();

    const xhr = new XMLHttpRequest();
    activeXhr = xhr;
    const fd = new FormData();
    for (const f of state.files) fd.append("files", f);
    if (extraFields) {
      for (const [k, v] of Object.entries(extraFields)) fd.append(k, String(v));
    }

    xhr.open("POST", "/api/workflow/sources/upload");
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (!e.lengthComputable) return;
      state.progress = Math.round((e.loaded / e.total) * 100);
      emit();
    };
    xhr.onload = () => {
      activeXhr = null;
      if (xhr.status >= 200 && xhr.status < 300) {
        state.progress = 100;
        state.status = "done";
        state.error = null;
        state.completedAt = Date.now();
      } else {
        state.status = "error";
        state.error = formatHttpError(xhr);
      }
      emit();
    };
    xhr.onerror = () => {
      activeXhr = null;
      state.status = "error";
      // 区分"网络/CORS"和"500 但 readyState=4": 后者会走 onload, 不会进这里
      state.error = "网络中断 (浏览器或 Caddy 在传输中关闭了连接)";
      emit();
    };
    xhr.ontimeout = () => {
      activeXhr = null;
      state.status = "error";
      state.error = "上传超时";
      emit();
    };
    xhr.send(fd);
  },

  abort() {
    activeXhr?.abort();
    activeXhr = null;
    state.status = "idle";
    state.progress = 0;
    emit();
  },
};

/**
 * 后端 (workflow-api) 出错时会回 JSON 包: { error, detail, trace_tail? }
 * FastAPI 默认 HTTPException 是 { detail: "..." } —— 两种都要解。
 */
function formatHttpError(xhr: XMLHttpRequest): string {
  const head = `HTTP ${xhr.status}${xhr.statusText ? " " + xhr.statusText : ""}`;
  const raw = xhr.responseText || "";
  if (!raw) return head;
  try {
    const j = JSON.parse(raw);
    const detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    const extra = j.trace_tail ? `\n${j.trace_tail.join("\n")}` : "";
    return `${head} · ${j.error || ""}${detail ? " " + detail : ""}${extra}`.slice(0, 800);
  } catch {
    return `${head} · ${raw.slice(0, 500)}`;
  }
}

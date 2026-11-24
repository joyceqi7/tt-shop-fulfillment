// Video workflow API client. Browser requests stay same-origin through Next rewrites:
// /api/workflow/* -> workflow-api, /workflow-files/* -> workflow-api/files/*

export type Source = {
  id: number;
  url: string;
  platform?: string;
  game?: string | null;
  original_title?: string;
  author?: string;
  duration?: number;
  thumbnail_url?: string | null;
  processed_url?: string | null;
  status: string;
  error?: string | null;
  job_id?: number | null;
  job_status?: string | null;
  job_progress?: number | null;
  job_stage?: string | null;
  job_error?: string | null;
  job_log?: string | null;
  job_started_at?: string | null;
  job_finished_at?: string | null;
  created_at: string;
};

export type Clip = {
  id: number;
  source_id: number;
  game?: string | null;
  start: number;
  end: number;
  duration: number;
  file_url?: string | null;
  thumbnail_url?: string | null;
  transcript?: string;
  description?: string;
  tags?: string[];
  content_type?: string;
  mood?: string;
  hook_score?: number;
  has_real_person?: boolean;
  real_person_note?: string | null;
  used_count?: number;
  created_at: string;
};

export type Game = {
  name: string;
  source_count: number;
};

export type Job = {
  id: number;
  kind: string;
  target_id?: number;
  status: "queued" | "running" | "done" | "failed" | string;
  progress: number;
  stage?: string;
  log?: string;
  error?: string;
  created_at: string;
  started_at?: string;
  finished_at?: string;
};

export type Scene = {
  clip_id: number;
  duration: number;
  voiceover: string;
  caption: string;
  fx_hint?: string;
  callout?: string;
};

export type BgmStyle = {
  style: string;
  count: number;
  files: string[];
};

export type BgmFile = {
  style: string;
  filename: string;
  url: string;
  category?: string;
  bpm?: number | null;
  duration?: number | null;
  beat_count?: number;
  analysis_status?: string;
  mood_tags?: string[];
};

export type BgmInfo = {
  default_style: string;
  bgm_dir: string;
  max_upload_mb?: number;
  categories?: string[];
  styles: BgmStyle[];
  files?: BgmFile[];
};

export type FontOption = {
  key: string;
  label: string;
  fontname: string;
  available: boolean;
  file?: string | null;
};

export type FontInfo = {
  fonts: FontOption[];
};

export type Script = {
  title?: string;
  total_duration?: number;
  scenes: Scene[];
};

export type Generation = {
  id: number;
  game?: string | null;
  product_name?: string;
  product_info?: string;
  target_duration?: number;
  style?: string;
  voice?: string;
  bgm_style?: string | null;
  bgm_file?: string | null;
  bgm_category?: string | null;
  bgm_start_mode?: string | null;
  subtitle_font?: string | null;
  subtitle_highlight_font?: string | null;
  script_json?: Script | null;
  used_clip_ids?: number[];
  output_url?: string | null;
  subtitle_url?: string | null;
  voiceover_url?: string | null;
  cover_url?: string | null;
  status: string;
  error?: string;
  created_at: string;
  finished_at?: string;
};

const API_BASE = "/api/workflow";

export function workflowFileUrl(url?: string | null): string | undefined {
  if (!url) return undefined;
  if (url.startsWith("http://") || url.startsWith("https://")) return url;
  if (url.startsWith("/files/")) return url.replace(/^\/files\//, "/workflow-files/");
  return url;
}

function mapFileUrls<T>(value: T): T {
  if (Array.isArray(value)) return value.map(mapFileUrls) as T;
  if (!value || typeof value !== "object") return value;
  const out: Record<string, unknown> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    out[key] = key.endsWith("_url") && typeof raw === "string" ? workflowFileUrl(raw) : mapFileUrls(raw);
  }
  return out as T;
}

export const fetcher = async <T = unknown>(url: string): Promise<T> => {
  const r = await fetch(API_BASE + url, { credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return mapFileUrls((await r.json()) as T);
};

export async function post<T = unknown>(path: string, body: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return mapFileUrls((await r.json()) as T);
}

export async function del(path: string): Promise<unknown> {
  const r = await fetch(API_BASE + path, { method: "DELETE", credentials: "include" });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

export async function patch<T = unknown>(path: string, body: unknown): Promise<T> {
  const r = await fetch(API_BASE + path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    credentials: "include",
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return mapFileUrls((await r.json()) as T);
}

export type UploadOptions = {
  /** form field name, default "files" */
  fieldName?: string;
  /** 额外的表单字段 */
  extraFields?: Record<string, string>;
  /** 上传进度回调, 参数是 0-100 的整数百分比 */
  onProgress?: (percent: number) => void;
};

export function uploadFiles<T = unknown>(
  path: string,
  files: FileList | File[],
  opts: UploadOptions = {},
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const fd = new FormData();
    const field = opts.fieldName ?? "files";
    const list = Array.from(files);
    for (const f of list) fd.append(field, f);
    if (opts.extraFields) {
      for (const [k, v] of Object.entries(opts.extraFields)) fd.append(k, v);
    }
    xhr.open("POST", API_BASE + path);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && opts.onProgress) {
        opts.onProgress(Math.round((e.loaded / e.total) * 100));
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(mapFileUrls(JSON.parse(xhr.responseText)) as T);
        } catch {
          resolve(xhr.responseText as unknown as T);
        }
      } else {
        // 后端在 main.py 里把 500 也包成 JSON, 这里尽量取 detail/error
        let msg = `${xhr.status} ${xhr.statusText || ""}`.trim();
        try {
          const j = JSON.parse(xhr.responseText);
          const detail =
            (typeof j?.detail === "string" && j.detail) ||
            (typeof j?.error === "string" && j.error) ||
            (j?.detail && JSON.stringify(j.detail)) ||
            xhr.responseText;
          if (detail) msg = `${xhr.status}: ${detail}`;
        } catch {
          if (xhr.responseText) msg += `\n${xhr.responseText}`;
        }
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.send(fd);
  });
}

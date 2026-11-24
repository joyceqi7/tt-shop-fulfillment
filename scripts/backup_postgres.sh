#!/usr/bin/env bash
# ===========================================
# 每日 Postgres 备份脚本
# - pg_dump → gzip → openssl AES-256-CBC 加密 → 上传阿里云 OSS
# - 本地 /data/backups 保留最近 7 份
# - 设计为 cron 每日 03:00 跑（UTC 时区，对应北京时间 11:00）
#
# 部署：
#   cp scripts/backup_postgres.sh /usr/local/bin/
#   chmod +x /usr/local/bin/backup_postgres.sh
#   crontab -e
#   # 加：0 3 * * * /usr/local/bin/backup_postgres.sh >> /var/log/tt-shop-backup.log 2>&1
#
# 依赖：docker（用 postgres 容器跑 pg_dump，免装客户端）+ ossutil
#   ossutil 安装：
#     wget https://gosspublic.alicdn.com/ossutil/1.7.18/ossutil64
#     chmod +x ossutil64 && mv ossutil64 /usr/local/bin/ossutil
#     ossutil config -e $OSS_ENDPOINT -i $OSS_ACCESS_KEY_ID -k $OSS_ACCESS_KEY_SECRET
# ===========================================

set -euo pipefail

# ---- 配置（从 .env.production 读，或写死在 cron 环境里）----
ENV_FILE="${ENV_FILE:-/opt/tt-shop/.env.production}"
if [[ -f "$ENV_FILE" ]]; then
    set -o allexport
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +o allexport
fi

PG_CONTAINER="${PG_CONTAINER:-tts-postgres}"
PG_USER="${POSTGRES_USER:-tts}"
PG_DB="${POSTGRES_DB:-tts}"
LOCAL_DIR="${LOCAL_DIR:-/data/backups}"
LOCAL_KEEP="${LOCAL_KEEP:-7}"
OSS_BUCKET="${OSS_BUCKET:-}"  # 可为空 → 跳过 OSS 上传（仅本地备份，强烈建议生产配上）
OSS_PREFIX="${OSS_PREFIX:-postgres-daily}"
PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE 未配置}"

# ---- 日志 ----
TS=$(date -u +%Y%m%dT%H%M%SZ)
log() { echo "[$(date -u +%FT%TZ)] $*"; }

mkdir -p "$LOCAL_DIR"

DUMP_FILE="$LOCAL_DIR/tts-$TS.sql.gz.enc"
TMP_FILE="$LOCAL_DIR/.tts-$TS.tmp"

# ---- 1. 跑 pg_dump（custom format 不行因为后面要 gzip+加密；plain 格式 + 立即 gzip 流式更省盘）----
log "begin pg_dump db=$PG_DB container=$PG_CONTAINER"
docker exec "$PG_CONTAINER" pg_dump \
    -U "$PG_USER" -d "$PG_DB" \
    --no-owner --no-privileges --clean --if-exists \
    | gzip -9 \
    | openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass pass:"$PASSPHRASE" \
    > "$TMP_FILE"

mv "$TMP_FILE" "$DUMP_FILE"
SIZE=$(du -h "$DUMP_FILE" | cut -f1)
log "dump done: $DUMP_FILE ($SIZE)"

# ---- 2. 上传 OSS（OSS_BUCKET 未配置时跳过，仅本地备份）----
if [[ -n "$OSS_BUCKET" ]]; then
    OSS_PATH="oss://$OSS_BUCKET/$OSS_PREFIX/tts-$TS.sql.gz.enc"
    log "uploading to $OSS_PATH"
    ossutil cp -f "$DUMP_FILE" "$OSS_PATH"
    log "uploaded OK"
else
    log "⚠️  OSS_BUCKET not configured, skipping upload (LOCAL-ONLY backup)"
    log "⚠️  ECS 物理挂了或 /data 被 rm 时，备份会一起丢；尽快配 OSS"
    OSS_PATH="(skipped)"
fi

# ---- 3. 清理本地（保留最近 N 份）----
log "pruning local backups (keep last $LOCAL_KEEP)"
cd "$LOCAL_DIR"
ls -1t tts-*.sql.gz.enc 2>/dev/null \
    | tail -n +$((LOCAL_KEEP + 1)) \
    | xargs --no-run-if-empty rm -v

# ---- 4. 清理 OSS（保留最近 30 份；可改成 OSS bucket lifecycle 规则更稳）----
# 简化：让 OSS bucket 自己配 lifecycle，30 天自动转归档 + 90 天删除
# 这里只列示，不在脚本里强删（避免误删）
# ossutil rm "oss://$OSS_BUCKET/$OSS_PREFIX/" -r -f --include "*.sql.gz.enc" --older-than 90

log "backup complete"

# ---- 5. 飞书通知（可选，用同一个 webhook url）----
if [[ -n "${FEISHU_WEBHOOK_URL:-}" ]]; then
    curl -fsS -X POST "$FEISHU_WEBHOOK_URL" \
        -H 'Content-Type: application/json' \
        -d "{\"msg_type\":\"text\",\"content\":{\"text\":\"[TT-Shop 备份] $TS db=$PG_DB size=$SIZE → $OSS_PATH\"}}" \
        > /dev/null || log "feishu notify failed (ignored)"
fi

#!/usr/bin/env bash
# ===========================================
# 从 OSS 拉备份恢复 Postgres
#
# !!! 危险操作 !!!
# 这个脚本会 DROP DATABASE 然后重建，**所有现有数据会丢**。
# 上线后**只用于灾难恢复**。日常用 deploy/README.md "数据回滚" 章节的方法。
#
# 用法：
#   bash scripts/restore_postgres.sh <oss_path>
#   # 例：
#   bash scripts/restore_postgres.sh oss://sailson-shop-backup/postgres-daily/tts-20260421T030000Z.sql.gz.enc
#
# 不带参数：列出 OSS 上最近 10 个备份让你选
# ===========================================

set -euo pipefail

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
OSS_BUCKET="${OSS_BUCKET:?OSS_BUCKET 未配置}"
OSS_PREFIX="${OSS_PREFIX:-postgres-daily}"
PASSPHRASE="${BACKUP_ENCRYPTION_PASSPHRASE:?BACKUP_ENCRYPTION_PASSPHRASE 未配置}"

red() { printf "\033[31m%s\033[0m\n" "$*"; }
green() { printf "\033[32m%s\033[0m\n" "$*"; }
yellow() { printf "\033[33m%s\033[0m\n" "$*"; }

# ---- 1. 选择备份 ----
OSS_PATH="${1:-}"
if [[ -z "$OSS_PATH" ]]; then
    yellow "未指定备份路径，列出最近 10 个备份："
    ossutil ls "oss://$OSS_BUCKET/$OSS_PREFIX/" \
        | grep -E '\.sql\.gz\.enc' \
        | sort -k2 -r \
        | head -n 10
    echo
    yellow "用法：bash $0 <oss_path>"
    exit 1
fi

# ---- 2. 二次确认 ----
red "即将从备份恢复："
red "  source: $OSS_PATH"
red "  target: container=$PG_CONTAINER db=$PG_DB user=$PG_USER"
red "!!! 这会先 DROP DATABASE 再重建，所有现有数据会丢 !!!"
read -p "输入 'YES' 确认继续：" CONFIRM
if [[ "$CONFIRM" != "YES" ]]; then
    yellow "已取消"
    exit 0
fi

# ---- 3. 下载到本地临时文件 ----
TMP=$(mktemp -d)
trap "rm -rf $TMP" EXIT
LOCAL_FILE="$TMP/restore.sql.gz.enc"

green "[1/4] 下载备份"
ossutil cp -f "$OSS_PATH" "$LOCAL_FILE"

# ---- 4. 解密 + 解压 → 落 plain SQL（avoid pipe 失败时压力丢一半）----
green "[2/4] 解密 + 解压"
PLAIN="$TMP/restore.sql"
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -salt -pass pass:"$PASSPHRASE" -in "$LOCAL_FILE" \
    | gunzip -c > "$PLAIN"
LINES=$(wc -l < "$PLAIN")
green "  恢复 SQL：$PLAIN ($LINES 行)"

# ---- 5. DROP + CREATE + restore ----
green "[3/4] DROP DATABASE $PG_DB + 重建"
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d postgres <<SQL
SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '$PG_DB' AND pid <> pg_backend_pid();
DROP DATABASE IF EXISTS $PG_DB;
CREATE DATABASE $PG_DB OWNER $PG_USER;
SQL

green "[4/4] 灌入 SQL（这一步可能要几分钟，大库可能十几分钟）"
docker exec -i "$PG_CONTAINER" psql -U "$PG_USER" -d "$PG_DB" < "$PLAIN"

green "===================================="
green "  恢复完成"
green "===================================="
yellow "下一步建议（按顺序）："
echo "  1. docker compose -f /opt/tt-shop/deploy/docker-compose.prod.yml restart api worker"
echo "  2. curl https://tts-api.sailson.com/health/ready"
echo "  3. 抽查一个订单数据是否完整"
echo "  4. 检查卡密水位 / token 状态：curl https://tts-api.sailson.com/metrics | grep tts_"

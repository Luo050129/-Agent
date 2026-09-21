#!/usr/bin/env bash
# 白山面试辅助 Agent · 一键启动脚本（macOS / Linux）
#
# 用法:
#   ./start.sh            # 本地演示模式（H2 + 本机 Redis，RAG 关闭）
#   ./start.sh local      # 同上
#   ./start.sh full       # 完整模式（docker compose: PostgreSQL+pgvector+RAG 开启）
#
# 依赖: JDK 21（在 PATH 或 JAVA_HOME）；local 模式需本机 Redis；full 模式需 Docker
# 环境变量: AI_API_KEY / EMBEDDING_API_KEY

set -uo pipefail
cd "$(dirname "$0")"

MODE="${1:-local}"
PORT=8080
JAR="build/libs/baishan-interview-agent.jar"

echo "==> 白山面试辅助 Agent · 启动模式: $MODE"

# 1. 校验 java / JDK 21
if ! command -v java >/dev/null 2>&1; then
  echo "[错误] 未找到 java，请安装 JDK 21 并加入 PATH 或设置 JAVA_HOME"
  exit 1
fi
echo "[java] $(java -version 2>&1 | head -1)"

# 2. 按模式准备依赖
PROFILE_ARG=""
if [ "$MODE" = "local" ]; then
  if ! redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
    echo "[提示] 本机 Redis 未运行，尝试启动（redis-server --daemonize yes）..."
    if command -v redis-server >/dev/null 2>&1; then
      redis-server --daemonize yes >/dev/null 2>&1 || true
      sleep 2
    fi
    if ! redis-cli -h 127.0.0.1 -p 6379 ping >/dev/null 2>&1; then
      echo "[警告] Redis 仍未就绪，请手动启动后重试（local 模式依赖 6379）"
    fi
  else
    echo "[Redis] 已运行"
  fi
  PROFILE_ARG="--spring.profiles.active=local"
elif [ "$MODE" = "full" ]; then
  if ! command -v docker >/dev/null 2>&1; then
    echo "[错误] 未找到 docker，完整模式需 Docker"
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "[错误] Docker 守护进程未运行，请先启动 Docker"
    exit 1
  fi
  echo "[Docker] 启动 PostgreSQL(pgvector) + Redis..."
  docker compose up -d
  echo "[等待] PostgreSQL 健康（最多 60 秒）..."
  for i in $(seq 1 30); do
    if docker compose ps postgres 2>/dev/null | grep -q healthy; then break; fi
    sleep 2
  done
  PROFILE_ARG=""
else
  echo "[错误] 未知模式: $MODE （请用 local 或 full）"
  exit 1
fi

# 3. 端口占用检查（macOS 无 lsof 时用本地探测，仅提示）
if command -v lsof >/dev/null 2>&1; then
  if lsof -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "[提示] 端口 $PORT 已被占用，应用可能已在运行，直接访问 http://localhost:$PORT"
    exit 0
  fi
fi

# 4. jar 缺失则构建（跳过测试）
if [ ! -f "$JAR" ]; then
  echo "[构建] 未找到 $JAR，执行 gradle 构建（跳过测试）..."
  ./gradlew build -x test
fi

# 5. 清理 H2 锁（仅 local）
if [ "$MODE" = "local" ] && [ -f "data/interview_local.lock.db" ]; then
  rm -f "data/interview_local.lock.db"
fi

# 6. 环境变量提醒
[ -z "${AI_API_KEY:-}" ] && echo "[警告] 未设置 AI_API_KEY，大模型调用将失败"
[ -z "${EMBEDDING_API_KEY:-}" ] && echo "[警告] 未设置 EMBEDDING_API_KEY，知识库向量化将失败"

# 7. 启动后自动打开浏览器（后台延迟 10 秒）
( sleep 10; if command -v open >/dev/null 2>&1; then open http://localhost:$PORT; elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://localhost:$PORT; fi ) >/dev/null 2>&1 &

echo ""
echo "[启动] $MODE 模式，访问 http://localhost:$PORT"
echo "[停止] Ctrl+C"
echo ""

exec java -jar "$JAR" $PROFILE_ARG --server.port=$PORT

@echo off
chcp 65001 >nul
REM ============================================================
REM  白山面试辅助 Agent · 完整模式一键启动（Windows + Docker）
REM  启动 PostgreSQL(pgvector) + Redis（docker compose），以默认 profile 运行
REM  开启知识库 RAG（pgvector 向量检索）—— local 模式无法演示该能力
REM  需配置：AI_API_KEY / EMBEDDING_API_KEY；需先启动 Docker Desktop
REM  用法：start-full.cmd
REM ============================================================
cd /d "%~dp0"
setlocal

REM ---- 1. 定位 JDK 21 ----
set "JDK21=C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot"
if not exist "%JDK21%\bin\java.exe" (
  for /f "tokens=2 delims==" %%i in ('findstr /C:"org.gradle.java.home" gradle.properties 2^>nul') do set "JDK21=%%i"
)
if exist "%JDK21%\bin\java.exe" (
  set "JAVA_HOME=%JDK21%"
  set "PATH=%JDK21%\bin;%PATH%"
) else (
  echo [错误] 未找到 JDK 21：%JDK21%
  pause
  exit /b 1
)
echo [JDK] 使用 %JAVA_HOME%

set "PORT=8080"
set "JAR=build\libs\baishan-interview-agent.jar"

REM ---- 2. Docker 检查 ----
docker info >nul 2>&1
if errorlevel 1 (
  echo [错误] 无法连接 Docker，请先启动 Docker Desktop 后重试。
  pause
  exit /b 1
)

REM ---- 3. 启动依赖（pgvector + redis）----
echo [Docker] 启动 PostgreSQL(pgvector) + Redis...
docker compose up -d
if errorlevel 1 (
  echo [错误] docker compose 启动失败，请检查 Docker 是否就绪。
  pause
  exit /b 1
)

REM ---- 4. 等待 PostgreSQL 就绪（健康检查，最多 60 秒）----
echo [等待] PostgreSQL 健康检查（最多 60 秒）...
set "READY=0"
for /l %%i in (1,1,30) do (
  docker compose ps postgres 2>nul | findstr /C:"healthy" >nul 2>&1
  if not errorlevel 1 (
    set "READY=1"
    goto :pg_ok
  )
  timeout /t 2 /nobreak >nul
)
:pg_ok
if "%READY%"=="0" (
  echo [警告] PostgreSQL 未在预期时间内健康，应用可能连接失败；仍继续尝试启动。
) else (
  echo [PostgreSQL] 已就绪
)

REM ---- 5. jar 缺失则构建 ----
if not exist "%JAR%" (
  echo [构建] 未找到 %JAR%，执行 gradle 构建（跳过测试）...
  call gradlew.bat build -x test
  if not exist "%JAR%" (
    echo [错误] 构建失败，请查看上方日志。
    pause
    exit /b 1
  )
)

REM ---- 6. 端口占用检查 ----
netstat -ano 2>nul | findstr /C:":%PORT%" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [提示] 端口 %PORT% 已被占用，应用可能已在运行。
  start http://localhost:%PORT%
  pause
  exit /b 0
)

REM ---- 7. 环境变量提醒 ----
if not defined AI_API_KEY (
  echo [警告] 未检测到 AI_API_KEY 环境变量，聊天/模拟面试将无法调用大模型。
)
if not defined EMBEDDING_API_KEY (
  echo [警告] 未检测到 EMBEDDING_API_KEY 环境变量，知识库向量化将失败。
)

REM ---- 8. 自动开浏览器 ----
start "" cmd /c "timeout /t 12 /nobreak >nul 2>nul && start http://localhost:%PORT%"

echo.
echo [启动] 完整模式（PostgreSQL + pgvector + RAG 开启），约 15~25 秒（含 Flyway 迁移）...
echo [访问] http://localhost:%PORT%
echo [停止] 本窗口 Ctrl+C 停止应用；docker compose down 停止依赖
echo.

REM ---- 9. 运行（默认 profile = PostgreSQL + pgvector + Redis，RAG 开启）----
"%JAVA_HOME%\bin\java.exe" -jar "%JAR%" --server.port=%PORT%
if errorlevel 1 (
  echo [错误] 启动失败，请查看上方日志。
  pause
)
endlocal

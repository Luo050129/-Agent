@echo off
chcp 65001 >nul
REM ============================================================
REM  白山面试辅助 Agent · 本地演示模式一键启动（Windows）
REM  自动：定位 JDK21 -> 启动本机 Redis(如未运行) -> 构建 jar(如缺失) -> 运行
REM  模式：H2(PostgreSQL 兼容) + 本机 Redis，无需 Docker / PostgreSQL
REM  需配置环境变量：AI_API_KEY / EMBEDDING_API_KEY
REM  用法：双击本文件，或命令行 start-local.cmd
REM ============================================================
cd /d "%~dp0"
setlocal

REM ---- 1. 定位 JDK 21（系统默认可能指向 Java 8，Spring Boot 4 需 17+）----
set "JDK21=C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot"
if not exist "%JDK21%\bin\java.exe" (
  for /f "tokens=2 delims==" %%i in ('findstr /C:"org.gradle.java.home" gradle.properties 2^>nul') do set "JDK21=%%i"
)
if exist "%JDK21%\bin\java.exe" (
  set "JAVA_HOME=%JDK21%"
  set "PATH=%JDK21%\bin;%PATH%"
) else (
  echo [错误] 未找到 JDK 21：%JDK21%
  echo        请安装 Microsoft Build of OpenJDK 21，或修改本脚本中的 JDK21 路径。
  pause
  exit /b 1
)
echo [JDK] 使用 %JAVA_HOME%

set "PORT=8080"
set "JAR=build\libs\baishan-interview-agent.jar"

REM ---- 2. 确保本机 Redis 已运行（local 模式依赖 6379）----
redis-cli -h 127.0.0.1 -p 6379 ping >nul 2>&1
if errorlevel 1 (
  echo [提示] 本机 Redis 未运行，尝试自动启动...
  where redis-server >nul 2>&1
  if not errorlevel 1 (
    start "" redis-server
    timeout /t 3 /nobreak >nul
    redis-cli -h 127.0.0.1 -p 6379 ping >nul 2>&1
    if errorlevel 1 ( echo [警告] Redis 仍未就绪，请手动启动后重试。 ) else ( echo [Redis] 已启动 )
  ) else (
    echo [警告] 未找到 redis-server，请手动启动 Redis（local 模式依赖 6379）。
  )
) else (
  echo [Redis] 已运行
)

REM ---- 3. 端口占用检查：已被占用 -> 视为已在运行，直接打开浏览器，避免重复实例冲突 ----
netstat -ano 2>nul | findstr /C:":%PORT%" | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
  echo [提示] 端口 %PORT% 已被占用，应用可能已在运行。
  echo        直接访问 http://localhost:%PORT% 即可；如需重启请先关闭旧实例。
  start http://localhost:%PORT%
  pause
  exit /b 0
)

REM ---- 4. 清理可能残留的 H2 文件锁（无进程占用时删除是安全的）----
if exist "data\interview_local.lock.db" (
  del /Q "data\interview_local.lock.db" >nul 2>&1
)

REM ---- 5. 环境变量提醒（缺失不影响启动，但调用大模型会失败）----
if not defined AI_API_KEY (
  echo [警告] 未检测到 AI_API_KEY 环境变量，聊天/模拟面试将无法调用大模型。
)
if not defined EMBEDDING_API_KEY (
  echo [警告] 未检测到 EMBEDDING_API_KEY 环境变量，知识库检索的向量化将失败。
)

REM ---- 6. jar 缺失则自动构建（跳过测试，首次较慢）----
if not exist "%JAR%" (
  echo [构建] 未找到 %JAR%，执行 gradle 构建（跳过测试）...
  call gradlew.bat build -x test
  if not exist "%JAR%" (
    echo [错误] 构建失败，请查看上方日志。
    pause
    exit /b 1
  )
)

REM ---- 7. 启动后自动打开浏览器（后台计时 10 秒）----
start "" cmd /c "timeout /t 10 /nobreak >nul 2>nul && start http://localhost:%PORT%"

echo.
echo [启动] 正在启动（local 模式：H2 + 本机 Redis），约 8~15 秒...
echo [访问] http://localhost:%PORT%
echo [停止] 在本窗口按 Ctrl+C
echo.

REM ---- 8. 前台运行（日志直接显示在本窗口；Ctrl+C 即停止）----
"%JAVA_HOME%\bin\java.exe" -jar "%JAR%" --spring.profiles.active=local --server.port=%PORT%
if errorlevel 1 (
  echo.
  echo [错误] 启动失败，请查看上方日志。常见原因：
  echo        1) 端口 %PORT% 仍被占用（关闭旧实例后重试）
  echo        2) H2 锁文件冲突（删除 data\interview_local.lock.db 后重试）
  echo        3) 本机 Redis 未启动（local 模式依赖 6379）
  echo        4) 源码有改动但 jar 过旧（先执行 gradlew.bat build 重新打包）
  pause
)
endlocal

@echo off
chcp 65001 >nul
REM ============================================================
REM  白山面试辅助 Agent · 本地演示模式一键启动
REM  无需 Docker/PostgreSQL：H2(兼容模式) + 本机 Redis
REM  优先用预编译 jar（秒级启动，不重编译、不留 Gradle 守护）
REM  jar 缺失时自动回退 gradle 构建
REM  需要本机已配置环境变量：AI_API_KEY / EMBEDDING_API_KEY
REM ============================================================
cd /d %~dp0
setlocal

REM ---- 1. 显式指定 JDK 21（系统默认可能指向 Java 8，Boot 4 需 17+）----
set "JDK21=C:\Program Files\Microsoft\jdk-21.0.11.10-hotspot"
if exist "%JDK21%\bin\java.exe" (
  set "JAVA_HOME=%JDK21%"
  set "PATH=%JDK21%\bin;%PATH%"
) else (
  echo [错误] 未找到 JDK 21：%JDK21%
  echo        请安装 Microsoft Build of OpenJDK 21，或修改本脚本中的 JDK21 路径。
  pause
  exit /b 1
)

set SPRING_PROFILES_ACTIVE=local
set PORT=8080
set "JAR=build\libs\baishan-interview-agent.jar"

REM ---- 2. 端口占用检查：已被占用 -> 视为已在运行，直接打开浏览器，避免重复实例冲突 ----
netstat -ano 2>nul | findstr /C:":%PORT%" | findstr /C:"LISTENING" >nul 2>nul
if not errorlevel 1 (
  echo [提示] 端口 %PORT% 已被占用，应用可能已在运行。
  echo        直接访问 http://localhost:%PORT% 即可；如需重启请先关闭旧实例。
  start http://localhost:%PORT%
  pause
  exit /b 0
)

REM ---- 3. 清理可能残留的 H2 文件锁（无进程占用时删除是安全的）----
if exist "data\interview_local.lock.db" (
  del /Q "data\interview_local.lock.db" >nul 2>nul
)

REM ---- 4. 环境变量提醒（缺失不影响启动，但调用大模型会失败）----
if not defined AI_API_KEY (
  echo [警告] 未检测到 AI_API_KEY 环境变量，聊天/模拟面试将无法调用大模型。
)
if not defined EMBEDDING_API_KEY (
  echo [警告] 未检测到 EMBEDDING_API_KEY 环境变量，知识库检索的向量化将失败。
)

REM ---- 5. 选择运行方式 ----
if exist "%JAR%" (
  echo [模式] 预编译 jar 启动（推荐，秒级）
) else (
  echo [模式] 未找到 %JAR%，改用 gradle 构建后启动（首次较慢）...
)

REM ---- 6. 启动后自动打开浏览器（后台计时 10 秒）----
start "" cmd /c "timeout /t 10 /nobreak >nul 2>nul && start http://localhost:%PORT%"

echo.
echo [启动] 正在启动（local 模式：H2 + 本机 Redis），约 8~15 秒...
echo [访问] http://localhost:%PORT%
echo [停止] 在本窗口按 Ctrl+C
echo.

REM ---- 7. 前台运行（日志直接显示在本窗口；Ctrl+C 即停止）----
if exist "%JAR%" (
  "%JAVA_HOME%\bin\java.exe" -jar "%JAR%" --server.port=%PORT%
) else (
  call gradlew.bat bootRun --args="--server.port=%PORT%"
)
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

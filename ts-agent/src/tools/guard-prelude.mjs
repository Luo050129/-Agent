/**
 * run_node 子进程守卫：在用户代码之前执行，按白名单拦截文件与网络操作。
 *
 * 为什么是 patch 而不是内核级隔离：Node 没有 Windows Job Object 那种东西，
 * AppContainer / restricted token 都要走 FFI 原生绑定（等于引入编译期依赖）。
 * 这里做到的是「常规代码绕不过去」，并在 tools/sandbox.ts 里配静态预检，
 * 挡掉 `import { readFileSync } from "node:fs"` 这类拿命名快照的写法。
 *
 * 约定：本文件所有顶层标识符都带 __xu 前缀 —— 它和用户代码在同一模块作用域，
 * 不加前缀会撞名（`const fs` vs 用户的 `import fs`）。
 *
 * 白名单根目录从环境变量读：XU_SANDBOX_READ_ROOTS / XU_SANDBOX_WRITE_ROOTS
 * （path.delimiter 分隔）。XU_SANDBOX_NET=1 才放行网络。
 */

import { createRequire as __xuCreateRequire } from "node:module";

const __xuRequire = __xuCreateRequire(import.meta.url);
const __xuFs = __xuRequire("node:fs");
const __xuPath = __xuRequire("node:path");
const __xuOs = __xuRequire("node:os");

const __xuRead = (process.env.XU_SANDBOX_READ_ROOTS || "").split(__xuPath.delimiter).filter(Boolean);
const __xuWrite = (process.env.XU_SANDBOX_WRITE_ROOTS || "").split(__xuPath.delimiter).filter(Boolean);
// Node 自身安装目录与系统临时目录天然放行：否则连模块解析都要被自己拦住
__xuRead.push(__xuPath.dirname(process.execPath), __xuOs.tmpdir());
__xuWrite.push(__xuOs.tmpdir());

const __xuOnWin = process.platform === "win32";
const __xuDrive = /^[A-Za-z]:[\\/]|^\\\\|^\/\//;

function __xuNorm(target) {
  const resolved = __xuPath.resolve(String(target));
  return __xuOnWin ? resolved.toLowerCase() : resolved;
}

function __xuInside(target, roots) {
  // 非 Windows 没有盘符语义：C:/Windows/x 会被 resolve 成 cwd 下的相对路径
  if (!__xuOnWin && __xuDrive.test(String(target))) return false;
  const t = __xuNorm(target);
  return roots.some((root) => {
    const r = __xuNorm(root);
    return t === r || t.startsWith(r + __xuPath.sep);
  });
}

function __xuDeny(action, target) {
  const err = new Error(`[SANDBOX] ${action}被沙箱拦截：${target}`);
  err.code = "ERR_SANDBOX";
  return err;
}

function __xuCheck(action, target, roots) {
  if (!__xuInside(target, roots)) throw __xuDeny(action, target);
}

// ---------------------------------------------------------------- fs patch

const __xuReadFns = [
  "readFileSync", "readFile", "readdirSync", "readdir", "statSync", "stat",
  "lstatSync", "lstat", "accessSync", "access", "realpathSync", "realpath",
  "readlinkSync", "readlink", "opendirSync", "opendir", "createReadStream",
];
const __xuWriteFns = [
  "writeFileSync", "writeFile", "appendFileSync", "appendFile", "rmSync", "rm",
  "unlinkSync", "unlink", "rmdirSync", "rmdir", "mkdirSync", "mkdir",
  "renameSync", "rename", "copyFileSync", "copyFile", "truncateSync", "truncate",
  "chmodSync", "chmod", "createWriteStream", "symlinkSync", "symlink",
  "linkSync", "link", "utimesSync", "utimes",
];

function __xuWrap(fn, action, roots) {
  return function __xuGuarded(...args) {
    for (let i = 0; i < args.length && i < 2; i += 1) {
      const arg = args[i];
      if (typeof arg === "string" || arg instanceof URL) __xuCheck(action, arg, roots);
    }
    return fn.apply(this, args);
  };
}

for (const name of __xuReadFns) {
  if (typeof __xuFs[name] !== "function") continue;
  __xuFs[name] = __xuWrap(__xuFs[name], "读取", __xuRead);
}
for (const name of __xuWriteFns) {
  if (typeof __xuFs[name] !== "function") continue;
  __xuFs[name] = __xuWrap(__xuFs[name], "写入/修改", __xuWrite);
}

// open 得按 flags 判定读还是写
const __xuRealOpenSync = __xuFs.openSync;
__xuFs.openSync = function __xuGuardedOpenSync(target, flags, ...rest) {
  const writing = typeof flags === "string" ? /[wa+]/.test(flags) : (flags & 3) !== 0;
  __xuCheck(writing ? "写入/修改" : "读取", target, writing ? __xuWrite : __xuRead);
  return __xuRealOpenSync.call(__xuFs, target, flags, ...rest);
};
const __xuRealOpen = __xuFs.open;
__xuFs.open = function __xuGuardedOpen(target, flags, ...rest) {
  const writing = typeof flags === "string" ? /[wa+]/.test(flags) : (flags & 3) !== 0;
  __xuCheck(writing ? "写入/修改" : "读取", target, writing ? __xuWrite : __xuRead);
  return __xuRealOpen.call(__xuFs, target, flags, ...rest);
};

// fs.promises 是独立对象，单独包一层
if (__xuFs.promises) {
  for (const name of __xuReadFns) {
    if (typeof __xuFs.promises[name] === "function") {
      __xuFs.promises[name] = __xuWrap(__xuFs.promises[name], "读取", __xuRead);
    }
  }
  for (const name of __xuWriteFns) {
    if (typeof __xuFs.promises[name] === "function") {
      __xuFs.promises[name] = __xuWrap(__xuFs.promises[name], "写入/修改", __xuWrite);
    }
  }
}

// ---------------------------------------------------------------- 进程与网络

function __xuNoProcess() {
  const err = new Error("[SANDBOX] 沙箱内禁止再启动子进程（child_process）");
  err.code = "ERR_SANDBOX";
  return err;
}

const __xuChildProcess = __xuRequire("node:child_process");
for (const name of ["exec", "execSync", "spawn", "spawnSync", "fork", "execFile", "execFileSync"]) {
  try {
    if (typeof __xuChildProcess[name] === "function") __xuChildProcess[name] = __xuNoProcess;
  } catch {
    /* 内置模块的导出可能是 getter，赋值会失败 —— 下面用 loader 钩子兜底 */
  }
}

// CJS 的所有 require 都经过 Module._load，这是能真正拦住的地方
const __xuModule = __xuRequire("node:module");
const __xuRealLoad = __xuModule._load;
const __xuBlocked = /^(?:node:)?(?:child_process|cluster|net|tls|http|https|http2|dgram|dns|worker_threads)$/;
__xuModule._load = function __xuGuardedLoad(request, parent, isMain) {
  if (typeof request === "string" && __xuBlocked.test(request)) {
    const err = new Error(`[SANDBOX] 沙箱内禁止使用模块 ${request}`);
    err.code = "ERR_SANDBOX";
    throw err;
  }
  return __xuRealLoad.apply(this, arguments);
};

if (process.env.XU_SANDBOX_NET !== "1") {
  const __xuNoNet = () => {
    const err = new Error("[SANDBOX] 沙箱内禁止网络连接");
    err.code = "ERR_SANDBOX";
    throw err; // 必须抛：返回 Error 对象的话 await 会拿到它并静默通过
  };
  try {
    const __xuNet = __xuRequire("node:net");
    __xuNet.connect = __xuNoNet;
    __xuNet.createConnection = __xuNoNet;
    const __xuHttp = __xuRequire("node:http");
    __xuHttp.request = __xuNoNet;
    __xuHttp.get = __xuNoNet;
    const __xuHttps = __xuRequire("node:https");
    __xuHttps.request = __xuNoNet;
    __xuHttps.get = __xuNoNet;
  } catch {
    /* 模块不在就算了 */
  }
  globalThis.fetch = __xuNoNet;
}

export {};

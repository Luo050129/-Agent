/**
 * 最小 zip 读取器。Node 有 zlib 但没有 zip 容器解析，装依赖就为解个压缩包不值，
 * 所以手写 central directory 解析（约 120 行）。只做「读」不做「写」。
 *
 * zip 结构要点：
 *   - 文件末尾找 EOCD（0x06054b50），里面有中央目录的偏移和条目数
 *   - 中央目录每条记录（0x02014b50）带压缩方法、大小、本地头偏移
 *   - 本地头（0x04034b50）之后才是真正的压缩数据，长度取中央目录里的
 *
 * 只支持 store（0）和 deflate（8）两种方法 —— GitHub 打包的仓库正好只用 deflate。
 */

import { inflateRawSync } from "node:zlib";

export class ZipError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ZipError";
  }
}

export interface ZipEntry {
  name: string;
  size: number;
}

interface CentralRecord {
  name: string;
  method: number;
  compressedSize: number;
  size: number;
  localOffset: number;
}

const EOCD = 0x06054b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;

function findEocd(buf: Buffer): number {
  // EOCD 在文件末尾，注释最长 65535 字节
  const start = Math.max(0, buf.length - 65557);
  for (let i = buf.length - 22; i >= start; i -= 1) {
    if (buf.readUInt32LE(i) === EOCD) return i;
  }
  throw new ZipError("不是合法的 zip：找不到中央目录结尾");
}

function readCentral(buf: Buffer): CentralRecord[] {
  const eocd = findEocd(buf);
  const count = buf.readUInt16LE(eocd + 10);
  let offset = buf.readUInt32LE(eocd + 16);

  const records: CentralRecord[] = [];
  for (let i = 0; i < count; i += 1) {
    if (buf.readUInt32LE(offset) !== CENTRAL) break;
    const method = buf.readUInt16LE(offset + 10);
    const compressedSize = buf.readUInt32LE(offset + 20);
    const size = buf.readUInt32LE(offset + 24);
    const nameLen = buf.readUInt16LE(offset + 28);
    const extraLen = buf.readUInt16LE(offset + 30);
    const commentLen = buf.readUInt16LE(offset + 32);
    const localOffset = buf.readUInt32LE(offset + 42);
    const name = buf.toString("utf8", offset + 46, offset + 46 + nameLen);
    records.push({ name, method, compressedSize, size, localOffset });
    offset += 46 + nameLen + extraLen + commentLen;
  }
  return records;
}

function readEntry(buf: Buffer, record: CentralRecord): Buffer {
  const local = record.localOffset;
  if (buf.readUInt32LE(local) !== LOCAL) {
    throw new ZipError(`本地头损坏：${record.name}`);
  }
  const nameLen = buf.readUInt16LE(local + 26);
  const extraLen = buf.readUInt16LE(local + 28);
  const start = local + 30 + nameLen + extraLen;
  const data = buf.subarray(start, start + record.compressedSize);

  if (record.method === 0) return Buffer.from(data); // store
  if (record.method === 8) return inflateRawSync(data);
  throw new ZipError(`不支持的压缩方法 ${record.method}：${record.name}`);
}

/** 列出压缩包内容（跳过目录） */
export function listZip(buf: Buffer): ZipEntry[] {
  return readCentral(buf)
    .filter((r) => !r.name.endsWith("/"))
    .map((r) => ({ name: r.name, size: r.size }));
}

export interface UnzipFilter {
  /** 只要这些后缀（小写比较） */
  suffixes?: string[];
  /** 只要这个前缀下的文件（如 "repo-main/"） */
  prefix?: string;
  /** 单个文件上限（字节），超大文件跳过 */
  maxBytes?: number;
}

/** 解出符合条件的文件，返回 { 相对路径 → 内容 } */
export function readZip(buf: Buffer, filter: UnzipFilter = {}): Map<string, Buffer> {
  const prefix = filter.prefix ?? "";
  const maxBytes = filter.maxBytes ?? 5 * 1024 * 1024;
  const out = new Map<string, Buffer>();

  for (const record of readCentral(buf)) {
    if (record.name.endsWith("/")) continue;
    if (prefix && !record.name.startsWith(prefix)) continue;
    const rel = prefix ? record.name.slice(prefix.length) : record.name;
    if (!rel || rel.includes("__MACOSX/")) continue;
    if (filter.suffixes && !filter.suffixes.some((s) => rel.toLowerCase().endsWith(s))) continue;
    if (record.size > maxBytes) continue;

    // 路径穿越防护：rel 里不许有 .. 或盘符
    const safe = rel.replace(/\\/g, "/");
    if (safe.startsWith("/") || /^[A-Za-z]:/.test(safe) || safe.split("/").includes("..")) continue;

    out.set(safe, readEntry(buf, record));
  }
  return out;
}

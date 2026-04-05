"use strict";

const crypto = require("node:crypto");
const readline = require("node:readline");
const util = require("node:util");
const vm = require("node:vm");
const { stripTypeScriptTypes } = require("node:module");

const contextVarName = process.argv[2] || "ctx";

const blockedNames = Object.freeze([
  "process",
  "require",
  "module",
  "exports",
  "eval",
  "Function",
]);

let lineNumberBase = 1;
let stdoutBuffer = [];
let stderrBuffer = [];
let evidenceBuffer = [];
let nextCallbackId = 0;

const pendingCallbacks = new Map();

function inspectValue(value) {
  return typeof value === "string"
    ? value
    : util.inspect(value, {
        depth: 4,
        breakLength: 120,
        maxArrayLength: 100,
      });
}

function send(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

function settleCallbackResponse(request) {
  const callbackId = Number(request.callback_id);
  const pending = pendingCallbacks.get(callbackId);
  if (!pending) {
    return;
  }
  pendingCallbacks.delete(callbackId);
  if (request.ok) {
    pending.resolve(request.value);
    return;
  }
  pending.reject(new Error(String(request.error || "Host callback failed")));
}

function callHost(name, args = [], kwargs = {}) {
  return new Promise((resolve, reject) => {
    const callbackId = ++nextCallbackId;
    pendingCallbacks.set(callbackId, { resolve, reject });
    send({
      op: "callback_request",
      callback_id: callbackId,
      name,
      args,
      kwargs,
    });
  });
}

function toText(value) {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (Buffer.isBuffer(value)) return value.toString("utf8");
  if (Array.isArray(value) || (typeof value === "object" && value)) {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

function splitLines(text) {
  return toText(text).split(/\r?\n/);
}

function normalizeFlags(flags = "", { ensureGlobal = false } = {}) {
  const normalized = typeof flags === "string" ? flags : "";
  return ensureGlobal && !normalized.includes("g") ? `${normalized}g` : normalized;
}

function regexFrom(pattern, flags = "", options = {}) {
  return new RegExp(String(pattern), normalizeFlags(flags, options));
}

function extractWithPattern(value, pattern, flags = "", maxResults = 100) {
  const text = toText(value);
  const lines = splitLines(text);
  const results = [];
  const rx = regexFrom(pattern, flags, { ensureGlobal: true });

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex += 1) {
    const line = lines[lineIndex];
    rx.lastIndex = 0;
    for (const match of line.matchAll(rx)) {
      results.push({
        value: match[0],
        line_num: lineIndex,
        start: match.index ?? 0,
        end: (match.index ?? 0) + match[0].length,
      });
      if (results.length >= maxResults) {
        return results;
      }
    }
  }

  return results;
}

function peekImpl(value, start = 0, end = null) {
  const text = toText(value);
  return text.slice(start, end === null ? undefined : end);
}

function linesImpl(value, start = 0, end = null) {
  const parts = splitLines(value);
  return parts.slice(start, end === null ? undefined : end).join("\n");
}

function chunkImpl(value, chunkSize, overlap = 0) {
  if (!(chunkSize > 0)) {
    throw new Error("chunk_size must be > 0");
  }
  if (overlap < 0) {
    throw new Error("overlap must be >= 0");
  }
  if (overlap >= chunkSize) {
    throw new Error("overlap must be < chunk_size");
  }

  const text = toText(value);
  const out = [];
  let i = 0;
  while (i < text.length) {
    const j = Math.min(text.length, i + chunkSize);
    out.push(text.slice(i, j));
    if (j === text.length) break;
    i = j - overlap;
  }
  return out;
}

function searchImpl(value, pattern, contextLines = 2, flags = "", maxResults = 20) {
  const rx = regexFrom(pattern, flags);
  const parts = splitLines(value);
  const results = [];

  for (let i = 0; i < parts.length; i += 1) {
    rx.lastIndex = 0;
    if (!rx.test(parts[i])) continue;
    const start = Math.max(0, i - contextLines);
    const end = Math.min(parts.length, i + contextLines + 1);
    results.push({
      match: parts[i],
      line_num: i + lineNumberBase,
      context: parts.slice(start, end).join("\n"),
    });
    if (results.length >= maxResults) break;
  }
  return results;
}

function findAllImpl(value, pattern, flags = "", maxResults = 100) {
  const rx = regexFrom(pattern, flags, { ensureGlobal: true });
  const matches = [];
  const text = toText(value);
  for (const match of text.matchAll(rx)) {
    matches.push(match[0]);
    if (matches.length >= maxResults) break;
  }
  return matches;
}

function countMatchesImpl(value, pattern, flags = "") {
  return findAllImpl(value, pattern, flags, Number.MAX_SAFE_INTEGER).length;
}

function firstMatchImpl(value, pattern, flags = "") {
  const rx = regexFrom(pattern, flags);
  const match = rx.exec(toText(value));
  return match ? match[0] : null;
}

function grepImpl(value, pattern, flags = "") {
  const rx = regexFrom(pattern, flags);
  return splitLines(value).filter((line) => {
    rx.lastIndex = 0;
    return rx.test(line);
  });
}

function grepVImpl(value, pattern, flags = "") {
  const rx = regexFrom(pattern, flags);
  return splitLines(value).filter((line) => {
    rx.lastIndex = 0;
    return !rx.test(line);
  });
}

function grepCImpl(value, pattern, flags = "") {
  return grepImpl(value, pattern, flags).length;
}

function containsImpl(value, pattern, flags = "") {
  const rx = regexFrom(pattern, flags);
  return rx.test(toText(value));
}

function containsAnyImpl(value, patterns, flags = "") {
  return Array.from(patterns).some((pattern) => containsImpl(value, pattern, flags));
}

function containsAllImpl(value, patterns, flags = "") {
  return Array.from(patterns).every((pattern) => containsImpl(value, pattern, flags));
}

function truncateImpl(value, maxChars = 200, suffix = "...") {
  const text = toText(value);
  return text.length <= maxChars ? text : `${text.slice(0, Math.max(0, maxChars - suffix.length))}${suffix}`;
}

function wordCountImpl(value) {
  const text = toText(value).trim();
  if (!text) return 0;
  return text.split(/\s+/).length;
}

function charCountImpl(value, includeWhitespace = true) {
  const text = toText(value);
  if (includeWhitespace) {
    return text.length;
  }
  return text.replace(/[ \n\t]/g, "").length;
}

function lineCountImpl(value) {
  return splitLines(value).length;
}

function sentenceCountImpl(value) {
  return toText(value)
    .split(/[.!?]+/)
    .filter((part) => part.trim()).length;
}

function paragraphCountImpl(value) {
  return toText(value)
    .split(/\n\s*\n/)
    .filter((part) => part.trim()).length;
}

function uniqueWordsImpl(value, caseInsensitive = true) {
  const text = caseInsensitive ? toText(value).toLowerCase() : toText(value);
  const words = text.match(/\b\w+\b/g) || [];
  return Array.from(new Set(words));
}

function wordFrequencyImpl(value, topN = 20, caseInsensitive = true) {
  const text = caseInsensitive ? toText(value).toLowerCase() : toText(value);
  const words = text.match(/\b\w+\b/g) || [];
  const counts = new Map();
  for (const word of words) {
    counts.set(word, (counts.get(word) || 0) + 1);
  }
  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1])
    .slice(0, topN);
}

function ngramsImpl(value, n = 2, topK = 20) {
  if (n <= 0) {
    throw new Error("n must be > 0");
  }
  const words = (toText(value).toLowerCase().match(/\b\w+\b/g) || []);
  const counts = new Map();
  for (let i = 0; i <= words.length - n; i += 1) {
    const gram = words.slice(i, i + n);
    const key = gram.join("\u0000");
    counts.set(key, { gram, count: (counts.get(key)?.count || 0) + 1 });
  }
  return Array.from(counts.values())
    .sort((a, b) => b.count - a.count)
    .slice(0, topK)
    .map((item) => [item.gram, item.count]);
}

function headImpl(value, n = 10) {
  return linesImpl(value, 0, n);
}

function tailImpl(value, n = 10) {
  const parts = splitLines(value);
  return parts.slice(Math.max(0, parts.length - n)).join("\n");
}

function uniqImpl(value) {
  const result = [];
  let previous = Symbol("start");
  for (const line of splitLines(value)) {
    if (line !== previous) {
      result.push(line);
      previous = line;
    }
  }
  return result;
}

function sortLinesImpl(value, reverse = false, numeric = false) {
  const lines = splitLines(value);
  if (numeric) {
    return [...lines].sort((a, b) => {
      const left = Number((a.match(/-?\d+\.?\d*/) || ["0"])[0]);
      const right = Number((b.match(/-?\d+\.?\d*/) || ["0"])[0]);
      return reverse ? right - left : left - right;
    });
  }
  return [...lines].sort((a, b) => (reverse ? b.localeCompare(a) : a.localeCompare(b)));
}

function numberLinesImpl(value, start = 1) {
  const lines = splitLines(value);
  const width = String(start + lines.length).length;
  return lines
    .map((line, index) => `${String(start + index).padStart(width, " ")}: ${line}`)
    .join("\n");
}

function stripLinesImpl(value) {
  return splitLines(value).map((line) => line.trim());
}

function blankLinesImpl(value) {
  const results = [];
  for (const [index, line] of splitLines(value).entries()) {
    if (!line.trim()) {
      results.push(index);
    }
  }
  return results;
}

function nonBlankLinesImpl(value) {
  return splitLines(value).filter((line) => line.trim());
}

function columnsImpl(value, col, delim = "\\s+") {
  const results = [];
  for (const line of splitLines(value)) {
    const parts = line.split(regexFrom(delim, "g"));
    if (col < parts.length) {
      results.push(parts[col]);
    }
  }
  return results;
}

function replaceAllImpl(value, pattern, replacement, flags = "") {
  return toText(value).replace(regexFrom(pattern, flags, { ensureGlobal: true }), replacement);
}

function splitByImpl(value, pattern, flags = "") {
  return toText(value).split(regexFrom(pattern, flags));
}

function betweenImpl(value, startPattern, endPattern, includeMarkers = false) {
  const pattern = includeMarkers
    ? `(${startPattern}.*?${endPattern})`
    : `${startPattern}(.*?)${endPattern}`;
  return Array.from(toText(value).matchAll(regexFrom(pattern, "gs"))).map((match) => match[1] ?? match[0]);
}

function beforeImpl(value, pattern, flags = "") {
  const text = toText(value);
  const match = regexFrom(pattern, flags).exec(text);
  return match ? text.slice(0, match.index ?? 0) : text;
}

function afterImpl(value, pattern, flags = "") {
  const text = toText(value);
  const match = regexFrom(pattern, flags).exec(text);
  return match ? text.slice((match.index ?? 0) + match[0].length) : "";
}

function wrapTextImpl(value, width = 80) {
  if (width <= 0) {
    throw new Error("width must be > 0");
  }
  const text = toText(value).trim();
  if (!text) {
    return "";
  }
  const words = text.split(/\s+/);
  const lines = [];
  let current = "";
  for (const word of words) {
    if (!current) {
      current = word;
      continue;
    }
    if (current.length + 1 + word.length > width) {
      lines.push(current);
      current = word;
      continue;
    }
    current += ` ${word}`;
  }
  if (current) {
    lines.push(current);
  }
  return lines.join("\n");
}

function indentTextImpl(value, prefix = "  ") {
  return splitLines(value).map((line) => `${prefix}${line}`).join("\n");
}

function dedentTextImpl(value) {
  const lines = splitLines(value);
  let minIndent = Infinity;
  for (const line of lines) {
    if (!line.trim()) continue;
    const indent = (line.match(/^\s*/) || [""])[0].length;
    minIndent = Math.min(minIndent, indent);
  }
  if (!Number.isFinite(minIndent)) {
    return toText(value);
  }
  return lines.map((line) => line.slice(Math.min(minIndent, line.length))).join("\n");
}

function normalizeWhitespaceImpl(value) {
  return splitLines(value)
    .map((line) => line.trim().replace(/\s+/g, " "))
    .join("\n");
}

function removePunctuationImpl(value) {
  return toText(value).replace(/[^\w\s]/g, "");
}

function buildBigrams(text) {
  const normalized = String(text);
  if (normalized.length < 2) {
    return normalized ? [normalized] : [];
  }
  const grams = [];
  for (let i = 0; i < normalized.length - 1; i += 1) {
    grams.push(normalized.slice(i, i + 2));
  }
  return grams;
}

function similarityImpl(leftValue, rightValue) {
  const left = toText(leftValue);
  const right = toText(rightValue);
  if (left === right) {
    return 1.0;
  }
  if (!left || !right) {
    return 0.0;
  }

  const leftCounts = new Map();
  for (const gram of buildBigrams(left)) {
    leftCounts.set(gram, (leftCounts.get(gram) || 0) + 1);
  }

  let overlap = 0;
  for (const gram of buildBigrams(right)) {
    const remaining = leftCounts.get(gram) || 0;
    if (remaining > 0) {
      overlap += 1;
      leftCounts.set(gram, remaining - 1);
    }
  }

  const total = Math.max(1, buildBigrams(left).length + buildBigrams(right).length);
  return (2 * overlap) / total;
}

function buildLineDiffOps(leftLines, rightLines) {
  const rows = Array.from({ length: leftLines.length + 1 }, () => Array(rightLines.length + 1).fill(0));

  for (let i = leftLines.length - 1; i >= 0; i -= 1) {
    for (let j = rightLines.length - 1; j >= 0; j -= 1) {
      rows[i][j] = leftLines[i] === rightLines[j]
        ? rows[i + 1][j + 1] + 1
        : Math.max(rows[i + 1][j], rows[i][j + 1]);
    }
  }

  const ops = [];
  let i = 0;
  let j = 0;
  while (i < leftLines.length && j < rightLines.length) {
    if (leftLines[i] === rightLines[j]) {
      ops.push({ type: "equal", line: leftLines[i] });
      i += 1;
      j += 1;
      continue;
    }
    if (rows[i + 1][j] >= rows[i][j + 1]) {
      ops.push({ type: "delete", line: leftLines[i] });
      i += 1;
      continue;
    }
    ops.push({ type: "insert", line: rightLines[j] });
    j += 1;
  }

  while (i < leftLines.length) {
    ops.push({ type: "delete", line: leftLines[i] });
    i += 1;
  }
  while (j < rightLines.length) {
    ops.push({ type: "insert", line: rightLines[j] });
    j += 1;
  }

  return ops;
}

function diffImpl(leftValue, rightValue, contextLines = 3) {
  const leftLines = splitLines(leftValue);
  const rightLines = splitLines(rightValue);
  const ops = buildLineDiffOps(leftLines, rightLines);
  if (!ops.some((op) => op.type !== "equal")) {
    return "";
  }

  const expanded = [];
  let leftLine = 1;
  let rightLine = 1;
  for (const op of ops) {
    expanded.push({
      ...op,
      leftLine,
      rightLine,
    });
    if (op.type !== "insert") {
      leftLine += 1;
    }
    if (op.type !== "delete") {
      rightLine += 1;
    }
  }

  const changed = expanded
    .map((op, index) => ({ op, index }))
    .filter(({ op }) => op.type !== "equal")
    .map(({ index }) => index);
  const ranges = [];
  for (const index of changed) {
    const start = Math.max(0, index - Math.max(0, contextLines));
    const end = Math.min(expanded.length, index + Math.max(0, contextLines) + 1);
    const last = ranges[ranges.length - 1];
    if (last && start <= last.end) {
      last.end = Math.max(last.end, end);
    } else {
      ranges.push({ start, end });
    }
  }

  const chunks = ["--- a", "+++ b"];
  for (const range of ranges) {
    const slice = expanded.slice(range.start, range.end);
    const oldStart = slice.find((op) => op.type !== "insert")?.leftLine || slice[0]?.leftLine || 1;
    const newStart = slice.find((op) => op.type !== "delete")?.rightLine || slice[0]?.rightLine || 1;
    const oldCount = slice.filter((op) => op.type !== "insert").length;
    const newCount = slice.filter((op) => op.type !== "delete").length;
    chunks.push(`@@ -${oldStart},${oldCount} +${newStart},${newCount} @@`);
    for (const op of slice) {
      const prefix = op.type === "equal" ? " " : op.type === "delete" ? "-" : "+";
      chunks.push(`${prefix}${op.line}`);
    }
  }

  return chunks.join("\n");
}

function commonLinesImpl(leftValue, rightValue) {
  const seen = new Set(splitLines(rightValue));
  return splitLines(leftValue).filter((line, index, array) => seen.has(line) && array.indexOf(line) === index);
}

function diffLinesImpl(leftValue, rightValue) {
  const left = splitLines(leftValue);
  const right = splitLines(rightValue);
  const leftSet = new Set(left);
  const rightSet = new Set(right);
  return {
    only_in_first: left.filter((line, index, array) => !rightSet.has(line) && array.indexOf(line) === index),
    only_in_second: right.filter((line, index, array) => !leftSet.has(line) && array.indexOf(line) === index),
  };
}

function flattenImpl(nested, depth = -1) {
  const out = [];
  const inner = (value, remaining) => {
    if (Array.isArray(value) && remaining !== 0) {
      const nextDepth = remaining > 0 ? remaining - 1 : -1;
      for (const item of value) {
        inner(item, nextDepth);
      }
      return;
    }
    out.push(value);
  };
  inner(nested, depth);
  return out;
}

function firstImpl(items, defaultValue = null) {
  const values = Array.from(items || []);
  return values.length > 0 ? values[0] : defaultValue;
}

function lastImpl(items, defaultValue = null) {
  const values = Array.from(items || []);
  return values.length > 0 ? values[values.length - 1] : defaultValue;
}

function takeImpl(count, items) {
  return Array.from(items || []).slice(0, Math.max(0, count));
}

function dropImpl(count, items) {
  return Array.from(items || []).slice(Math.max(0, count));
}

function partitionImpl(items, predicate) {
  if (typeof predicate !== "function") {
    throw new Error("partition requires a predicate function");
  }
  const matches = [];
  const nonMatches = [];
  for (const item of Array.from(items || [])) {
    if (predicate(item)) {
      matches.push(item);
    } else {
      nonMatches.push(item);
    }
  }
  return [matches, nonMatches];
}

function groupKeyFor(item, keyFn) {
  if (typeof keyFn === "function") {
    return keyFn(item);
  }
  if (typeof keyFn === "string" && item && typeof item === "object") {
    return item[keyFn];
  }
  return item;
}

function groupByImpl(items, keyFn) {
  const out = {};
  for (const item of Array.from(items || [])) {
    const key = String(groupKeyFor(item, keyFn));
    if (!Object.prototype.hasOwnProperty.call(out, key)) {
      out[key] = [];
    }
    out[key].push(item);
  }
  return out;
}

function frequencyImpl(items, topN = null) {
  const counts = new Map();
  for (const item of Array.from(items || [])) {
    const key = item && typeof item === "object" ? JSON.stringify(item) : String(item);
    const current = counts.get(key);
    if (current) {
      current[1] += 1;
    } else {
      counts.set(key, [item, 1]);
    }
  }
  const ranked = Array.from(counts.values()).sort((left, right) => right[1] - left[1]);
  return topN == null ? ranked : ranked.slice(0, Math.max(0, topN));
}

function createSeededRng(seed = null) {
  if (seed == null) {
    return Math.random;
  }
  let state = Number(seed) >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = Math.imul(state ^ (state >>> 15), 1 | state);
    t ^= t + Math.imul(t ^ (t >>> 7), 61 | t);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function sampleItemsImpl(items, count, seed = null) {
  const values = Array.from(items || []);
  const shuffled = shuffleItemsImpl(values, seed);
  return shuffled.slice(0, Math.max(0, count));
}

function shuffleItemsImpl(items, seed = null) {
  const values = Array.from(items || []);
  const rng = createSeededRng(seed);
  for (let i = values.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rng() * (i + 1));
    [values[i], values[j]] = [values[j], values[i]];
  }
  return values;
}

function isNumericImpl(text) {
  const normalized = String(text ?? "").replace(/,/g, "").trim();
  if (!normalized) {
    return false;
  }
  return Number.isFinite(Number(normalized));
}

function isEmailImpl(text) {
  return /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(String(text ?? "").trim());
}

function isUrlImpl(text) {
  return /^https?:\/\/[^\s<>"']+$/.test(String(text ?? "").trim());
}

function isIpImpl(text) {
  const parts = String(text ?? "").trim().split(".");
  if (parts.length !== 4) {
    return false;
  }
  return parts.every((part) => /^\d+$/.test(part) && Number(part) >= 0 && Number(part) <= 255);
}

function isUuidImpl(text) {
  return /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/.test(String(text ?? "").trim());
}

function isJsonImpl(text) {
  try {
    JSON.parse(String(text ?? ""));
    return true;
  } catch {
    return false;
  }
}

function isBlankImpl(text) {
  return !String(text ?? "").trim();
}

function toCsvRowImpl(items, delim = ",") {
  return Array.from(items || []).map((item) => {
    const text = String(item ?? "");
    if (text.includes('"') || text.includes("\n") || text.includes("\r") || text.includes(delim)) {
      return `"${text.replace(/"/g, '""')}"`;
    }
    return text;
  }).join(delim);
}

function fromCsvRowImpl(text, delim = ",") {
  const input = String(text ?? "");
  const out = [];
  let current = "";
  let inQuotes = false;
  for (let i = 0; i < input.length; i += 1) {
    const char = input[i];
    if (inQuotes) {
      if (char === '"' && input[i + 1] === '"') {
        current += '"';
        i += 1;
      } else if (char === '"') {
        inQuotes = false;
      } else {
        current += char;
      }
      continue;
    }
    if (char === '"') {
      inQuotes = true;
      continue;
    }
    if (char === delim) {
      out.push(current);
      current = "";
      continue;
    }
    current += char;
  }
  out.push(current);
  return out;
}

function toLowerImpl(value) {
  return toText(value).toLowerCase();
}

function toUpperImpl(value) {
  return toText(value).toUpperCase();
}

function toTitleImpl(value) {
  return toText(value).replace(/\w\S*/g, (word) => word[0].toUpperCase() + word.slice(1).toLowerCase());
}

function dedupeImpl(items) {
  const seen = new Set();
  const out = [];
  for (const item of Array.from(items)) {
    const key = item && typeof item === "object" ? JSON.stringify(item) : String(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function toJsonImpl(value, indent = 2) {
  return JSON.stringify(value, null, indent);
}

function fromJsonImpl(text) {
  return JSON.parse(String(text));
}

function toIntImpl(text, defaultValue = 0) {
  const parsed = Number.parseInt(String(text).replace(/,/g, "").trim(), 10);
  return Number.isNaN(parsed) ? defaultValue : parsed;
}

function toFloatImpl(text, defaultValue = 0.0) {
  const parsed = Number.parseFloat(String(text).replace(/,/g, "").trim());
  return Number.isNaN(parsed) ? defaultValue : parsed;
}

function toSnakeCaseImpl(text) {
  return String(text)
    .replace(/(.)([A-Z][a-z]+)/g, "$1_$2")
    .replace(/([a-z0-9])([A-Z])/g, "$1_$2")
    .replace(/[-\s]+/g, "_")
    .toLowerCase();
}

function toCamelCaseImpl(text) {
  const parts = String(text).split(/[-_\s]+/).filter(Boolean);
  if (parts.length === 0) return "";
  return parts[0].toLowerCase() + parts.slice(1).map((part) => part[0].toUpperCase() + part.slice(1).toLowerCase()).join("");
}

function toPascalCaseImpl(text) {
  return String(text)
    .split(/[-_\s]+/)
    .filter(Boolean)
    .map((part) => part[0].toUpperCase() + part.slice(1).toLowerCase())
    .join("");
}

function toKebabCaseImpl(text) {
  return toSnakeCaseImpl(text).replace(/_/g, "-");
}

function slugifyImpl(text) {
  return String(text)
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .replace(/[-\s]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// =============================================================================
// Recipe DSL (mirrors Python RecipeStep / RecipeBuilder)
// =============================================================================

const RECIPE_DSL_VERSION = "aleph.recipe.v1";

class RecipeStep {
  constructor(op, params = {}) {
    this._payload = { op };
    for (const [key, value] of Object.entries(params)) {
      if (value != null) {
        this._payload[key] = value;
      }
    }
  }

  toDict() {
    return { ...this._payload };
  }

  toJSON() {
    return this.toDict();
  }

  toString() {
    return `RecipeStep(${JSON.stringify(this._payload)})`;
  }
}

class RecipeBuilder {
  constructor(contextId = "default", { version = RECIPE_DSL_VERSION, steps = null, budget = null } = {}) {
    this.version = version;
    this.contextId = contextId;
    this._steps = (steps || []).map((s) => ({ ...s }));
    this._budget = budget ? { ...budget } : {};
  }

  get steps() {
    return this._steps.map((s) => ({ ...s }));
  }

  _clone() {
    return new RecipeBuilder(this.contextId, {
      version: this.version,
      steps: this._steps,
      budget: this._budget,
    });
  }

  _append(step) {
    const payload = step instanceof RecipeStep ? step.toDict() : { ...step };
    const next = this._clone();
    next._steps.push(payload);
    return next;
  }

  pipe(step) {
    return this._append(step);
  }

  withBudget({ maxSteps = null, maxSubQueries = null } = {}) {
    const next = this._clone();
    if (maxSteps != null) next._budget.max_steps = maxSteps;
    if (maxSubQueries != null) next._budget.max_sub_queries = maxSubQueries;
    return next;
  }

  toDict() {
    const payload = {
      version: this.version,
      context_id: this.contextId,
      steps: this._steps.map((s) => ({ ...s })),
    };
    if (Object.keys(this._budget).length > 0) {
      payload.budget = { ...this._budget };
    }
    return payload;
  }

  compile() {
    return this.toDict();
  }

  toJSON() {
    return this.toDict();
  }

  step(op, params = {}) {
    return this.pipe(new RecipeStep(op, params));
  }

  search(pattern, { contextLines = 2, maxResults = 20, input = null, store = null } = {}) {
    return this.pipe(
      new RecipeStep("search", {
        pattern,
        context_lines: contextLines,
        max_results: maxResults,
        input,
        store,
      })
    );
  }

  peek({ start = 0, end = null, input = null, store = null } = {}) {
    return this.pipe(new RecipeStep("peek", { start, end, input, store }));
  }

  lines({ start = 0, end = null, input = null, store = null } = {}) {
    return this.pipe(new RecipeStep("lines", { start, end, input, store }));
  }

  take(count, { input = null, store = null } = {}) {
    return this.pipe(new RecipeStep("take", { count, input, store }));
  }

  chunk(chunkSize, { overlap = 0, input = null, store = null } = {}) {
    return this.pipe(new RecipeStep("chunk", { chunk_size: chunkSize, overlap, input, store }));
  }

  filter({ pattern = null, contains = null, field = null, input = null, store = null } = {}) {
    return this.pipe(new RecipeStep("filter", { pattern, contains, field, input, store }));
  }

  mapSubQuery(prompt, {
    backend = "auto",
    contextField = null,
    limit = null,
    continueOnError = false,
    input = null,
    store = null,
  } = {}) {
    return this.pipe(
      new RecipeStep("map_sub_query", {
        prompt,
        backend,
        context_field: contextField,
        limit,
        continue_on_error: continueOnError,
        input,
        store,
      })
    );
  }

  subQuery(prompt, { backend = "auto", contextField = null, input = null, store = null } = {}) {
    return this.pipe(
      new RecipeStep("sub_query", { prompt, backend, context_field: contextField, input, store })
    );
  }

  aggregate(prompt, { backend = "auto", contextField = null, input = null, store = null } = {}) {
    return this.pipe(
      new RecipeStep("aggregate", { prompt, backend, context_field: contextField, input, store })
    );
  }

  assign(name, { input = null } = {}) {
    return this.pipe(new RecipeStep("assign", { name, input }));
  }

  load(name, { store = null } = {}) {
    return this.pipe(new RecipeStep("load", { name, store }));
  }

  finalize() {
    return this.pipe(new RecipeStep("finalize"));
  }

  toString() {
    return `RecipeBuilder(contextId=${JSON.stringify(this.contextId)}, steps=${JSON.stringify(this._steps)})`;
  }
}

function Recipe(contextId = "default", { maxSteps = null, maxSubQueries = null } = {}) {
  return new RecipeBuilder(contextId).withBudget({ maxSteps, maxSubQueries });
}

function Step(op, params = {}) {
  return new RecipeStep(op, params);
}

function Search(pattern, { contextLines = 2, maxResults = 20, input = null, store = null } = {}) {
  return new RecipeStep("search", {
    pattern,
    context_lines: contextLines,
    max_results: maxResults,
    input,
    store,
  });
}

function Peek({ start = 0, end = null, input = null, store = null } = {}) {
  return new RecipeStep("peek", { start, end, input, store });
}

function Lines({ start = 0, end = null, input = null, store = null } = {}) {
  return new RecipeStep("lines", { start, end, input, store });
}

function Take(count, { input = null, store = null } = {}) {
  return new RecipeStep("take", { count, input, store });
}

function Chunk(chunkSize, { overlap = 0, input = null, store = null } = {}) {
  return new RecipeStep("chunk", { chunk_size: chunkSize, overlap, input, store });
}

function Filter({ pattern = null, contains = null, field = null, input = null, store = null } = {}) {
  return new RecipeStep("filter", { pattern, contains, field, input, store });
}

function MapSubQuery(prompt, {
  backend = "auto",
  contextField = null,
  limit = null,
  continueOnError = false,
  input = null,
  store = null,
} = {}) {
  return new RecipeStep("map_sub_query", {
    prompt,
    backend,
    context_field: contextField,
    limit,
    continue_on_error: continueOnError,
    input,
    store,
  });
}

function SubQuery(prompt, { backend = "auto", contextField = null, input = null, store = null } = {}) {
  return new RecipeStep("sub_query", { prompt, backend, context_field: contextField, input, store });
}

function Aggregate(prompt, { backend = "auto", contextField = null, input = null, store = null } = {}) {
  return new RecipeStep("aggregate", { prompt, backend, context_field: contextField, input, store });
}

function Assign(name, { input = null } = {}) {
  return new RecipeStep("assign", { name, input });
}

function Load(name, { store = null } = {}) {
  return new RecipeStep("load", { name, store });
}

function Finalize() {
  return new RecipeStep("finalize");
}

function asRecipe(value) {
  if (value instanceof RecipeBuilder) return value.toDict();
  if (value && typeof value === "object" && !Array.isArray(value)) return { ...value };
  throw new TypeError("as_recipe expects RecipeBuilder or object");
}

// =============================================================================

function embedTextImpl(text, dim = 256) {
  if (!(dim > 0)) {
    throw new Error("dim must be > 0");
  }
  const vec = Array.from({ length: dim }, () => 0);
  const tokens = String(text).toLowerCase().match(/[A-Za-z0-9_]+/g) || [];
  for (const token of tokens) {
    if (token.length < 2) continue;
    const digest = crypto.createHash("blake2b512").update(token, "utf8").digest();
    const idx = digest.readUInt32LE(0) % dim;
    vec[idx] += 1;
  }
  const norm = Math.sqrt(vec.reduce((acc, value) => acc + value * value, 0));
  return norm > 0 ? vec.map((value) => value / norm) : vec;
}

function cosineSimilarityImpl(a, b) {
  if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return 0;
  let total = 0;
  for (let i = 0; i < a.length; i += 1) {
    total += a[i] * b[i];
  }
  return total;
}

function semanticSearchImpl(value, query, chunkSize = 1000, overlap = 100, topK = 5, embedDim = 256) {
  if (!query) {
    return [];
  }
  const chunks = chunkImpl(value, chunkSize, overlap);
  if (chunks.length === 0) {
    return [];
  }
  const queryVector = embedTextImpl(String(query), embedDim);
  const results = [];
  let position = 0;
  for (let index = 0; index < chunks.length; index += 1) {
    const chunkText = chunks[index];
    const chunkVector = embedTextImpl(chunkText, embedDim);
    results.push({
      index,
      score: cosineSimilarityImpl(queryVector, chunkVector),
      start_char: position,
      end_char: position + chunkText.length,
      preview: chunkText.length > 200 ? `${chunkText.slice(0, 200)}...` : chunkText,
    });
    position += index < chunks.length - 1 ? chunkText.length - overlap : chunkText.length;
  }
  return results.sort((a, b) => b.score - a.score).slice(0, Math.max(0, topK));
}

function extractNumbersImpl(value, includeNegative = true, includeDecimals = true) {
  let pattern;
  if (includeDecimals && includeNegative) {
    pattern = "-?\\d+\\.?\\d*";
  } else if (includeDecimals) {
    pattern = "\\d+\\.?\\d*";
  } else if (includeNegative) {
    pattern = "-?\\d+";
  } else {
    pattern = "\\d+";
  }
  return extractWithPattern(value, pattern);
}

function extractMoneyImpl(value, currencies = "[$€£¥₹]") {
  return extractWithPattern(value, `${currencies}\\s*[\\d,]+\\.?\\d*|\\d+\\.?\\d*\\s*${currencies}`);
}

function extractPercentagesImpl(value) {
  return extractWithPattern(value, "-?\\d+\\.?\\d*\\s*%");
}

function extractDatesImpl(value) {
  const patterns = [
    "\\d{4}-\\d{2}-\\d{2}",
    "\\d{1,2}/\\d{1,2}/\\d{2,4}",
    "\\d{1,2}-\\d{1,2}-\\d{2,4}",
    "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\.?\\s+\\d{1,2},?\\s+\\d{4}",
    "\\d{1,2}\\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\\.?\\s+\\d{4}",
  ];
  return extractWithPattern(value, patterns.map((pattern) => `(${pattern})`).join("|"), "i");
}

function extractTimesImpl(value) {
  return extractWithPattern(value, "\\d{1,2}:\\d{2}(?::\\d{2})?(?:\\s*[AaPp][Mm])?");
}

function extractTimestampsImpl(value) {
  const patterns = [
    "\\d{4}-\\d{2}-\\d{2}[T ]\\d{2}:\\d{2}:\\d{2}(?:\\.\\d+)?(?:Z|[+-]\\d{2}:?\\d{2})?",
    "\\d{4}/\\d{2}/\\d{2} \\d{2}:\\d{2}:\\d{2}",
    "(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\\s+\\d{1,2}\\s+\\d{2}:\\d{2}:\\d{2}",
  ];
  return extractWithPattern(value, patterns.map((pattern) => `(${pattern})`).join("|"), "i");
}

function extractEmailsImpl(value) {
  return extractWithPattern(value, "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}");
}

function extractUrlsImpl(value) {
  return extractWithPattern(value, "https?://[^\\s<>\"']+|ftp://[^\\s<>\"']+|www\\.[^\\s<>\"']+");
}

function extractIpsImpl(value, includeIpv6 = false) {
  const ipv4 = "\\b(?:\\d{1,3}\\.){3}\\d{1,3}\\b";
  const ipv6 = "(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}";
  return extractWithPattern(value, includeIpv6 ? `${ipv4}|${ipv6}` : ipv4);
}

function extractPhonesImpl(value) {
  return extractWithPattern(value, "(?:\\+\\d{1,3}[-.\\s]?)?\\(?\\d{3}\\)?[-.\\s]?\\d{3}[-.\\s]?\\d{4}");
}

function extractHexImpl(value) {
  return extractWithPattern(value, "0x[0-9a-fA-F]+|#[0-9a-fA-F]{3,8}\\b");
}

function extractUuidsImpl(value) {
  return extractWithPattern(value, "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}");
}

function extractPathsImpl(value) {
  const patterns = [
    "/(?:[^/\\s]+/)*[^/\\s]+",
    "[A-Za-z]:\\\\(?:[^\\\\:\\s]+\\\\)*[^\\\\:\\s]+",
    "\\.{1,2}/(?:[^/\\s]+/)*[^/\\s]*",
  ];
  return extractWithPattern(value, patterns.map((pattern) => `(${pattern})`).join("|"));
}

function extractEnvVarsImpl(value) {
  return extractWithPattern(value, "\\$\\{[A-Za-z_][A-Za-z0-9_]*\\}|\\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%");
}

function extractVersionsImpl(value) {
  return extractWithPattern(value, "v?\\d+\\.\\d+(?:\\.\\d+)?(?:-[a-zA-Z0-9.]+)?(?:\\+[a-zA-Z0-9.]+)?");
}

function extractHashesImpl(value) {
  const patterns = [
    "\\b[a-fA-F0-9]{32}\\b",
    "\\b[a-fA-F0-9]{40}\\b",
    "\\b[a-fA-F0-9]{64}\\b",
  ];
  return extractWithPattern(value, patterns.join("|"));
}

function extractFunctionsImpl(value, lang = "python") {
  const patterns = {
    python: "(?:async\\s+)?def\\s+([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\(",
    javascript:
      "(?:async\\s+)?function\\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\\s*\\(|(?:const|let|var)\\s+([a-zA-Z_$][a-zA-Z0-9_$]*)\\s*=\\s*(?:async\\s+)?\\([^)]*\\)\\s*=>",
    go: "func\\s+(?:\\([^)]+\\)\\s+)?([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\(",
    rust: "(?:pub\\s+)?(?:async\\s+)?fn\\s+([a-zA-Z_][a-zA-Z0-9_]*)",
    java: "(?:public|private|protected)?\\s*(?:static\\s+)?(?:\\w+\\s+)+([a-zA-Z_][a-zA-Z0-9_]*)\\s*\\(",
  };
  return extractWithPattern(value, patterns[String(lang).toLowerCase()] || patterns.python);
}

function extractClassesImpl(value, lang = "python") {
  const patterns = {
    python: "class\\s+([A-Za-z_][A-Za-z0-9_]*)",
    javascript: "class\\s+([A-Za-z_$][A-Za-z0-9_$]*)",
    java: "(?:public\\s+)?(?:abstract\\s+)?(?:final\\s+)?class\\s+([A-Za-z_][A-Za-z0-9_]*)",
    go: "type\\s+([A-Za-z_][A-Za-z0-9_]*)\\s+struct",
    rust: "(?:pub\\s+)?struct\\s+([A-Za-z_][A-Za-z0-9_]*)",
  };
  return extractWithPattern(value, patterns[String(lang).toLowerCase()] || patterns.python);
}

function extractImportsImpl(value, lang = "python") {
  const patterns = {
    python: "(?:from\\s+[\\w.]+\\s+)?import\\s+[\\w., ]+",
    javascript: "import\\s+.*?from\\s+['\"][^'\"]+['\"]|require\\s*\\(['\"][^'\"]+['\"]\\)",
    go: "import\\s+(?:\\(\\s*(?:\"[^\"]+\"\\s*)+\\)|\"[^\"]+\")",
    java: "import\\s+[\\w.]+;",
    rust: "use\\s+[\\w:]+;",
  };
  return extractWithPattern(value, patterns[String(lang).toLowerCase()] || patterns.python);
}

function extractCommentsImpl(value, lang = "python") {
  const patterns = {
    python: "#.*$|'''[\\s\\S]*?'''|\"\"\"[\\s\\S]*?\"\"\"",
    javascript: "//.*$|/\\*[\\s\\S]*?\\*/",
    go: "//.*$|/\\*[\\s\\S]*?\\*/",
    java: "//.*$|/\\*[\\s\\S]*?\\*/",
    rust: "//.*$|/\\*[\\s\\S]*?\\*/",
    c: "//.*$|/\\*[\\s\\S]*?\\*/",
    html: "<!--[\\s\\S]*?-->",
    css: "/\\*[\\s\\S]*?\\*/",
  };
  return extractWithPattern(value, patterns[String(lang).toLowerCase()] || patterns.python, "m");
}

function extractRoutesImpl(value, lang = "auto") {
  const patterns = {
    python: "@(?:app|router)\\.(?:get|post|put|delete|patch|options|head)\\(\\s*[\"'][^\"']+",
    django: "\\b(?:path|re_path)\\(\\s*r?[\"'][^\"']+",
    javascript: "\\b(?:app|router)\\.(?:get|post|put|delete|patch|options|head|use)\\(\\s*[\"'][^\"']+",
    ruby: "\\b(?:get|post|put|delete|patch|match)\\s+[\"'][^\"']+",
  };
  const key = String(lang).toLowerCase().trim();
  const pattern = patterns[key] || Object.values(patterns).map((item) => `(${item})`).join("|");
  return extractWithPattern(value, pattern, "im");
}

function extractStringsImpl(value) {
  return extractWithPattern(value, "\"(?:[^\"\\\\]|\\\\.)*\"|'(?:[^'\\\\]|\\\\.)*'|`(?:[^`\\\\]|\\\\.)*`");
}

function extractTodosImpl(value) {
  return extractWithPattern(value, "(?:TODO|FIXME|HACK|XXX|BUG|NOTE)[\\s:]+.*", "i");
}

function extractLogLevelsImpl(value) {
  return extractWithPattern(value, "\\b(?:FATAL|ERROR|WARN(?:ING)?|INFO|DEBUG|TRACE)\\b", "i");
}

function extractExceptionsImpl(value) {
  const patterns = [
    "(?:Exception|Error|Traceback).*",
    "at\\s+[\\w.$]+\\([\\w.:]+\\)",
    "File \".*\", line \\d+",
  ];
  return extractWithPattern(value, patterns.join("|"));
}

function extractJsonObjectsImpl(value) {
  return extractWithPattern(value, "\\{[^{}]*(?:\\{[^{}]*\\}[^{}]*)*\\}");
}

function citeImpl(snippet, lineRange = null, note = null) {
  const citation = {
    snippet: String(snippet).slice(0, 500),
    line_range: Array.isArray(lineRange) ? lineRange.slice(0, 2) : lineRange,
    note: note == null ? null : String(note),
  };
  evidenceBuffer.push(citation);
  return citation;
}

function serialize(value, seen = new WeakSet()) {
  if (value === undefined) return { kind: "undefined", value: null };
  if (
    value === null ||
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  ) {
    return { kind: "json", value };
  }
  if (typeof value === "bigint") {
    return { kind: "repr", value: value.toString() };
  }
  if (typeof value === "function") {
    return { kind: "repr", value: `[Function ${value.name || "anonymous"}]` };
  }
  if (value instanceof Date) {
    return { kind: "json", value: value.toISOString() };
  }
  if (value instanceof RegExp) {
    return { kind: "repr", value: String(value) };
  }
  if (typeof value === "object") {
    if (seen.has(value)) {
      return { kind: "repr", value: "[Circular]" };
    }
    seen.add(value);
    if (Array.isArray(value)) {
      return { kind: "json", value: value.map((item) => serialize(item, seen).value) };
    }
    if (typeof value.toJSON === "function") {
      return serialize(value.toJSON(), seen);
    }
    const proto = Object.getPrototypeOf(value);
    const tag = Object.prototype.toString.call(value);
    if (proto === Object.prototype || proto === null || tag === "[object Object]") {
      const out = {};
      for (const [key, item] of Object.entries(value)) {
        out[key] = serialize(item, seen).value;
      }
      return { kind: "json", value: out };
    }
  }
  return { kind: "repr", value: inspectValue(value) };
}

function detectUpdatedVariables(code, previousCtx, nextCtx) {
  const updated = new Set();
  const patterns = [
    /(?:^|[;\n])\s*(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)/gm,
    /(?:^|[;\n])\s*([A-Za-z_$][\w$]*)\s*(?:\+\+|--|[+\-*/%]?=)(?!=|>)/gm,
    /globalThis\.([A-Za-z_$][\w$]*)\s*(?:\+\+|--|[+\-*/%]?=)(?!=|>)/gm,
  ];

  for (const rx of patterns) {
    for (const match of code.matchAll(rx)) {
      const name = match[1];
      if (name && !blockedNames.includes(name)) updated.add(name);
    }
  }
  if (previousCtx !== nextCtx) {
    updated.add(contextVarName);
  }
  return Array.from(updated);
}

function findMatchingParenStart(source, closeIndex) {
  let depth = 0;
  let quote = null;
  for (let index = closeIndex; index >= 0; index -= 1) {
    const ch = source[index];
    const prev = index > 0 ? source[index - 1] : "";
    if (quote) {
      if (ch === quote && prev !== "\\") {
        quote = null;
      }
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === ")") {
      depth += 1;
      continue;
    }
    if (ch === "(") {
      depth -= 1;
      if (depth === 0) {
        return index;
      }
    }
  }
  return -1;
}

function findTypeTerminator(source, startIndex, terminatorChar) {
  let quote = null;
  const stack = [];
  for (let index = startIndex; index < source.length; index += 1) {
    const ch = source[index];
    const prev = index > startIndex ? source[index - 1] : "";
    if (quote) {
      if (ch === quote && prev !== "\\") {
        quote = null;
      }
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      continue;
    }
    if (ch === "/" && source[index + 1] === "/") {
      const newline = source.indexOf("\n", index + 2);
      if (newline === -1) {
        return -1;
      }
      index = newline;
      continue;
    }
    if (ch === "/" && source[index + 1] === "*") {
      const commentEnd = source.indexOf("*/", index + 2);
      if (commentEnd === -1) {
        return -1;
      }
      index = commentEnd + 1;
      continue;
    }
    if (ch === "(" || ch === "[" || ch === "{" || ch === "<") {
      stack.push(ch);
      continue;
    }
    if (ch === ")" || ch === "]" || ch === "}" || ch === ">") {
      stack.pop();
      continue;
    }
    if (ch === terminatorChar && stack.length === 0) {
      // When looking for '=', skip '=>' (arrow tokens) — they are part of the type.
      if (terminatorChar === "=" && source[index + 1] === ">") {
        index += 1; // skip past '>'
        continue;
      }
      return index;
    }
  }
  return -1;
}

function stripTypeAnnotationsFromParams(paramsSource) {
  let out = "";
  let index = 0;
  let quote = null;
  while (index < paramsSource.length) {
    const ch = paramsSource[index];
    const prev = index > 0 ? paramsSource[index - 1] : "";
    if (quote) {
      out += ch;
      if (ch === quote && prev !== "\\") {
        quote = null;
      }
      index += 1;
      continue;
    }
    if (ch === "'" || ch === '"' || ch === "`") {
      quote = ch;
      out += ch;
      index += 1;
      continue;
    }
    if (ch === ":") {
      let scan = index + 1;
      let nestedQuote = null;
      let parenDepth = 0;
      let bracketDepth = 0;
      let braceDepth = 0;
      let angleDepth = 0;
      while (scan < paramsSource.length) {
        const current = paramsSource[scan];
        const currentPrev = scan > index + 1 ? paramsSource[scan - 1] : "";
        if (nestedQuote) {
          if (current === nestedQuote && currentPrev !== "\\") {
            nestedQuote = null;
          }
          scan += 1;
          continue;
        }
        if (current === "'" || current === '"' || current === "`") {
          nestedQuote = current;
          scan += 1;
          continue;
        }
        if (current === "(") parenDepth += 1;
        else if (current === ")") {
          if (parenDepth === 0 && bracketDepth === 0 && braceDepth === 0 && angleDepth === 0) break;
          parenDepth -= 1;
        } else if (current === "[") bracketDepth += 1;
        else if (current === "]") bracketDepth -= 1;
        else if (current === "{") braceDepth += 1;
        else if (current === "}") braceDepth -= 1;
        else if (current === "<") angleDepth += 1;
        else if (current === ">") angleDepth -= 1;
        else if (current === "=" && parenDepth === 0 && bracketDepth === 0 && braceDepth === 0 && angleDepth === 0) {
          // '=' at depth 0 starts a default value — stop stripping the type here.
          // But skip '=>' which is part of arrow function types.
          if (scan + 1 < paramsSource.length && paramsSource[scan + 1] === ">") {
            scan += 1; // skip past '>'
          } else {
            break;
          }
        } else if (
          (current === "," || current === ")") &&
          parenDepth === 0 &&
          bracketDepth === 0 &&
          braceDepth === 0 &&
          angleDepth === 0
        ) {
          break;
        }
        scan += 1;
      }
      while (scan < paramsSource.length && /\s/.test(paramsSource[scan])) {
        scan += 1;
      }
      index = scan;
      continue;
    }
    out += ch;
    index += 1;
  }
  return out;
}

function stripArrowFunctionTypes(source) {
  let result = source;
  let cursor = 0;
  while (cursor < result.length) {
    const arrowIndex = result.indexOf("=>", cursor);
    if (arrowIndex === -1) {
      break;
    }
    let paramEnd = arrowIndex - 1;
    while (paramEnd >= 0 && /\s/.test(result[paramEnd])) {
      paramEnd -= 1;
    }
    if (paramEnd < 0 || result[paramEnd] !== ")") {
      cursor = arrowIndex + 2;
      continue;
    }
    const paramStart = findMatchingParenStart(result, paramEnd);
    if (paramStart === -1) {
      cursor = arrowIndex + 2;
      continue;
    }
    const params = result.slice(paramStart + 1, paramEnd);
    const strippedParams = stripTypeAnnotationsFromParams(params);
    result =
      result.slice(0, paramStart + 1) +
      strippedParams +
      result.slice(paramEnd);
    const nextArrow = result.indexOf("=>", paramStart);
    cursor = nextArrow === -1 ? result.length : nextArrow + 2;
  }
  return result;
}

function stripVariableDeclarationTypes(source) {
  const declarationPattern = /\b(const|let|var)\s+([A-Za-z_$][\w$]*)\s*:/g;
  let result = "";
  let lastIndex = 0;
  let match;
  while ((match = declarationPattern.exec(source)) !== null) {
    const colonIndex = declarationPattern.lastIndex - 1;
    const typeStart = colonIndex + 1;
    const equalsIndex = findTypeTerminator(source, typeStart, "=");
    if (equalsIndex === -1) {
      continue;
    }
    result += source.slice(lastIndex, colonIndex);
    lastIndex = equalsIndex;
    declarationPattern.lastIndex = equalsIndex;
  }
  result += source.slice(lastIndex);
  return result;
}

function stripTypeScriptFallback(code) {
  let result = String(code);
  result = stripArrowFunctionTypes(result);
  result = stripVariableDeclarationTypes(result);
  return result;
}

function normalizeCode(code, language) {
  if (language === "typescript") {
    if (
      process.env.ALEPH_NODE_FORCE_TS_FALLBACK === "true" ||
      typeof stripTypeScriptTypes !== "function"
    ) {
      return stripTypeScriptFallback(code);
    }
    return stripTypeScriptTypes(code);
  }
  return code;
}

function isTopLevelAwaitSyntaxError(error) {
  if (!(error instanceof SyntaxError)) {
    return false;
  }
  const message = String(error.message || "");
  return /await is only valid|Unexpected reserved word/i.test(message);
}

function rewriteTopLevelDeclarationsForAsync(code) {
  return String(code)
    .replace(/(^|[;\n]\s*)(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/g, "$1globalThis.$2 =")
    .replace(/(^|[;\n]\s*)async\s+function\s+([A-Za-z_$][\w$]*)\s*\(/g, "$1globalThis.$2 = async function $2(")
    .replace(/(^|[;\n]\s*)function\s+([A-Za-z_$][\w$]*)\s*\(/g, "$1globalThis.$2 = function $2(")
    .replace(/(^|[;\n]\s*)class\s+([A-Za-z_$][\w$]*)\b/g, "$1globalThis.$2 = class $2");
}

function findLastExpression(code) {
  const candidates = [0];
  for (let i = 0; i < code.length; i += 1) {
    if (code[i] === ";" || code[i] === "\n") {
      candidates.push(i + 1);
    }
  }

  for (let index = candidates.length - 1; index >= 0; index -= 1) {
    const start = candidates[index];
    const prefix = code.slice(0, start);
    const rawCandidate = code.slice(start).trim();
    const candidate = rawCandidate.replace(/;+\s*$/, "").trim();
    if (!candidate) {
      continue;
    }
    try {
      new vm.Script(`(async () => { return (${candidate}); })()`, { displayErrors: true });
      return { prefix, expression: candidate };
    } catch {
      // Keep looking for a parseable expression boundary.
    }
  }

  return { prefix: code, expression: null };
}

function buildAsyncWrappedCode(code) {
  const transformed = rewriteTopLevelDeclarationsForAsync(code);
  const { prefix, expression } = findLastExpression(transformed);
  let body = prefix.trimEnd();
  if (expression) {
    body = body ? `${body}\nreturn (${expression});` : `return (${expression});`;
  }
  return `(async () => { with (globalThis) {\n${body}\n} })()`;
}

function runCodeInContext(code, timeoutMs) {
  const script = new vm.Script(code, { displayErrors: true });
  return script.runInContext(context, {
    timeout: timeoutMs,
    displayErrors: true,
  });
}

async function executeCode(code, timeoutMs) {
  try {
    return runCodeInContext(code, timeoutMs);
  } catch (error) {
    if (!isTopLevelAwaitSyntaxError(error) || !/\bawait\b/.test(code)) {
      throw error;
    }
    return runCodeInContext(buildAsyncWrappedCode(code), timeoutMs);
  }
}

const sandbox = {
  [contextVarName]: "",
  line_number_base: lineNumberBase,
};

sandbox.globalThis = sandbox;
sandbox.console = {
  log: (...args) => stdoutBuffer.push(args.map(inspectValue).join(" ")),
  info: (...args) => stdoutBuffer.push(args.map(inspectValue).join(" ")),
  warn: (...args) => stderrBuffer.push(args.map(inspectValue).join(" ")),
  error: (...args) => stderrBuffer.push(args.map(inspectValue).join(" ")),
};

const contextHelpers = {
  peek: (value, start = 0, end = null) => peekImpl(value, start, end),
  lines: (value, start = 0, end = null) => linesImpl(value, start, end),
  search: (value, pattern, contextLines = 2, flags = "", maxResults = 20) =>
    searchImpl(value, pattern, contextLines, flags, maxResults),
  chunk: (value, chunkSize, overlap = 0) => chunkImpl(value, chunkSize, overlap),
  extract_numbers: (value, includeNegative = true, includeDecimals = true) =>
    extractNumbersImpl(value, includeNegative, includeDecimals),
  extract_money: (value, currencies = "[$€£¥₹]") => extractMoneyImpl(value, currencies),
  extract_percentages: (value) => extractPercentagesImpl(value),
  extract_dates: (value) => extractDatesImpl(value),
  extract_times: (value) => extractTimesImpl(value),
  extract_timestamps: (value) => extractTimestampsImpl(value),
  extract_emails: (value) => extractEmailsImpl(value),
  extract_urls: (value) => extractUrlsImpl(value),
  extract_ips: (value, includeIpv6 = false) => extractIpsImpl(value, includeIpv6),
  extract_phones: (value) => extractPhonesImpl(value),
  extract_hex: (value) => extractHexImpl(value),
  extract_uuids: (value) => extractUuidsImpl(value),
  extract_paths: (value) => extractPathsImpl(value),
  extract_env_vars: (value) => extractEnvVarsImpl(value),
  extract_versions: (value) => extractVersionsImpl(value),
  extract_hashes: (value) => extractHashesImpl(value),
  extract_functions: (value, lang = "python") => extractFunctionsImpl(value, lang),
  extract_classes: (value, lang = "python") => extractClassesImpl(value, lang),
  extract_imports: (value, lang = "python") => extractImportsImpl(value, lang),
  extract_comments: (value, lang = "python") => extractCommentsImpl(value, lang),
  extract_routes: (value, lang = "auto") => extractRoutesImpl(value, lang),
  extract_strings: (value) => extractStringsImpl(value),
  extract_todos: (value) => extractTodosImpl(value),
  extract_log_levels: (value) => extractLogLevelsImpl(value),
  extract_exceptions: (value) => extractExceptionsImpl(value),
  extract_json_objects: (value) => extractJsonObjectsImpl(value),
  word_count: (value) => wordCountImpl(value),
  char_count: (value, includeWhitespace = true) => charCountImpl(value, includeWhitespace),
  line_count: (value) => lineCountImpl(value),
  sentence_count: (value) => sentenceCountImpl(value),
  paragraph_count: (value) => paragraphCountImpl(value),
  unique_words: (value, caseInsensitive = true) => uniqueWordsImpl(value, caseInsensitive),
  word_frequency: (value, topN = 20, caseInsensitive = true) => wordFrequencyImpl(value, topN, caseInsensitive),
  ngrams: (value, n = 2, topK = 20) => ngramsImpl(value, n, topK),
  head: (value, n = 10) => headImpl(value, n),
  tail: (value, n = 10) => tailImpl(value, n),
  grep: (value, pattern, flags = "") => grepImpl(value, pattern, flags),
  grep_v: (value, pattern, flags = "") => grepVImpl(value, pattern, flags),
  grep_c: (value, pattern, flags = "") => grepCImpl(value, pattern, flags),
  uniq: (value) => uniqImpl(value),
  sort_lines: (value, reverse = false, numeric = false) => sortLinesImpl(value, reverse, numeric),
  number_lines: (value, start = 1) => numberLinesImpl(value, start),
  strip_lines: (value) => stripLinesImpl(value),
  blank_lines: (value) => blankLinesImpl(value),
  non_blank_lines: (value) => nonBlankLinesImpl(value),
  columns: (value, col, delim = "\\s+") => columnsImpl(value, col, delim),
  replace_all: (value, pattern, replacement, flags = "") => replaceAllImpl(value, pattern, replacement, flags),
  split_by: (value, pattern, flags = "") => splitByImpl(value, pattern, flags),
  between: (value, startPattern, endPattern, includeMarkers = false) =>
    betweenImpl(value, startPattern, endPattern, includeMarkers),
  before: (value, pattern, flags = "") => beforeImpl(value, pattern, flags),
  after: (value, pattern, flags = "") => afterImpl(value, pattern, flags),
  truncate: (value, maxChars = 200, suffix = "...") => truncateImpl(value, maxChars, suffix),
  wrap_text: (value, width = 80) => wrapTextImpl(value, width),
  indent_text: (value, prefix = "  ") => indentTextImpl(value, prefix),
  dedent_text: (value) => dedentTextImpl(value),
  normalize_whitespace: (value) => normalizeWhitespaceImpl(value),
  remove_punctuation: (value) => removePunctuationImpl(value),
  contains: (value, pattern, flags = "") => containsImpl(value, pattern, flags),
  contains_any: (value, patterns, flags = "") => containsAnyImpl(value, patterns, flags),
  contains_all: (value, patterns, flags = "") => containsAllImpl(value, patterns, flags),
  count_matches: (value, pattern, flags = "") => countMatchesImpl(value, pattern, flags),
  find_all: (value, pattern, flags = "", maxResults = 100) => findAllImpl(value, pattern, flags, maxResults),
  first_match: (value, pattern, flags = "") => firstMatchImpl(value, pattern, flags),
  semantic_search: (value, query, chunkSize = 1000, overlap = 100, topK = 5, embedDim = 256) =>
    semanticSearchImpl(value, query, chunkSize, overlap, topK, embedDim),
  to_lower: (value) => toLowerImpl(value),
  to_upper: (value) => toUpperImpl(value),
  to_title: (value) => toTitleImpl(value),
};

for (const [name, fn] of Object.entries(contextHelpers)) {
  sandbox[name] = (...args) => fn(sandbox[contextVarName], ...args);
}

sandbox.ctx_append = (text) => {
  sandbox[contextVarName] = toText(sandbox[contextVarName]) + String(text);
  return sandbox[contextVarName];
};
sandbox.ctx_set = (text) => {
  sandbox[contextVarName] = String(text);
  return sandbox[contextVarName];
};
sandbox.cite = citeImpl;
sandbox.blocked_names = () => Array.from(blockedNames);
sandbox.diff = (left, right, contextLines = 3) => diffImpl(left, right, contextLines);
sandbox.similarity = (left, right) => similarityImpl(left, right);
sandbox.common_lines = (left, right) => commonLinesImpl(left, right);
sandbox.diff_lines = (left, right) => diffLinesImpl(left, right);
sandbox.embed_text = (text, dim = 256) => embedTextImpl(text, dim);
sandbox.dedupe = (items) => dedupeImpl(items);
sandbox.flatten = (nested, depth = -1) => flattenImpl(nested, depth);
sandbox.first = (items, defaultValue = null) => firstImpl(items, defaultValue);
sandbox.last = (items, defaultValue = null) => lastImpl(items, defaultValue);
sandbox.take = (count, items) => takeImpl(count, items);
sandbox.drop = (count, items) => dropImpl(count, items);
sandbox.partition = (items, predicate) => partitionImpl(items, predicate);
sandbox.group_by = (items, keyFn) => groupByImpl(items, keyFn);
sandbox.frequency = (items, topN = null) => frequencyImpl(items, topN);
sandbox.sample_items = (items, count, seed = null) => sampleItemsImpl(items, count, seed);
sandbox.shuffle_items = (items, seed = null) => shuffleItemsImpl(items, seed);
sandbox.is_numeric = (text) => isNumericImpl(text);
sandbox.is_email = (text) => isEmailImpl(text);
sandbox.is_url = (text) => isUrlImpl(text);
sandbox.is_ip = (text) => isIpImpl(text);
sandbox.is_uuid = (text) => isUuidImpl(text);
sandbox.is_json = (text) => isJsonImpl(text);
sandbox.is_blank = (text) => isBlankImpl(text);
sandbox.to_json = (value, indent = 2) => toJsonImpl(value, indent);
sandbox.from_json = (text) => fromJsonImpl(text);
sandbox.to_csv_row = (items, delim = ",") => toCsvRowImpl(items, delim);
sandbox.from_csv_row = (text, delim = ",") => fromCsvRowImpl(text, delim);
sandbox.to_int = (text, defaultValue = 0) => toIntImpl(text, defaultValue);
sandbox.to_float = (text, defaultValue = 0.0) => toFloatImpl(text, defaultValue);
sandbox.to_snake_case = (text) => toSnakeCaseImpl(text);
sandbox.to_camel_case = (text) => toCamelCaseImpl(text);
sandbox.to_pascal_case = (text) => toPascalCaseImpl(text);
sandbox.to_kebab_case = (text) => toKebabCaseImpl(text);
sandbox.slugify = (text) => slugifyImpl(text);
sandbox.RecipeStep = RecipeStep;
sandbox.RecipeBuilder = RecipeBuilder;
sandbox.Recipe = Recipe;
sandbox.Step = Step;
sandbox.Search = Search;
sandbox.Peek = Peek;
sandbox.Lines = Lines;
sandbox.Take = Take;
sandbox.Chunk = Chunk;
sandbox.Filter = Filter;
sandbox.MapSubQuery = MapSubQuery;
sandbox.SubQuery = SubQuery;
sandbox.Aggregate = Aggregate;
sandbox.Assign = Assign;
sandbox.Load = Load;
sandbox.Finalize = Finalize;
sandbox.as_recipe = asRecipe;
sandbox.RECIPE_DSL_VERSION = RECIPE_DSL_VERSION;
sandbox.sub_query = (prompt, contextSlice = null) => callHost("sub_query", [prompt, contextSlice]);
sandbox.sub_query_map = (prompts, contextSlices = null, limit = null, parallel = true) =>
  callHost("sub_query_map", [prompts], { context_slices: contextSlices, limit, parallel });
sandbox.sub_query_batch = (prompt, contextSlices, limit = null) =>
  callHost("sub_query_batch", [prompt, contextSlices], { limit });
sandbox.sub_query_strict = (
  prompt,
  contextSlice = null,
  validateRegex = null,
  maxRetries = 0,
  retryPrompt = null
) =>
  callHost("sub_query_strict", [prompt], {
    context_slice: contextSlice,
    validate_regex: validateRegex,
    max_retries: maxRetries,
    retry_prompt: retryPrompt,
  });
sandbox.sub_aleph = (query, contextValue = null) => callHost("sub_aleph", [query, contextValue]);
sandbox.get_config = () => callHost("get_config");
sandbox.set_backend = (backend) => callHost("set_backend", [backend]);
sandbox.require = undefined;
sandbox.process = undefined;
sandbox.module = undefined;
sandbox.exports = undefined;
sandbox.eval = undefined;
sandbox.Function = undefined;

const context = vm.createContext(sandbox, {
  codeGeneration: {
    strings: false,
    wasm: false,
  },
});

async function handleRequest(request) {
  const id = request.id;
  try {
    if (request.op === "sync_context") {
      sandbox[contextVarName] = String(request.context || "");
      lineNumberBase = Number(request.line_number_base || 1);
      sandbox.line_number_base = lineNumberBase;
      send({ id, ok: true });
      return;
    }

    if (request.op === "set_variable") {
      const name = String(request.name || "");
      if (!/^[A-Za-z_$][\w$]*$/.test(name)) {
        throw new Error(`Invalid variable name: ${name}`);
      }
      sandbox[name] = request.value;
      send({ id, ok: true });
      return;
    }

    if (request.op === "get_variable") {
      const name = String(request.name || "");
      if (!/^[A-Za-z_$][\w$]*$/.test(name)) {
        throw new Error(`Invalid variable name: ${name}`);
      }
      try {
        const value = new vm.Script(name).runInContext(context, { timeout: 1000 });
        send({ id, ok: true, found: true, value: serialize(value) });
      } catch (error) {
        if (error && error.name === "ReferenceError") {
          send({ id, ok: true, found: false });
          return;
        }
        throw error;
      }
      return;
    }

    if (request.op === "exec") {
      stdoutBuffer = [];
      stderrBuffer = [];
      evidenceBuffer = [];

      const beforeCtx = toText(sandbox[contextVarName]);
      const code = normalizeCode(String(request.code || ""), String(request.language || "javascript"));
      const timeoutMs = Number(request.timeout_ms || 180000);
      const start = Date.now();

      let result = await executeCode(code, timeoutMs);
      if (result && typeof result.then === "function") {
        result = await Promise.race([
          result,
          new Promise((_, reject) =>
            setTimeout(() => reject(new Error(`Code execution exceeded ${timeoutMs}ms timeout`)), timeoutMs)
          ),
        ]);
      }

      const afterCtx = toText(sandbox[contextVarName]);
      send({
        id,
        ok: true,
        stdout: stdoutBuffer.join("\n"),
        stderr: stderrBuffer.join("\n"),
        return_value: serialize(result),
        variables_updated: detectUpdatedVariables(code, beforeCtx, afterCtx),
        context: serialize(afterCtx),
        citations: evidenceBuffer,
        execution_time_ms: Date.now() - start,
      });
      return;
    }

    if (request.op === "close") {
      send({ id, ok: true });
      process.exit(0);
    }

    throw new Error(`Unsupported operation: ${request.op}`);
  } catch (error) {
    send({
      id,
      ok: false,
      stdout: stdoutBuffer.join("\n"),
      stderr: stderrBuffer.join("\n"),
      error: error && error.stack ? String(error.stack) : String(error),
      context: serialize(toText(sandbox[contextVarName])),
      citations: evidenceBuffer,
      execution_time_ms: 0,
    });
  }
}

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

rl.on("line", (line) => {
  if (!line.trim()) return;
  let request;
  try {
    request = JSON.parse(line);
  } catch (error) {
    send({ ok: false, error: `Invalid JSON request: ${error}` });
    return;
  }

  if (request.op === "callback_response") {
    settleCallbackResponse(request);
    return;
  }

  void handleRequest(request);
});

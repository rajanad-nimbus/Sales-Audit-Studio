/** Turn machine keys and JSON values into plain words for people. */

type Obj = Record<string, unknown>;
const isObj = (v: unknown): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);

const ACRONYMS: Record<string, string> = {
  id: 'ID',
  ids: 'IDs',
  usd: 'USD',
  url: 'URL',
  api: 'API',
  sku: 'SKU',
  ach: 'ACH',
  pos: 'POS',
  llm: 'LLM',
  ui: 'UI',
  sla: 'SLA',
  csv: 'CSV',
  json: 'JSON',
  gl: 'GL',
  upc: 'UPC',
};

export function humanizeKey(key: string | null | undefined): string {
  // Missing values must not take a whole page down.
  if (key == null) return '';
  const words = String(key)
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_\-.]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((w) => ACRONYMS[w.toLowerCase()] ?? w.toLowerCase());
  if (!words.length) return key;
  const first = words[0];
  words[0] = first === first.toUpperCase() ? first : first[0].toUpperCase() + first.slice(1);
  return words.join(' ');
}

/** One-line, human-readable summary of a value, for table cells and CSV. */
export function summarizeValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return 'Not set';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (Array.isArray(value)) return value.map(summarizeValue).join(', ');
  if (isObj(value)) {
    return Object.entries(value)
      .map(
        ([k, v]) => `${humanizeKey(k)}: ${isObj(v) ? `(${summarizeValue(v)})` : summarizeValue(v)}`
      )
      .join('; ');
  }
  return String(value);
}

/**
 * FastAPI answers bad input with a list of {loc, msg} items. Turn that list
 * into one sentence people can act on, e.g. "Column mapping: Field required".
 */
export function describeValidationErrors(detail: unknown[]): string {
  return detail
    .map((item) => {
      if (!isObj(item)) return summarizeValue(item);
      if (typeof item.message === 'string') {
        return item.row != null ? `Row ${String(item.row)}: ${item.message}` : item.message;
      }
      const msg = typeof item.msg === 'string' ? item.msg.replace(/^Value error, /, '') : null;
      const loc = Array.isArray(item.loc)
        ? item.loc.filter(
            (p) => p !== 'body' && p !== 'query' && p !== 'path' && typeof p === 'string'
          )
        : [];
      const where = loc.length ? humanizeKey(String(loc[loc.length - 1])) : '';
      if (msg) return where ? `${where}: ${msg}` : msg;
      return summarizeValue(item);
    })
    .join('; ');
}

/** Indented "Label: value" text of a payload, for copying into notes or email. */
export function toPlainText(value: unknown, indent = ''): string {
  if (Array.isArray(value)) {
    return value
      .map((v) =>
        isObj(v)
          ? `${indent}-\n${toPlainText(v, indent + '  ')}`
          : `${indent}- ${summarizeValue(v)}`
      )
      .join('\n');
  }
  if (isObj(value)) {
    return Object.entries(value)
      .map(([k, v]) =>
        isObj(v) || (Array.isArray(v) && v.length)
          ? `${indent}${humanizeKey(k)}:\n${toPlainText(v, indent + '  ')}`
          : `${indent}${humanizeKey(k)}: ${summarizeValue(v)}`
      )
      .join('\n');
  }
  return `${indent}${summarizeValue(value)}`;
}

/** Audit error codes such as TENDER_NOT_FOUND, read as "Tender not found". */
const CODE_WORDS: Record<string, string> = {
  ORIG: 'original',
  NOSALE: 'no sale',
  QTY: 'quantity',
  AMT: 'amount',
  TXN: 'transaction',
  TRAN: 'transaction',
  REG: 'register',
  DEPT: 'department',
  DUP: 'duplicate',
  EXCESS: 'excess',
};
export const codeLabel = (code: string | null | undefined) =>
  code
    ? humanizeKey(
        code
          .split('_')
          .map((w) => CODE_WORDS[w.toUpperCase()] ?? w)
          .join('_')
      )
    : '';

const DATASET_LABELS: Record<string, string> = {
  audited_transactions: 'Audited transactions',
  audited_totals: 'Audited totals',
  source_records: 'Source records',
  audit_events: 'Audit trail events',
};
export const datasetLabel = (dataset: string | null | undefined) =>
  dataset ? (DATASET_LABELS[dataset] ?? humanizeKey(dataset)) : '';

const SOURCE_LABELS: Record<string, string> = {
  ManualAdjustment: 'Manual correction',
  POSControl: 'POS control',
  OMSControl: 'OMS control',
  POS: 'POS',
  OMS: 'OMS',
  ERP: 'ERP',
};
export const sourceLabel = (source: string | null | undefined) =>
  source ? (SOURCE_LABELS[source] ?? source) : '';

const FRESHNESS_LABELS: Record<string, string> = {
  fresh: 'Fresh',
  overdue: 'Overdue',
  never: 'Never received',
  unknown: 'Unknown',
  disabled: 'Disabled',
};
export const freshnessLabel = (state: string) => FRESHNESS_LABELS[state] ?? humanizeKey(state);

const TRIGGER_LABELS: Record<string, string> = { api: 'Manual', schedule: 'Scheduled' };
export const triggerLabel = (trigger: string | null | undefined) =>
  trigger ? (TRIGGER_LABELS[trigger] ?? humanizeKey(trigger)) : '';

/** "1 case" / "2 cases". */
export const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

/**
 * Backend notes still say "2 open case(s)" or "1 marker(s)". Resolve each "(s)" from the
 * number before it so the sentence reads naturally.
 */
export const tidyPlurals = (text: string) =>
  text.replace(/\b(\d[\d,]*)((?:\s+[\w-]+){1,4}?)\(s\)/g, (_m, n: string, words: string) =>
    Number(n.replace(/,/g, '')) === 1 ? `${n}${words}` : `${n}${words}s`
  );

const KEEP_CASE = new Set(['POS', 'OMS', 'ERP', 'GL', 'ACH', 'ID', 'SKU', 'Shopify', 'Oracle', 'Ontology', 'Studio', 'Finance', 'IT']);

/**
 * Status text arrives in mixed styles ("Sales Audit In Progress", "Store Errors Pending").
 * Show every status in sentence case, keeping acronyms and product names.
 */
export function statusLabel(text: string): string {
  const words = text.split(' ');
  if (words.length < 2) return text;
  return words
    .map((w, i) => {
      if (i === 0 || KEEP_CASE.has(w) || /^[A-Z0-9]{2,}$/.test(w) || /\d/.test(w)) return w;
      return /^[A-Z][a-z]+$/.test(w) ? w.toLowerCase() : w;
    })
    .join(' ');
}

/** Free text from the backend sometimes names error codes ("Raised TENDER_TOTAL_MISMATCH"); spell them out. */
export const humanizeCodesInText = (text: string) =>
  text.replace(/\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b/g, (code) => codeLabel(code).toLowerCase());

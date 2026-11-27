'use client';

import { Fragment, type ReactNode } from 'react';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { CopyButton } from '@/components/Feedback';
import { codeLabel, humanizeKey, summarizeValue } from '@/lib/humanize';

export { humanizeKey, summarizeValue };

/*
 * Renders any JSON-like payload as labelled fields instead of raw JSON.
 * Objects become field lists, arrays of codes become chips, uniform arrays
 * of objects become small tables, and {before, after} pairs become a list
 * of changes. The raw payload stays available behind a disclosure.
 */

type Json = unknown;
type Obj = Record<string, Json>;

const isObj = (v: Json): v is Obj => typeof v === 'object' && v !== null && !Array.isArray(v);
const isPrimitive = (v: Json) => v === null || v === undefined || typeof v !== 'object';

const ISO_DATE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const HASH = /^[a-f0-9]{24,}$/i;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SNAKE = /^[a-z]+(_[a-z0-9]+)+$/;
const SCREAMING = /^[A-Z]+(_[A-Z0-9]+)+$/;
const CODE = /^[A-Z0-9][A-Z0-9_\-.:]{1,}$/;
const CODE_LIST = /^(.{3,80}?):\s*([A-Z0-9][A-Z0-9_\-.]+(?:,\s*[A-Z0-9][A-Z0-9_\-.]+)+)\.?$/;
const MONEY_KEY = /(amount|total|cost|price|value|variance|over_short|balance)(_usd)?$/i;

function Hash({ value }: { value: string }) {
  return (
    <span className="sv-hash" title={value}>
      <code>
        {value.slice(0, 10)}…{value.slice(-6)}
      </code>
      <CopyButton value={value} label="Copy" />
    </span>
  );
}

function Chips({ items }: { items: string[] }) {
  return (
    <span className="sv-chips">
      {items.map((c, i) => (
        <span key={`${c}-${i}`} className="sv-chip">
          {c}
        </span>
      ))}
    </span>
  );
}

function Scalar({ value, field }: { value: Json; field?: string }): ReactNode {
  if (value === null || value === undefined || value === '') {
    return <span className="sv-empty">Not set</span>;
  }
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (field && /(^|_)id$|Id$|seq$|year$/.test(field)) return String(value);
    if (field && MONEY_KEY.test(field) && !Number.isInteger(value)) {
      return formatNumber(value, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    return formatNumber(value);
  }
  const s = String(value);
  if (ISO_DATE.test(s) && !Number.isNaN(Date.parse(s))) {
    return (
      <time dateTime={s}>{formatDateTime(s, { dateStyle: 'medium', timeStyle: 'short' })}</time>
    );
  }
  if (HASH.test(s) || UUID.test(s)) return <Hash value={s} />;
  if (SNAKE.test(s)) return <span title={s}>{humanizeKey(s)}</span>;
  if (SCREAMING.test(s)) return <span title={s}>{codeLabel(s)}</span>;
  const listed = s.match(CODE_LIST);
  if (listed) {
    return (
      <span className="sv-listed">
        <span>{listed[1]}</span>
        <Chips items={listed[2].split(/,\s*/)} />
      </span>
    );
  }
  return s;
}

function uniformKeys(rows: Json[]): string[] | null {
  if (rows.length < 2 || !rows.every(isObj)) return null;
  const keys = Object.keys(rows[0] as Obj);
  if (keys.length === 0 || keys.length > 6) return null;
  const same = rows.every((r) => {
    const k = Object.keys(r as Obj);
    return k.length === keys.length && k.every((x) => keys.includes(x));
  });
  if (!same) return null;
  return rows.every((r) => keys.every((k) => isPrimitive((r as Obj)[k]))) ? keys : null;
}

function ArrayValue({ items, field, depth }: { items: Json[]; field?: string; depth: number }) {
  if (items.length === 0) return <span className="sv-empty">None</span>;
  if (items.every((x) => typeof x === 'string' && CODE.test(x) && x.length <= 40)) {
    return <Chips items={items as string[]} />;
  }
  if (items.every(isPrimitive)) {
    if (items.length === 1) return <Scalar value={items[0]} field={field} />;
    return (
      <ul className="sv-list">
        {items.map((x, i) => (
          <li key={i}>
            <Scalar value={x} field={field} />
          </li>
        ))}
      </ul>
    );
  }
  const keys = uniformKeys(items);
  if (keys) {
    return (
      <div className="sv-table-wrap">
        <table className="sv-table">
          <thead>
            <tr>
              {keys.map((k) => (
                <th key={k}>{humanizeKey(k)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {items.map((r, i) => (
              <tr key={i}>
                {keys.map((k) => (
                  <td key={k}>
                    <Scalar value={(r as Obj)[k]} field={k} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <ol className="sv-stack">
      {items.map((x, i) => (
        <li key={i}>
          <Value value={x} field={field} depth={depth + 1} />
        </li>
      ))}
    </ol>
  );
}

function isChangePair(v: Json): v is { before: Json; after: Json } {
  return (
    isObj(v) &&
    'before' in v &&
    'after' in v &&
    Object.keys(v).length <= 3 &&
    (isObj(v.before) || isObj(v.after) || v.before === null)
  );
}

function Changes({ before, after }: { before: Json; after: Json }) {
  const b = isObj(before) ? before : {};
  const a = isObj(after) ? after : {};
  const keys = Array.from(new Set([...Object.keys(b), ...Object.keys(a)]));
  const changed = keys.filter((k) => JSON.stringify(b[k]) !== JSON.stringify(a[k]));
  const shown = changed.length ? changed : keys;
  if (!shown.length) return <span className="sv-empty">No changes recorded</span>;
  return (
    <div className="sv-table-wrap">
      <table className="sv-table sv-changes">
        <thead>
          <tr>
            <th>Field</th>
            <th>Before</th>
            <th>After</th>
          </tr>
        </thead>
        <tbody>
          {shown.map((k) => (
            <tr key={k}>
              <th scope="row">{humanizeKey(k)}</th>
              <td className="sv-before">
                {k in b ? (
                  <Value value={b[k]} field={k} depth={2} />
                ) : (
                  <span className="sv-empty">Not set</span>
                )}
              </td>
              <td>
                {k in a ? (
                  <Value value={a[k]} field={k} depth={2} />
                ) : (
                  <span className="sv-empty">Removed</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Fields({ obj, depth }: { obj: Obj; depth: number }) {
  const entries = Object.entries(obj).filter(([, v]) => v !== undefined);
  if (!entries.length) return <span className="sv-empty">No details</span>;
  return (
    <dl className={`sv-fields${depth > 0 ? ' nested' : ''}`}>
      {entries.map(([k, v]) => {
        const block =
          (isObj(v) && Object.keys(v).length > 0) ||
          (Array.isArray(v) && v.some((x) => !isPrimitive(x)));
        return (
          <Fragment key={k}>
            <dt className={block ? 'block' : undefined}>{humanizeKey(k)}</dt>
            <dd className={block ? 'block' : undefined}>
              <Value value={v} field={k} depth={depth + 1} />
            </dd>
          </Fragment>
        );
      })}
    </dl>
  );
}

function Value({ value, field, depth }: { value: Json; field?: string; depth: number }): ReactNode {
  if (Array.isArray(value)) return <ArrayValue items={value} field={field} depth={depth} />;
  if (isChangePair(value)) return <Changes before={value.before} after={value.after} />;
  if (isObj(value)) {
    if (Object.keys(value).length === 0) return <span className="sv-empty">None</span>;
    return <Fields obj={value} depth={depth} />;
  }
  if (typeof value === 'string' && /^[[{]/.test(value.trim())) {
    try {
      return <Value value={JSON.parse(value)} field={field} depth={depth} />;
    } catch {
      /* plain string */
    }
  }
  return <Scalar value={value} field={field} />;
}

export function StructuredValue({
  value,
  raw = false,
  rawLabel = 'Show raw data',
  className,
}: {
  value: Json;
  /** Opt in to offer the original JSON behind a disclosure (developer-facing screens only). */
  raw?: boolean;
  rawLabel?: string;
  className?: string;
}) {
  const text = (() => {
    try {
      return JSON.stringify(value, null, 2);
    } catch {
      return String(value);
    }
  })();
  return (
    <div className={`sv${className ? ` ${className}` : ''}`}>
      <Value value={value} depth={0} />
      {raw && !isPrimitive(value) && (
        <details className="sv-raw">
          <summary>{rawLabel}</summary>
          <div className="sv-raw-body">
            <CopyButton value={text} label="Copy JSON" />
            <pre>{text}</pre>
          </div>
        </details>
      )}
    </div>
  );
}

/** A plain table of records (e.g. a sample of export rows), one column per key. */
export function RecordTable({ rows, maxRows = 20 }: { rows: Json[]; maxRows?: number }) {
  const records = rows.filter(isObj).slice(0, maxRows);
  if (!records.length) return <span className="sv-empty">No rows</span>;
  const keys = Array.from(new Set(records.flatMap((r) => Object.keys(r))));
  return (
    <div className="sv-table-wrap sv-records">
      <table className="sv-table">
        <thead>
          <tr>
            {keys.map((k) => (
              <th key={k}>{humanizeKey(k)}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {records.map((r, i) => (
            <tr key={i}>
              {keys.map((k) => (
                <td key={k}>
                  {isPrimitive(r[k]) ? <Scalar value={r[k]} field={k} /> : summarizeValue(r[k])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

import { Fragment } from 'react';
import { Button } from './Button';
import { EmptyState } from './EmptyState';
import { SkeletonRows } from './Skeleton';
import { downloadCsv } from '@/lib/csv';

export interface Column<T> {
  key: string;
  header: string;
  render: (row: T) => React.ReactNode;
  /** Right-align and use tabular figures. */
  numeric?: boolean;
  /** Emphasise as the row's main identifier. */
  primary?: boolean;
  /** Makes the header a sort button; the value is passed to `onSort`. */
  sortKey?: string;
  /** Keep clicks in this cell from triggering `onRowClick` (links, buttons). */
  stopRowClick?: boolean;
  nowrap?: boolean;
  width?: string;
  /** Plain export value for CSV; never inferred from rendered markup. */
  csvValue?: (row: T) => string | number | boolean | null | undefined;
  /** Excludes action-only columns from CSV without changing their on-screen rendering. */
  csvSkip?: boolean;
}

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

interface DataTableProps<T> {
  columns: Column<T>[];
  /** `null` while loading. */
  rows: T[] | null;
  rowKey: (row: T) => string;
  /** Click or Enter on a row. Rows become focusable links. */
  onRowClick?: (row: T) => void;
  selectedKey?: string | null;
  sort?: SortState | null;
  onSort?: (sortKey: string) => void;
  emptyTitle?: string;
  emptyHint?: React.ReactNode;
  /** The last load failed. With no rows, the body says so instead of claiming nothing matched. */
  failed?: boolean;
  /** Rendered under the table inside the same card, for example a `Pager`. */
  footer?: React.ReactNode;
  loadingRows?: number;
  /** Extra classes for a row, for example an `open` highlight. */
  rowClassName?: (row: T) => string;
  /** Key of the row whose detail is open; `renderDetail` fills the full-width row below it. */
  expandedKey?: string | null;
  renderDetail?: (row: T) => React.ReactNode;
  detailClassName?: string;
  /** Enables CSV export for the rows currently shown in this table. */
  csvFileName?: string;
}

/** Standard table: loading skeleton, empty state, sortable headers, clickable rows. */
export function DataTable<T>({
  columns,
  rows,
  rowKey,
  onRowClick,
  selectedKey,
  sort,
  onSort,
  emptyTitle = 'Nothing to show',
  emptyHint,
  failed,
  footer,
  loadingRows,
  rowClassName,
  expandedKey,
  renderDetail,
  detailClassName,
  csvFileName,
}: DataTableProps<T>) {
  if (rows === null) return <SkeletonRows rows={loadingRows} />;
  const csvColumns = columns.filter((column) => !column.csvSkip);
  const canExport =
    !!csvFileName && csvColumns.length > 0 && csvColumns.every((column) => column.csvValue);
  return (
    <div className="table-container data-table-surface" data-row-count={rows.length}>
      {rows.length > 0 && (
        <div className="data-table-export">
          <span>
            {rows.length} visible row{rows.length === 1 ? '' : 's'}
          </span>
          <span className="data-table-export-actions">
            {canExport && (
              <Button
                size="sm"
                aria-label={`Download CSV of ${rows.length} visible rows`}
                onClick={() =>
                  downloadCsv(
                    csvFileName!,
                    csvColumns.map((column) => column.header),
                    rows.map((row) => csvColumns.map((column) => column.csvValue!(row)))
                  )
                }
              >
                Download visible CSV
              </Button>
            )}
          </span>
        </div>
      )}
      <div className="data-table-scroll" role="region" aria-label="Table results" tabIndex={0}>
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((c) => {
                const active = sort?.key === c.sortKey;
                return (
                  <th
                    key={c.key}
                    scope="col"
                    className={c.numeric ? 'num' : ''}
                    style={c.width ? { width: c.width } : undefined}
                    aria-sort={
                      c.sortKey && onSort
                        ? active
                          ? sort!.dir === 'asc'
                            ? 'ascending'
                            : 'descending'
                          : 'none'
                        : undefined
                    }
                  >
                    {c.sortKey && onSort ? (
                      <button
                        className="ac-th table-sort"
                        onClick={() => onSort(c.sortKey!)}
                        title={`Sort by ${c.header}`}
                      >
                        {c.header}
                        <span
                          className={`table-sort-icon${active ? ' active' : ''}`}
                          aria-hidden="true"
                        >
                          {active ? (sort!.dir === 'asc' ? '↑' : '↓') : '↕'}
                        </span>
                      </button>
                    ) : (
                      c.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={columns.length}>
                  {failed ? (
                    <EmptyState title="Results could not be loaded">
                      <p>
                        Nothing is listed because the request failed, not because of your filters.
                      </p>
                    </EmptyState>
                  ) : (
                    <EmptyState title={emptyTitle}>{emptyHint}</EmptyState>
                  )}
                </td>
              </tr>
            )}
            {rows.map((r) => {
              const k = rowKey(r);
              const open = expandedKey === k;
              const cls = [
                onRowClick ? 'row-link' : '',
                k === selectedKey ? 'selected' : '',
                rowClassName?.(r) ?? '',
              ]
                .filter(Boolean)
                .join(' ');
              return (
                <Fragment key={k}>
                  <tr
                    className={cls}
                    data-csv={
                      canExport
                        ? JSON.stringify(csvColumns.map((column) => column.csvValue!(r)))
                        : undefined
                    }
                    tabIndex={onRowClick ? 0 : undefined}
                    aria-expanded={renderDetail ? open : undefined}
                    onClick={onRowClick ? () => onRowClick(r) : undefined}
                    onKeyDown={
                      onRowClick
                        ? (e) => {
                            if (e.key === 'Enter' && e.target === e.currentTarget) onRowClick(r);
                          }
                        : undefined
                    }
                  >
                    {columns.map((c) => (
                      <td
                        key={c.key}
                        className={`${c.numeric ? 'num' : ''}${c.primary ? ' font-medium' : ''}${c.nowrap ? ' nowrap' : ''}`}
                        onClick={c.stopRowClick ? (e) => e.stopPropagation() : undefined}
                      >
                        {c.render(r)}
                      </td>
                    ))}
                  </tr>
                  {open && renderDetail && (
                    <tr>
                      <td colSpan={columns.length} className={detailClassName}>
                        {renderDetail(r)}
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  );
}

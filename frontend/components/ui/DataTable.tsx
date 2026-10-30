import { Fragment } from 'react';
import { EmptyState } from './EmptyState';
import { SkeletonRows } from './Skeleton';

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
  /** Rendered under the table inside the same card, for example a `Pager`. */
  footer?: React.ReactNode;
  loadingRows?: number;
  /** Extra classes for a row, for example an `open` highlight. */
  rowClassName?: (row: T) => string;
  /** Key of the row whose detail is open; `renderDetail` fills the full-width row below it. */
  expandedKey?: string | null;
  renderDetail?: (row: T) => React.ReactNode;
  detailClassName?: string;
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
  footer,
  loadingRows,
  rowClassName,
  expandedKey,
  renderDetail,
  detailClassName,
}: DataTableProps<T>) {
  if (rows === null) return <SkeletonRows rows={loadingRows} />;
  if (rows.length === 0) return <EmptyState title={emptyTitle}>{emptyHint}</EmptyState>;
  return (
    <div className="table-container">
      <table className="data-table">
        <thead>
          <tr>
            {columns.map((c) => {
              const active = sort?.key === c.sortKey;
              return (
                <th
                  key={c.key}
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
                    <button className="ac-th" onClick={() => onSort(c.sortKey!)}>
                      {c.header}
                      {active ? (sort!.dir === 'asc' ? ' ↑' : ' ↓') : ''}
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
                  tabIndex={onRowClick ? 0 : undefined}
                  aria-expanded={renderDetail ? open : undefined}
                  onClick={onRowClick ? () => onRowClick(r) : undefined}
                  onKeyDown={
                    onRowClick
                      ? (e) => {
                          if (e.key === 'Enter') onRowClick(r);
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
      {footer}
    </div>
  );
}

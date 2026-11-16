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
  /** Optional title for a detached view; otherwise the containing card title is used. */
  detachTitle?: string;
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
  detachTitle,
}: DataTableProps<T>) {
  if (rows === null) return <SkeletonRows rows={loadingRows} />;
  const csvColumns = columns.filter((column) => !column.csvSkip);
  const canExport =
    !!csvFileName && csvColumns.length > 0 && csvColumns.every((column) => column.csvValue);
  const detachTable = (button: HTMLButtonElement) => {
    const container = button.closest('.table-container');
    const table = container?.querySelector('table');
    const cardTitle = button.closest('.card')?.querySelector('.card-title')?.textContent?.trim();
    const title = detachTitle || cardTitle || 'Table results';
    const popup = window.open('', '_blank');
    const status = button.parentElement?.querySelector<HTMLElement>('[data-detach-status]');
    if (!popup || !table || !container) {
      if (status) status.textContent = 'Allow pop-ups to open the detached table.';
      popup?.close();
      return;
    }
    popup.opener = null;
    const detachedDocument = popup.document;
    detachedDocument.title = `${title} · ZeTSA`;
    detachedDocument.documentElement.lang = document.documentElement.lang;
    detachedDocument.documentElement.className = document.documentElement.className;
    detachedDocument.body.className = document.body.className;
    detachedDocument.head.replaceChildren();

    const viewport = detachedDocument.createElement('meta');
    viewport.name = 'viewport';
    viewport.content = 'width=device-width, initial-scale=1';
    detachedDocument.head.append(viewport);
    document.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]').forEach((source) => {
      const stylesheet = detachedDocument.createElement('link');
      stylesheet.rel = 'stylesheet';
      stylesheet.href = source.href;
      detachedDocument.head.append(stylesheet);
    });
    const detachedStyles = detachedDocument.createElement('style');
    detachedStyles.textContent = `
      body { margin: 0; padding: 2rem; background: var(--bg, #f7f8fa); color: var(--fg, #202734); }
      main { max-width: 100%; margin: 0 auto; }
      header { display: flex; align-items: baseline; justify-content: space-between; gap: 1rem; margin-bottom: 1rem; }
      h1 { margin: 0; font: 600 1.35rem/1.3 system-ui, sans-serif; }
      header p { margin: 0; color: var(--fg-secondary, #687282); font: 0.8rem/1.4 system-ui, sans-serif; }
      .detached-controls { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; }
      .detached-controls button, .detached-controls select, .detached-controls input { border: 1px solid var(--border, #d8dde5); border-radius: .45rem; padding: .4rem .7rem; background: var(--bg-secondary, #fff); color: var(--fg, #202734); font: 500 .8rem/1.2 system-ui, sans-serif; }
      .detached-controls button { cursor: pointer; }
      .detached-filter-label { display: flex; align-items: center; gap: .35rem; color: var(--fg-secondary, #687282); font: 500 .8rem/1.2 system-ui, sans-serif; }
      .detached-scroll { overflow: auto; padding: 0.75rem; border: 1px solid var(--border, #d8dde5); border-radius: 0.65rem; background: var(--bg-secondary, #fff); }
      table { width: 100%; }
      .detached-live-status { margin: 0 0 0.75rem; color: var(--fg-tertiary, #687282); font: 0.8rem/1.4 system-ui, sans-serif; }
      @media print { body { padding: 0; } .detached-controls, .detached-live-status { display: none; } .detached-scroll { overflow: visible; border: 0; padding: 0; } }
    `;
    detachedDocument.head.append(detachedStyles);

    const main = detachedDocument.createElement('main');
    const header = detachedDocument.createElement('header');
    const heading = detachedDocument.createElement('h1');
    heading.textContent = title;
    const context = detachedDocument.createElement('p');
    context.setAttribute('aria-live', 'polite');
    context.textContent = `${rows.length} visible rows`;
    const controls = detachedDocument.createElement('div');
    controls.className = 'detached-controls';
    const headers = Array.from(table.querySelectorAll<HTMLTableCellElement>('thead th'))
      .map((cell, index) => {
        const sortButton = cell.querySelector('button');
        const label =
          sortButton?.childNodes[0]?.textContent?.trim() || cell.textContent?.trim() || '';
        return { index, label };
      })
      .filter((column) => column.label);
    const filterLabel = detachedDocument.createElement('label');
    filterLabel.className = 'detached-filter-label';
    filterLabel.textContent = 'Filter this page';
    const filterInput = detachedDocument.createElement('input');
    filterInput.type = 'search';
    filterInput.setAttribute('aria-label', 'Filter detached table rows');
    filterInput.placeholder = 'Search rows';
    filterLabel.append(filterInput);
    controls.append(filterLabel);
    const filterColumn = detachedDocument.createElement('select');
    filterColumn.setAttribute('aria-label', 'Filter column');
    const allColumns = detachedDocument.createElement('option');
    allColumns.value = '-1';
    allColumns.textContent = 'All columns';
    filterColumn.append(allColumns);
    headers.forEach(({ index, label }) => {
      const option = detachedDocument.createElement('option');
      option.value = String(index);
      option.textContent = label;
      filterColumn.append(option);
    });
    controls.append(filterColumn);
    const sortColumn = detachedDocument.createElement('select');
    sortColumn.setAttribute('aria-label', 'Sort column');
    const noSort = detachedDocument.createElement('option');
    noSort.value = '-1';
    noSort.textContent = 'Original order';
    sortColumn.append(noSort);
    headers.forEach(({ index, label }) => {
      const option = detachedDocument.createElement('option');
      option.value = String(index);
      option.textContent = label;
      sortColumn.append(option);
    });
    controls.append(sortColumn);
    const directionButton = detachedDocument.createElement('button');
    directionButton.type = 'button';
    directionButton.textContent = 'Ascending';
    directionButton.setAttribute('aria-label', 'Toggle sort direction');
    directionButton.disabled = true;
    controls.append(directionButton);
    const printButton = detachedDocument.createElement('button');
    printButton.type = 'button';
    printButton.textContent = 'Print';
    printButton.addEventListener('click', () => popup.print());
    controls.append(printButton);
    if (canExport) {
      const exportButton = detachedDocument.createElement('button');
      exportButton.type = 'button';
      exportButton.textContent = 'Download visible CSV';
      exportButton.addEventListener('click', () => {
        const values = Array.from(
          detachedDocument.querySelectorAll<HTMLTableRowElement>('tbody tr[data-csv]:not([hidden])')
        ).flatMap((row) => {
          try {
            return [JSON.parse(row.dataset.csv!) as (string | number | boolean | null)[]];
          } catch {
            return [];
          }
        });
        downloadCsv(
          csvFileName!,
          csvColumns.map((column) => column.header),
          values
        );
      });
      controls.append(exportButton);
    }
    header.append(heading, context, controls);
    const scroll = detachedDocument.createElement('div');
    scroll.className = 'detached-scroll';
    const liveStatus = detachedDocument.createElement('p');
    liveStatus.className = 'detached-live-status';
    liveStatus.textContent = 'Live view · controls filter and sort rows on this page.';
    const copyTable = (source: HTMLTableElement) => {
      const copy = source.cloneNode(true) as HTMLTableElement;
      copy.querySelectorAll('thead button').forEach((control) => {
        control.querySelector('.table-sort-icon')?.remove();
        const label = detachedDocument.createElement('span');
        label.textContent = control.textContent;
        control.replaceWith(label);
      });
      copy.querySelectorAll('tbody button').forEach((control) => {
        const label = detachedDocument.createElement('span');
        label.textContent = control.textContent;
        control.replaceWith(label);
      });
      copy.querySelectorAll('a').forEach((link) => {
        link.target = '_blank';
        link.rel = 'noopener noreferrer';
      });
      copy.querySelectorAll('tr').forEach((row) => row.removeAttribute('tabindex'));
      return copy;
    };
    let filterText = '';
    let filterColumnIndex = -1;
    let sortColumnIndex = -1;
    let sortDirection: 'asc' | 'desc' = 'asc';
    const applyDetachedControls = () => {
      const detachedTable = scroll.querySelector('table');
      const body = detachedTable?.tBodies[0];
      if (!detachedTable || !body) return;
      const groups = Array.from(
        body.querySelectorAll<HTMLTableRowElement>('tr[data-detach-row]')
      ).map((row, index) => {
        if (!row.dataset.detachOrder) row.dataset.detachOrder = String(index);
        const detail = row.nextElementSibling;
        return {
          row,
          detail: detail?.matches('tr[data-detach-detail]')
            ? (detail as HTMLTableRowElement)
            : null,
        };
      });
      const needle = filterText.trim().toLocaleLowerCase();
      for (const group of groups) {
        const content =
          filterColumnIndex < 0
            ? (group.row.textContent ?? '')
            : (group.row.cells[filterColumnIndex]?.textContent ?? '');
        const hidden = !!needle && !content.toLocaleLowerCase().includes(needle);
        group.row.hidden = hidden;
        if (group.detail) group.detail.hidden = hidden;
      }
      groups.sort((left, right) => {
        if (sortColumnIndex < 0) {
          return Number(left.row.dataset.detachOrder) - Number(right.row.dataset.detachOrder);
        }
        const a = left.row.cells[sortColumnIndex]?.textContent?.trim() ?? '';
        const b = right.row.cells[sortColumnIndex]?.textContent?.trim() ?? '';
        return (
          a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }) *
          (sortDirection === 'asc' ? 1 : -1)
        );
      });
      body.append(
        ...groups.flatMap((group) => (group.detail ? [group.row, group.detail] : [group.row]))
      );
      const shown = groups.reduce((count, group) => count + Number(!group.row.hidden), 0);
      context.textContent = `${shown} of ${groups.length} rows on this page`;
      liveStatus.textContent =
        needle && shown === 0
          ? 'No rows match this filter.'
          : 'Live view · follows the source page; filters apply to this page.';
    };
    filterInput.addEventListener('input', () => {
      filterText = filterInput.value;
      applyDetachedControls();
    });
    filterColumn.addEventListener('change', () => {
      filterColumnIndex = Number(filterColumn.value);
      applyDetachedControls();
    });
    sortColumn.addEventListener('change', () => {
      sortColumnIndex = Number(sortColumn.value);
      directionButton.disabled = sortColumnIndex < 0;
      applyDetachedControls();
    });
    directionButton.addEventListener('click', () => {
      sortDirection = sortDirection === 'asc' ? 'desc' : 'asc';
      directionButton.textContent = sortDirection === 'asc' ? 'Ascending' : 'Descending';
      applyDetachedControls();
    });
    scroll.append(copyTable(table));
    applyDetachedControls();
    main.append(header, scroll);
    main.insertBefore(liveStatus, scroll);
    detachedDocument.body.replaceChildren(main);

    let animationFrame: number | null = null;
    let stopped = false;
    const refreshDetachedView = () => {
      animationFrame = null;
      const liveTable = container?.querySelector('table');
      if (popup.closed) {
        stopObservers();
        return;
      }
      if (!container.isConnected) {
        liveStatus.textContent = 'The source page was closed · this table is now a snapshot.';
        stopObservers();
        return;
      }
      if (!liveTable) return;
      const rowCount = Number(container.getAttribute('data-row-count') ?? rows.length);
      scroll.replaceChildren(copyTable(liveTable));
      context.textContent = `${rowCount} of ${rowCount} rows on this page`;
      applyDetachedControls();
      detachedDocument.documentElement.className = document.documentElement.className;
      for (const attribute of ['data-theme', 'style']) {
        const value = document.documentElement.getAttribute(attribute);
        if (value === null) detachedDocument.documentElement.removeAttribute(attribute);
        else detachedDocument.documentElement.setAttribute(attribute, value);
      }
      detachedDocument.body.className = document.body.className;
    };
    const scheduleRefresh = () => {
      if (animationFrame === null && !stopped) {
        animationFrame = window.requestAnimationFrame(refreshDetachedView);
      }
    };
    const stopObservers = () => {
      stopped = true;
      if (animationFrame !== null) window.cancelAnimationFrame(animationFrame);
      contentObserver.disconnect();
      themeObserver.disconnect();
      lifecycleObserver.disconnect();
    };
    const contentObserver = new MutationObserver(scheduleRefresh);
    contentObserver.observe(container, {
      attributes: true,
      attributeFilter: ['data-row-count'],
      childList: true,
      characterData: true,
      subtree: true,
    });
    const themeObserver = new MutationObserver(scheduleRefresh);
    themeObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme', 'style'],
    });
    const lifecycleRoot = container.closest('.container') ?? document.body;
    const lifecycleObserver = new MutationObserver(() => {
      if (!container.isConnected) {
        liveStatus.textContent = 'The source page was closed · this table is now a snapshot.';
        stopObservers();
      }
    });
    lifecycleObserver.observe(lifecycleRoot, { childList: true, subtree: true });
    popup.addEventListener('pagehide', stopObservers, { once: true });
    popup.focus();
    if (status) status.textContent = '';
  };
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
            <Button
              size="sm"
              aria-label={`Detach live table with ${rows.length} visible rows`}
              onClick={(event) => detachTable(event.currentTarget)}
            >
              Detach live table
            </Button>
            <span className="data-table-detach-status" data-detach-status role="status" />
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
                    data-detach-row="true"
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
                    <tr data-detach-detail="true">
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

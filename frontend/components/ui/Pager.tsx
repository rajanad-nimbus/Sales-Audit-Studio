interface PagerProps {
  page: number;
  pages: number;
  total: number;
  pageSize: number;
  pageSizes?: number[];
  onPage: (page: number) => void;
  onPageSize: (size: number) => void;
}

/** Footer for server-paged tables. */
export function Pager({
  page,
  pages,
  total,
  pageSize,
  pageSizes = [25, 50, 100],
  onPage,
  onPageSize,
}: PagerProps) {
  const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
  return (
    <div className="ac-pager">
      <span>
        {from.toLocaleString()}–{Math.min(page * pageSize, total).toLocaleString()} of{' '}
        {total.toLocaleString()}
      </span>
      <span className="ac-spacer" />
      <select
        value={pageSize}
        onChange={(e) => onPageSize(Number(e.target.value))}
        aria-label="Rows per page"
      >
        {pageSizes.map((n) => (
          <option key={n} value={n}>
            {n} / page
          </option>
        ))}
      </select>
      <button
        className="btn-secondary btn-small"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        Previous
      </button>
      <span>
        Page {page} of {pages.toLocaleString()}
      </span>
      <button
        className="btn-secondary btn-small"
        disabled={page >= pages}
        onClick={() => onPage(page + 1)}
      >
        Next
      </button>
    </div>
  );
}

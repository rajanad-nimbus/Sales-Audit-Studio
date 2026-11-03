interface PageNavProps {
  page: number;
  /** Whether another page follows. There is no total: counting a large table is itself a scan. */
  hasMore: boolean;
  onPage: (page: number) => void;
  pageSize?: number;
  pageSizes?: number[];
  onPageSize?: (size: number) => void;
}

/** Footer for server-paged tables too large to count: Previous, Next, and an optional page size. */
export function PageNav({
  page,
  hasMore,
  onPage,
  pageSize,
  pageSizes = [25, 50, 100, 200],
  onPageSize,
}: PageNavProps) {
  return (
    <div className="ac-pager">
      <span>Page {page}</span>
      <span className="ac-spacer" />
      {pageSize !== undefined && onPageSize && (
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
      )}
      <button
        className="btn-secondary btn-small"
        disabled={page <= 1}
        onClick={() => onPage(page - 1)}
      >
        Previous
      </button>
      <button
        className="btn-secondary btn-small"
        disabled={!hasMore}
        onClick={() => onPage(page + 1)}
      >
        Next
      </button>
    </div>
  );
}

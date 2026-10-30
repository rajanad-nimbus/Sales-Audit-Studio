type ControlsTableProps = {
  headers: string[];
  rows: string[][];
};

export default function ControlsTable({ headers, rows }: ControlsTableProps) {
  return (
    <div className="table-container" style={{ marginTop: '1rem' }}>
      <table className="data-table">
        <thead>
          <tr>
            {headers.map((header) => (
              <th key={header}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.length ? (
            rows.map((row, rowIndex) => (
              <tr key={rowIndex}>
                {row.map((cell, cellIndex) => (
                  <td key={cellIndex}>{cell}</td>
                ))}
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={headers.length} className="cc-empty">
                No records configured.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

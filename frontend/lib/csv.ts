export type CsvValue = string | number | boolean | null | undefined;

const csvCell = (value: CsvValue) => {
  const text = value == null ? '' : String(value);
  // Spreadsheet applications may evaluate cells beginning with these characters as formulas.
  const safe = /^[\t\r\n ]*[=+\-@]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
};

export function downloadBlob(filename: string, blob: Blob): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename.replace(/[^\w.-]+/g, '_');
  link.style.display = 'none';
  document.body.append(link);
  link.click();
  link.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function downloadCsv(filename: string, headers: string[], rows: CsvValue[][]): void {
  const contents = [headers, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n');
  downloadBlob(filename, new Blob([`\uFEFF${contents}`], { type: 'text/csv;charset=utf-8' }));
}

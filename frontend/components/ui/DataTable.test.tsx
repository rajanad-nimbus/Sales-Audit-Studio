import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { DataTable } from './DataTable';

it('keeps headings, sorting, and footer available when no rows match', () => {
  const sort = vi.fn();
  render(
    <DataTable<{ id: string }>
      columns={[{ key: 'id', header: 'Case', render: (row) => row.id, sortKey: 'id' }]}
      rows={[]}
      rowKey={(row) => row.id}
      onSort={sort}
      sort={{ key: 'id', dir: 'desc' }}
      emptyTitle="No matching cases"
      emptyHint={<p>Try changing your filters.</p>}
      footer={<button>Previous page</button>}
    />
  );
  expect(screen.getByRole('columnheader')).toHaveAttribute('aria-sort', 'descending');
  fireEvent.click(screen.getByRole('button', { name: 'Case' }));
  expect(sort).toHaveBeenCalledWith('id');
  expect(screen.getByText('No matching cases')).toBeInTheDocument();
  const scroller = screen.getByRole('region', { name: 'Table results' });
  expect(scroller).toHaveAttribute('tabindex', '0');
  expect(within(scroller).queryByRole('button', { name: 'Previous page' })).toBeNull();
  expect(screen.getByRole('button', { name: 'Previous page' })).toBeInTheDocument();
});

it('opens a focused row with Enter without also opening it from a nested action', () => {
  const openRow = vi.fn();
  const action = vi.fn();
  const row = { id: 'case-1' };
  render(
    <DataTable
      columns={[
        { key: 'id', header: 'Case', render: (item) => item.id },
        {
          key: 'action',
          header: 'Action',
          stopRowClick: true,
          render: () => <button onClick={action}>Review</button>,
        },
      ]}
      rows={[row]}
      rowKey={(item) => item.id}
      onRowClick={openRow}
    />
  );
  const button = screen.getByRole('button', { name: 'Review' });
  fireEvent.keyDown(button, { key: 'Enter' });
  fireEvent.click(button);
  expect(action).toHaveBeenCalledOnce();
  expect(openRow).not.toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole('row', { name: 'case-1 Review' }), { key: 'Enter' });
  expect(openRow).toHaveBeenCalledExactlyOnceWith(row);
});

import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { useNameDialog } from './NameDialog';

function Harness({ onName }: { onName: (name: string | null) => void }) {
  const { ask, dialog } = useNameDialog();
  return (
    <>
      <button onClick={async () => onName(await ask('Name this Cases view', ['Month end']))}>
        Save current view
      </button>
      {dialog}
    </>
  );
}

describe('useNameDialog', () => {
  it('resolves with the trimmed name when the form is submitted', async () => {
    const onName = vi.fn();
    render(<Harness onName={onName} />);
    await userEvent.click(screen.getByRole('button', { name: 'Save current view' }));
    expect(screen.getByRole('dialog', { name: 'Name this Cases view' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save view' })).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/View name/), '  High exposure  {Enter}');
    expect(onName).toHaveBeenCalledWith('High exposure');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('blocks names over 60 characters and says when a view will be replaced', async () => {
    const onName = vi.fn();
    render(<Harness onName={onName} />);
    await userEvent.click(screen.getByRole('button', { name: 'Save current view' }));
    const input = screen.getByLabelText(/View name/);
    await userEvent.type(input, 'month END');
    expect(screen.getByText('Replaces the saved view "Month end".')).toBeInTheDocument();
    await userEvent.clear(input);
    await userEvent.type(input, 'x'.repeat(61));
    expect(screen.getByRole('alert')).toHaveTextContent('Use 60 characters or fewer');
    expect(screen.getByRole('button', { name: 'Save view' })).toBeDisabled();
  });

  it('resolves with null when cancelled', async () => {
    const onName = vi.fn();
    render(<Harness onName={onName} />);
    await userEvent.click(screen.getByRole('button', { name: 'Save current view' }));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onName).toHaveBeenCalledWith(null);
  });
});

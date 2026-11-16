import { fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { TenderTypePackages } from '@/components/settings/TenderTypePackages';
import { ME, calls, renderAs } from '@/test/utils';

const BUNDLE = {
  format: 'zetsa.tender-types',
  version: 1,
  tenders: [{ code: 'Card', name: 'Card', tender_group: 'Card' }],
};
const file = (content: unknown) =>
  new File([JSON.stringify(content)], 'tenders.json', { type: 'application/json' });
const choose = (input: HTMLElement, content: unknown) =>
  fireEvent.change(input, { target: { files: [file(content)] } });

describe('TenderTypePackages', () => {
  it('previews a package, then applies it only with a reason', async () => {
    const applied = vi.fn(async () => {});
    await renderAs(<TenderTypePackages onApplied={applied} />, ME('finance'), {
      'POST /api/tender-types/package/preview': {
        valid: true,
        preview_token: 'a'.repeat(64),
        changed: true,
        errors: [],
        rows: [{ code: 'Card', name: 'Card', action: 'Create' }],
      },
      'POST /api/tender-types/package/apply': { changed: true },
    });
    choose(document.querySelector('input[type=file]') as HTMLElement, BUNDLE);

    expect(await screen.findByText('Create: Card — Card')).toBeInTheDocument();
    const apply = screen.getByRole('button', { name: 'Apply tender package' });
    expect(apply).toBeDisabled();

    await userEvent.type(screen.getByLabelText(/Reason for promotion/), 'Match production');
    await userEvent.click(apply);
    await waitFor(() => expect(calls('post', '/api/tender-types/package/apply')).toHaveLength(1));
    expect(calls('post', '/api/tender-types/package/apply')[0][1]).toEqual({
      package: BUNDLE,
      preview_token: 'a'.repeat(64),
      reason: 'Match production',
    });
    expect(applied).toHaveBeenCalled();
  });

  it('shows each problem row and offers no way to apply', async () => {
    await renderAs(<TenderTypePackages onApplied={async () => {}} />, ME('finance'), {
      'POST /api/tender-types/package/preview': {
        valid: false,
        preview_token: null,
        changed: false,
        errors: [{ row: 2, error: 'Tender code must be one of Card, Cash' }],
        rows: [],
      },
    });
    choose(document.querySelector('input[type=file]') as HTMLElement, BUNDLE);

    expect(await screen.findByText(/Row 2: Tender code must be one of/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply tender package' })).toBeDisabled();
  });

  it('tells a role without edit rights that applying needs Finance or IT', async () => {
    await renderAs(<TenderTypePackages onApplied={async () => {}} />, ME('auditor'), {
      'POST /api/tender-types/package/preview': {
        valid: true,
        preview_token: 'b'.repeat(64),
        changed: true,
        errors: [],
        rows: [{ code: 'Card', name: 'Card', action: 'Update' }],
      },
    });
    choose(document.querySelector('input[type=file]') as HTMLElement, BUNDLE);
    expect(await screen.findByText(/Finance or IT role is required/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply tender package' })).toBeDisabled();
  });
});

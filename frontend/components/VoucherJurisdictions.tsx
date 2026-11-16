'use client';

import { Field } from '@/components/ui';
import { LocationDraft, VOUCHER_LOCATIONS, VoucherLocation } from '@/lib/vouchers';

export function VoucherJurisdictions({
  value,
  onChange,
  disabled = false,
}: {
  value: LocationDraft;
  onChange: (value: LocationDraft) => void;
  disabled?: boolean;
}) {
  const change = (level: VoucherLocation, patch: Partial<LocationDraft[VoucherLocation]>) =>
    onChange({ ...value, [level]: { ...value[level], ...patch } });
  return (
    <>
      {VOUCHER_LOCATIONS.map((level) => (
        <div className="form-row" key={level} role="group" aria-label={`${level} jurisdiction`}>
          <Field label={`${level} country`}>
            <input
              value={value[level].country}
              maxLength={2}
              disabled={disabled}
              onChange={(event) => change(level, { country: event.target.value.toUpperCase() })}
            />
          </Field>
          <Field label={`${level} state / province`}>
            <input
              value={value[level].state}
              maxLength={100}
              disabled={disabled}
              onChange={(event) => change(level, { state: event.target.value.toUpperCase() })}
            />
          </Field>
        </div>
      ))}
      <p className="hint">
        Use two-letter country codes. State or province values must match the routing policy.
      </p>
    </>
  );
}

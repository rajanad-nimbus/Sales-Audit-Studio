export type VoucherLocation = 'Store' | 'Issuer' | 'Recipient';
export interface Jurisdiction {
  country: string;
  state: string | null;
}
export type VoucherLocations = Partial<Record<VoucherLocation, Jurisdiction>>;
export interface RoutingDecision {
  destination_type: 'Government' | 'RetailerIncome' | 'Manual' | 'Legacy';
  label: string;
  location_of?: string | null;
  income_partner?: string | null;
  policy_revision?: string | null;
  trace: {
    location_of: string;
    source: string;
    jurisdiction: Jurisdiction | null;
    outcome: string;
  }[];
}

export type LocationDraft = Record<VoucherLocation, { country: string; state: string }>;
export const VOUCHER_LOCATIONS: VoucherLocation[] = ['Store', 'Issuer', 'Recipient'];
export const locationDraft = (locations?: VoucherLocations | null): LocationDraft => ({
  Store: { country: locations?.Store?.country ?? '', state: locations?.Store?.state ?? '' },
  Issuer: { country: locations?.Issuer?.country ?? '', state: locations?.Issuer?.state ?? '' },
  Recipient: {
    country: locations?.Recipient?.country ?? '',
    state: locations?.Recipient?.state ?? '',
  },
});
export const validLocations = (draft: LocationDraft) =>
  VOUCHER_LOCATIONS.every((level) => {
    const value = draft[level];
    return (
      (!value.country.trim() && !value.state.trim()) || /^[A-Za-z]{2}$/.test(value.country.trim())
    );
  });
export const locationsPayload = (draft: LocationDraft): VoucherLocations =>
  Object.fromEntries(
    VOUCHER_LOCATIONS.filter((level) => draft[level].country.trim()).map((level) => [
      level,
      {
        country: draft[level].country.trim().toUpperCase(),
        state: draft[level].state.trim().toUpperCase() || null,
      },
    ])
  );

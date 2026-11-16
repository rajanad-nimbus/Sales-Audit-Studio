'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/lib/api';
import { apiError } from '@/lib/storeDays';
import { formatDateTime } from '@/lib/preferences';
import type { Jurisdiction } from '@/lib/vouchers';
import { useMe } from '@/components/MeProvider';
import { useToast } from '@/components/Feedback';
import { Alert, Button, Card, Field } from '@/components/ui';

const LEVELS = ['Store', 'Issuer', 'Recipient', 'HQ'] as const;
type Level = (typeof LEVELS)[number];
interface Rule {
  country: string;
  state: string | null;
  locations: Level[];
}
interface Policy {
  enabled: boolean;
  hq_location: Jurisdiction | null;
  income_partner: string | null;
  rules: Rule[];
  revision: string | null;
  updated_by: string | null;
  updated_at: string | null;
}
interface Draft {
  enabled: boolean;
  hq_country: string;
  hq_state: string;
  income_partner: string;
  rules: Rule[];
}
const toDraft = (policy: Policy): Draft => ({
  enabled: policy.enabled,
  hq_country: policy.hq_location?.country ?? '',
  hq_state: policy.hq_location?.state ?? '',
  income_partner: policy.income_partner ?? '',
  rules: policy.rules.map((rule) => ({ ...rule, locations: [...rule.locations] })),
});

export function VoucherRoutingPolicy() {
  const { me } = useMe();
  const toast = useToast();
  const canEdit = ['it', 'admin'].includes(me?.role ?? '');
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try {
      const { data } = await api.get<Policy>('/api/vouchers/routing-policy');
      setPolicy(data);
      setDraft(toDraft(data));
      setError(null);
    } catch (e) {
      setError(apiError(e, 'Could not load jurisdiction routing.'));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  const change = (patch: Partial<Draft>) =>
    setDraft((current) => (current ? { ...current, ...patch } : current));
  const ruleChange = (index: number, patch: Partial<Rule>) =>
    setDraft((current) =>
      current
        ? {
            ...current,
            rules: current.rules.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)),
          }
        : current
    );
  const jurisdictionKeys = (draft?.rules ?? []).map(
    (rule) => `${rule.country.trim().toUpperCase()}/${rule.state?.trim().toUpperCase() ?? ''}`
  );
  const duplicate = new Set(jurisdictionKeys).size !== jurisdictionKeys.length;
  const invalid =
    !draft ||
    duplicate ||
    (draft.enabled && !draft.income_partner.trim()) ||
    (draft.hq_country.trim()
      ? !/^[A-Za-z]{2}$/.test(draft.hq_country.trim())
      : !!draft.hq_state.trim()) ||
    draft.rules.some((rule) => !/^[A-Za-z]{2}$/.test(rule.country.trim()));
  const dirty = !!policy && !!draft && JSON.stringify(draft) !== JSON.stringify(toDraft(policy));
  const save = async () => {
    if (!draft || !policy || invalid || busy) return;
    setBusy(true);
    setError(null);
    try {
      const { data } = await api.put<Policy>('/api/vouchers/routing-policy', {
        enabled: draft.enabled,
        hq_location: draft.hq_country.trim()
          ? { country: draft.hq_country.trim(), state: draft.hq_state.trim() || null }
          : null,
        income_partner: draft.income_partner.trim() || null,
        rules: draft.rules.map((rule) => ({
          country: rule.country.trim(),
          state: rule.state?.trim() || null,
          locations: rule.locations,
        })),
        expected_revision: policy.revision,
      });
      setPolicy(data);
      setDraft(toDraft(data));
      toast('Voucher routing policy saved.', 'success');
    } catch (e) {
      setError(apiError(e, 'Could not save jurisdiction routing.'));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card title="Voucher jurisdiction routing">
      <p>Store → Issuer → Recipient → HQ</p>
      <p className="hint">
        At each location, the state rule takes precedence over the country rule. A location excluded
        by that rule is skipped. The first permitted jurisdiction is selected; if none match, the
        voucher is assigned to retailer income using the configured adjustment partner.
      </p>
      {error && (
        <Alert tone="error">
          {error}
          <Button onClick={() => void load()} disabled={busy}>
            Reload saved policy
          </Button>
        </Alert>
      )}
      {draft && policy ? (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void save();
          }}
        >
          <Field label="Route escheatment by jurisdiction">
            <input
              type="checkbox"
              checked={draft.enabled}
              disabled={!canEdit || busy}
              onChange={(event) => change({ enabled: event.target.checked })}
            />
          </Field>
          {!draft.enabled && (
            <p className="hint">Routing is off. Finance records manual escheatment.</p>
          )}
          <div className="form-row">
            <Field label="HQ country">
              <input
                value={draft.hq_country}
                maxLength={2}
                disabled={!canEdit || busy}
                onChange={(event) => change({ hq_country: event.target.value.toUpperCase() })}
              />
            </Field>
            <Field label="HQ state / province">
              <input
                value={draft.hq_state}
                maxLength={100}
                disabled={!canEdit || busy}
                onChange={(event) => change({ hq_state: event.target.value.toUpperCase() })}
              />
            </Field>
            <Field label="Income adjustment partner">
              <input
                value={draft.income_partner}
                maxLength={100}
                disabled={!canEdit || busy}
                onChange={(event) => change({ income_partner: event.target.value })}
              />
            </Field>
          </div>
          <p className="hint">
            Use two-letter country codes. Leave the state blank for a country rule. No locations
            selected means the jurisdiction excludes every level.
          </p>
          {draft.rules.length === 0 && <p className="hint">No jurisdiction rules configured.</p>}
          {draft.rules.map((rule, index) => (
            <div
              className="form-row"
              key={index}
              role="group"
              aria-label={`Jurisdiction rule ${index + 1}`}
            >
              <Field label="Country">
                <input
                  value={rule.country}
                  maxLength={2}
                  disabled={!canEdit || busy}
                  onChange={(event) =>
                    ruleChange(index, { country: event.target.value.toUpperCase() })
                  }
                />
              </Field>
              <Field label="State / province">
                <input
                  value={rule.state ?? ''}
                  maxLength={100}
                  disabled={!canEdit || busy}
                  onChange={(event) =>
                    ruleChange(index, { state: event.target.value.toUpperCase() || null })
                  }
                />
              </Field>
              {LEVELS.map((level) => (
                <Field label={level} key={level}>
                  <input
                    type="checkbox"
                    checked={rule.locations.includes(level)}
                    disabled={!canEdit || busy}
                    onChange={(event) =>
                      ruleChange(index, {
                        locations: LEVELS.filter((value) =>
                          value === level ? event.target.checked : rule.locations.includes(value)
                        ),
                      })
                    }
                  />
                </Field>
              ))}
              {canEdit && (
                <Button
                  disabled={busy}
                  aria-label={`Remove jurisdiction rule ${index + 1}`}
                  onClick={() => change({ rules: draft.rules.filter((_, i) => i !== index) })}
                >
                  Remove
                </Button>
              )}
            </div>
          ))}
          {duplicate && (
            <Alert tone="warning">Each country/state jurisdiction can have only one rule.</Alert>
          )}
          {canEdit && (
            <div className="form-row">
              <Button
                disabled={busy || draft.rules.length >= 200}
                onClick={() =>
                  change({ rules: [...draft.rules, { country: '', state: null, locations: [] }] })
                }
              >
                Add jurisdiction
              </Button>
              <Button type="submit" variant="primary" disabled={!dirty || invalid || busy}>
                {busy ? 'Saving…' : 'Save routing policy'}
              </Button>
            </div>
          )}
          {policy.updated_by && (
            <p className="hint">
              Updated by {policy.updated_by}
              {policy.updated_at ? ` · ${formatDateTime(policy.updated_at)}` : ''}
            </p>
          )}
        </form>
      ) : (
        !error && <p className="hint">Loading jurisdiction routing…</p>
      )}
    </Card>
  );
}

'use client';

import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { applyUserPreferences, DEFAULT_PREFERENCES, type UserPreferences } from '@/lib/preferences';
import { useTheme } from '@/lib/ThemeContext';
import { Alert, Button, Card, Field, PageHeader } from '@/components/ui';
import { uiMessage } from '@/lib/ui-messages';

const LOCALES = [
  ['en-US', 'English (United States)'],
  ['en-GB', 'English (United Kingdom)'],
  ['en-CA', 'English (Canada)'],
  ['fr-FR', 'Français (France)'],
  ['de-DE', 'Deutsch (Deutschland)'],
  ['ja-JP', '日本語 (日本)'],
] as const;
const TIME_ZONES = [
  'UTC',
  'America/Los_Angeles',
  'America/Chicago',
  'America/New_York',
  'America/Toronto',
  'America/Sao_Paulo',
  'Europe/London',
  'Europe/Paris',
  'Europe/Berlin',
  'Africa/Johannesburg',
  'Asia/Dubai',
  'Asia/Kolkata',
  'Asia/Kathmandu',
  'Asia/Singapore',
  'Asia/Tokyo',
  'Australia/Sydney',
];

export default function PreferencesPage() {
  const { setTheme } = useTheme();
  const [preferences, setPreferences] = useState<UserPreferences>(DEFAULT_PREFERENCES);
  const [savedPreferences, setSavedPreferences] = useState<UserPreferences>(DEFAULT_PREFERENCES);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [saved, setSaved] = useState(false);
  const t = (key: Parameters<typeof uiMessage>[0]) => uiMessage(key, preferences.locale);
  useEffect(() => {
    api
      .get<UserPreferences>('/api/me/preferences')
      .then(({ data }) => {
        setPreferences(data);
        setSavedPreferences(data);
        applyUserPreferences(data);
        setTheme(data.theme);
      })
      .catch(() => setError(uiMessage('loadError')))
      .finally(() => setLoading(false));
  }, [setTheme]);
  const changed = useMemo(() => {
    const comparable = ({ updated_at: _updatedAt, ...value }: UserPreferences) => value;
    return JSON.stringify(comparable(preferences)) !== JSON.stringify(comparable(savedPreferences));
  }, [preferences, savedPreferences]);
  const save = async () => {
    setSaving(true);
    setError('');
    setSaved(false);
    try {
      const { data } = await api.put<UserPreferences>('/api/me/preferences', preferences);
      setPreferences(data);
      setSavedPreferences(data);
      applyUserPreferences(data);
      setTheme(data.theme);
      setSaved(true);
    } catch (e) {
      setError(
        (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail ??
          uiMessage('saveError', preferences.locale)
      );
    } finally {
      setSaving(false);
    }
  };
  const patch = <K extends keyof UserPreferences>(key: K, value: UserPreferences[K]) => {
    setPreferences((current) => ({ ...current, [key]: value }));
    setSaved(false);
  };

  return (
    <div className="page-container preferences-page">
      <PageHeader
        title={t('preferences')}
        subtitle={t('preferencesSubtitle')}
      />
      {error && <Alert tone="error">{error}</Alert>}
      {saved && <Alert tone="success">{t('saved')}</Alert>}
      <Card title={t('regionalDisplay')}>
        <div className="preferences-grid">
          <Field label={t('locale')}>
            <select
              value={preferences.locale}
              onChange={(event) => patch('locale', event.target.value)}
              disabled={loading}
            >
              {LOCALES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label={t('timeZone')}>
            <select
              value={preferences.time_zone}
              onChange={(event) => patch('time_zone', event.target.value)}
              disabled={loading}
            >
              {TIME_ZONES.map((zone) => (
                <option key={zone} value={zone}>
                  {zone.replaceAll('_', ' ')}
                </option>
              ))}
              {!TIME_ZONES.includes(preferences.time_zone) && (
                <option value={preferences.time_zone}>{preferences.time_zone}</option>
              )}
            </select>
          </Field>
        </div>
        <p className="hint preferences-preview">
          Example:{' '}
          {new Intl.NumberFormat(preferences.locale, { style: 'currency', currency: 'EUR' }).format(
            1234.56
          )}{' '}
          ·{' '}
          {new Intl.DateTimeFormat(preferences.locale, {
            timeZone: preferences.time_zone,
            dateStyle: 'full',
            timeStyle: 'short',
          }).format(new Date('2026-11-09T12:30:00Z'))}
        </p>
      </Card>
      <Card title={t('appearance')}>
        <div className="preferences-grid">
          <Field label={t('theme')}>
            <select
              value={preferences.theme}
              onChange={(event) => patch('theme', event.target.value as UserPreferences['theme'])}
              disabled={loading}
            >
              <option value="system">{t('device')}</option>
              <option value="light">{t('light')}</option>
              <option value="dark">{t('dark')}</option>
            </select>
          </Field>
          <Field label={t('density')}>
            <select
              value={preferences.density}
              onChange={(event) =>
                patch('density', event.target.value as UserPreferences['density'])
              }
              disabled={loading}
            >
              <option value="comfortable">{t('comfortable')}</option>
              <option value="compact">{t('compact')}</option>
            </select>
          </Field>
          <Field label={t('contrast')}>
            <select
              value={preferences.contrast}
              onChange={(event) =>
                patch('contrast', event.target.value as UserPreferences['contrast'])
              }
              disabled={loading}
            >
              <option value="standard">{t('standard')}</option>
              <option value="high">{t('highContrast')}</option>
            </select>
          </Field>
          <Field label={t('motion')}>
            <select
              value={preferences.motion}
              onChange={(event) => patch('motion', event.target.value as UserPreferences['motion'])}
              disabled={loading}
            >
              <option value="system">{t('systemMotion')}</option>
              <option value="reduce">{t('reduceMotion')}</option>
            </select>
          </Field>
        </div>
      </Card>
      <div className="toolbar preferences-save">
        <span className="hint">{t('accountSync')}</span>
        <Button
          variant="primary"
          onClick={() => void save()}
          disabled={loading || saving || !changed}
        >
          {saving ? t('saving') : t('save')}
        </Button>
      </div>
    </div>
  );
}

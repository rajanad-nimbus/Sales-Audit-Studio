'use client';

import { useEffect, useMemo, useState } from 'react';
import { api } from '@/lib/api';
import { useToast } from '@/components/Feedback';
import {
  Alert,
  Badge,
  Button,
  Card,
  EmptyState,
  MetricGrid,
  MetricTile,
  Segmented,
  SkeletonRows,
  Toolbar,
} from '@/components/ui';
import { SettingsSection } from './SettingsSection';

interface AdminScreen {
  key: string;
  path: string;
  label: string;
  group: string;
  hidden: boolean;
  protected: boolean;
}
interface AdminModule {
  key: string;
  label: string;
  description: string;
  screens: string[];
}
type Filter = 'all' | 'visible' | 'hidden';

function Switch({
  on,
  label,
  disabled,
  onChange,
}: {
  on: boolean;
  label: string;
  disabled?: boolean;
  onChange: (on: boolean) => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={label}
      disabled={disabled}
      className="adm-switch"
      onClick={() => onChange(!on)}
    />
  );
}

export function AdminConfig() {
  const toast = useToast();
  const [screens, setScreens] = useState<AdminScreen[]>([]);
  const [modules, setModules] = useState<AdminModule[]>([]);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [saved, setSaved] = useState<Set<string>>(new Set());
  const [reason, setReason] = useState('');
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [saving, setSaving] = useState(false);

  const apply = (data: { screens: AdminScreen[]; modules: AdminModule[] }) => {
    const keys = data.screens.filter((s) => s.hidden).map((s) => s.key);
    setScreens(data.screens);
    setModules(data.modules);
    setHidden(new Set(keys));
    setSaved(new Set(keys));
  };

  useEffect(() => {
    api
      .get('/api/admin/config')
      .then((res) => apply(res.data))
      .catch(() => setError('Unable to load Admin settings.'))
      .finally(() => setLoaded(true));
  }, []);

  const changes = useMemo(() => {
    let n = 0;
    screens.forEach((s) => {
      if (hidden.has(s.key) !== saved.has(s.key)) n += 1;
    });
    return n;
  }, [screens, hidden, saved]);

  const groups = useMemo(() => {
    const q = query.trim().toLowerCase();
    const map = new Map<string, AdminScreen[]>();
    screens
      .filter((s) => {
        if (filter === 'hidden' && !hidden.has(s.key)) return false;
        if (filter === 'visible' && hidden.has(s.key)) return false;
        return !q || `${s.label} ${s.path} ${s.group}`.toLowerCase().includes(q);
      })
      .forEach((s) => map.set(s.group, [...(map.get(s.group) ?? []), s]));
    return [...map.entries()];
  }, [screens, hidden, query, filter]);

  const set = (keys: string[], hide: boolean) =>
    setHidden((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => (hide ? next.add(k) : next.delete(k)));
      return next;
    });

  const total = screens.length;
  const hiddenCount = hidden.size;
  const modulesOff = modules.filter((m) => m.screens.every((k) => hidden.has(k))).length;

  const save = async () => {
    setSaving(true);
    try {
      setError(null);
      const { data } = await api.put('/api/admin/config/screens', {
        hidden: [...hidden],
        reason: reason.trim(),
      });
      apply(data);
      setReason('');
      toast('Admin changes saved.', 'success');
    } catch (e: any) {
      setError(e?.response?.data?.detail ?? 'Could not save Admin changes.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <SettingsSection
      title="Admin"
      description="Choose which pages and modules people can use. Administrators always keep full access."
    >
      {error && (
        <Alert tone="error" onDismiss={() => setError(null)}>
          {error}
        </Alert>
      )}

      {!loaded ? (
        <SkeletonRows rows={6} />
      ) : (
        <>
          <MetricGrid>
            <MetricTile label="Pages available" value={total - hiddenCount} />
            <MetricTile label="Pages hidden" value={hiddenCount} />
            <MetricTile label="Modules off" value={modulesOff} />
          </MetricGrid>

          <Card title="Modules">
            <p className="hint">Switch a group of related pages on or off in one step.</p>
            <div className="adm-modules">
              {modules.map((m) => {
                const off = m.screens.every((k) => hidden.has(k));
                return (
                  <div key={m.key} className={`adm-module${off ? ' off' : ''}`}>
                    <div className="adm-module-top">
                      <b>{m.label}</b>
                      <Switch
                        on={!off}
                        label={`${m.label} module`}
                        onChange={(on) => set(m.screens, !on)}
                      />
                    </div>
                    <small>{m.description}</small>
                    <div>
                      <Badge variant={off ? 'secondary' : 'success'} size="sm">
                        {off ? 'Off' : 'On'}
                      </Badge>{' '}
                      <small>{m.screens.length} pages</small>
                    </div>
                  </div>
                );
              })}
            </div>
          </Card>

          <Card title="Pages">
            <Toolbar
              count={`${groups.reduce((n, [, items]) => n + items.length, 0)} of ${total} pages`}
            >
              <input
                type="search"
                className="adm-search"
                placeholder="Search pages"
                aria-label="Search pages"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <Segmented
                value={filter}
                onChange={setFilter}
                options={[
                  { value: 'all', label: 'All' },
                  { value: 'visible', label: 'Visible' },
                  { value: 'hidden', label: 'Hidden' },
                ]}
              />
            </Toolbar>
            {groups.length === 0 && <EmptyState title="No pages match" />}
            {groups.map(([group, items]) => {
              const allOn = items.every((s) => s.protected || !hidden.has(s.key));
              const editable = items.filter((s) => !s.protected).map((s) => s.key);
              return (
                <div className="adm-group" key={group}>
                  <div className="adm-group-head">
                    <h3>{group === 'Configuration' ? 'Admin' : group}</h3>
                    {editable.length > 0 && (
                      <Button size="sm" variant="secondary" onClick={() => set(editable, allOn)}>
                        {allOn ? 'Hide all' : 'Show all'}
                      </Button>
                    )}
                  </div>
                  {items.map((s) => {
                    const off = hidden.has(s.key);
                    return (
                      <div className={`adm-row${off ? ' off' : ''}`} key={s.key}>
                        <div className="adm-row-main">
                          <b>{s.label}</b>
                          <small>{s.path}</small>
                        </div>
                        <div className="adm-row-side">
                          {s.protected && <Badge size="sm">Always on</Badge>}
                          {!s.protected && off !== saved.has(s.key) && (
                            <Badge variant="warning" size="sm">
                              Unsaved
                            </Badge>
                          )}
                          <Switch
                            on={!off}
                            label={s.label}
                            disabled={s.protected}
                            onChange={(on) => set([s.key], !on)}
                          />
                        </div>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </Card>

          {changes > 0 && (
            <div className="adm-savebar" role="region" aria-label="Unsaved changes">
              <b>
                {changes} unsaved {changes === 1 ? 'change' : 'changes'}
              </b>
              <input
                value={reason}
                maxLength={2000}
                placeholder="Reason for this change (at least 10 characters)"
                aria-label="Reason for change"
                onChange={(e) => setReason(e.target.value)}
              />
              <Button variant="secondary" onClick={() => setHidden(new Set(saved))}>
                Discard
              </Button>
              <Button
                variant="primary"
                disabled={reason.trim().length < 10 || saving}
                onClick={() => void save()}
              >
                Save changes
              </Button>
            </div>
          )}
        </>
      )}
    </SettingsSection>
  );
}

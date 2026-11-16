'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { api, AuditEvent } from '@/lib/api';
import { downloadBlob } from '@/lib/csv';
import { formatDateTime, formatNumber } from '@/lib/preferences';
import { Alert, Button, DataTable, EmptyState, PageHeader, Segmented } from '@/components/ui';

interface Evt extends AuditEvent {
  after?: any;
}
import { useDataChanged } from '@/lib/sync';
import { CopyButton, SkeletonRows, useToast } from '@/components/Feedback';
import { AgentModeNotice, useAgentMode } from '@/components/AgentMode';
import { CaseLink, useCanOpen } from '@/components/MeProvider';

import {
  GROUPS,
  SYSTEM_ACTORS,
  groupOf,
  FILTERS,
  pretty,
  tone,
  isAlert,
  initials,
  groupLabel,
  ago,
  dayLabel,
} from '@/lib/activityUtil';

interface SavedActivityView {
  name: string;
  params: { agent?: string; q?: string; alerts_only?: boolean };
}

export default function ActivityPage() {
  const [latest, setLatest] = useState<Evt[]>([]);
  const [older, setOlder] = useState<Evt[]>([]);
  const olderRef = useRef<Evt[]>([]);
  const queryVersion = useRef(0);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [onlyAlerts, setOnlyAlerts] = useState(false);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const known = useRef<Set<string> | null>(null);
  const agentMode = useAgentMode();
  const canOpen = useCanOpen();
  const events = useMemo(() => {
    const seen = new Set<string>();
    const out: Evt[] = [];
    for (const e of [...latest, ...older])
      if (!seen.has(e.id)) {
        seen.add(e.id);
        out.push(e);
      }
    return out;
  }, [latest, older]);
  const [open, setOpen] = useState<string | null>(null);
  const toast = useToast();
  const [loaded, setLoaded] = useState(false);
  const [agent, setAgent] = useState('all');
  const [filtersReady, setFiltersReady] = useState(false);
  const [savedViews, setSavedViews] = useState<SavedActivityView[]>([]);
  const [savedViewsError, setSavedViewsError] = useState(false);
  const savedViewSaveQueue = useRef<Promise<unknown>>(Promise.resolve());
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const g = params.get('agent');
    if (FILTERS.some((filter) => filter.key === g)) setAgent(g!);
    setQ((params.get('q') ?? '').slice(0, 255));
    setOnlyAlerts(params.get('alerts_only') === '1');
    setFiltersReady(true);
  }, []);
  const [q, setQ] = useState('');
  const [debouncedQ, setDebouncedQ] = useState('');
  const [live, setLive] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [, setNow] = useState(0);

  useEffect(() => {
    const timeout = setTimeout(() => setDebouncedQ(q.trim()), 250);
    return () => clearTimeout(timeout);
  }, [q]);

  useEffect(() => {
    if (!filtersReady) return;
    const params = new URLSearchParams(window.location.search);
    for (const key of ['agent', 'q', 'alerts_only']) params.delete(key);
    if (agent !== 'all') params.set('agent', agent);
    if (q.trim()) params.set('q', q.trim());
    if (onlyAlerts) params.set('alerts_only', '1');
    const query = params.toString();
    history.replaceState(null, '', query ? `?${query}` : window.location.pathname);
  }, [filtersReady, agent, q, onlyAlerts]);

  useEffect(() => {
    let localViews: SavedActivityView[] = [];
    try {
      localViews = JSON.parse(localStorage.getItem('nimbus_activity_views') || '[]');
    } catch {
      /* ignore */
    }
    api
      .get<{ views: SavedActivityView[] }>('/api/me/saved-views/activity')
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setSavedViews(localViews);
          await api.put('/api/me/saved-views/activity', { views: localViews });
        } else {
          setSavedViews(data.views);
        }
        setSavedViewsError(false);
      })
      .catch(() => {
        setSavedViews(localViews);
        setSavedViewsError(true);
      });
  }, []);

  const persistSavedViews = (next: SavedActivityView[]) => {
    setSavedViews(next);
    try {
      localStorage.setItem('nimbus_activity_views', JSON.stringify(next));
    } catch {
      /* ignore */
    }
    savedViewSaveQueue.current = savedViewSaveQueue.current
      .catch(() => undefined)
      .then(() => api.put('/api/me/saved-views/activity', { views: next }))
      .then(() => setSavedViewsError(false))
      .catch(() => setSavedViewsError(true));
  };

  const applySavedView = (view: SavedActivityView) => {
    setAgent(view.params.agent ?? 'all');
    setQ(view.params.q ?? '');
    setOnlyAlerts(view.params.alerts_only ?? false);
  };

  const saveCurrentView = () => {
    const entered = window.prompt('Name this Agent Activity view');
    const name = entered?.trim();
    if (!name) return;
    if (name.length > 60) {
      toast('Saved view names must be 60 characters or fewer.', 'error');
      return;
    }
    const params: SavedActivityView['params'] = { agent };
    if (q.trim()) params.q = q.trim();
    if (onlyAlerts) params.alerts_only = true;
    persistSavedViews([
      ...savedViews.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, params },
    ]);
  };

  const load = useCallback(async () => {
    if (!filtersReady || debouncedQ !== q.trim()) return;
    const version = queryVersion.current;
    try {
      const ev = await api.get('/api/audit/activity/page', {
        params: { agent, q: debouncedQ || undefined, alerts_only: onlyAlerts, limit: 200 },
      });
      if (version !== queryVersion.current) return;
      const items: Evt[] = ev.data.items.map((e: any) => ({
        ...e,
        action_description: e.description,
      }));
      setLatest(items);
      if (!olderRef.current.length) {
        setNextCursor(ev.data.next_before_seq);
        setHasMore(ev.data.has_more);
      }
      // Briefly highlight events that arrived since the last refresh (not on first load).
      const ids = new Set(items.map((e) => e.id));
      if (known.current) {
        const added = items.filter((e) => !known.current!.has(e.id)).map((e) => e.id);
        if (added.length) {
          setFresh(new Set(added));
          setTimeout(() => setFresh(new Set()), 4000);
        }
      }
      known.current = new Set([...(known.current ?? []), ...ids]);
      setError(null);
    } catch {
      if (version === queryVersion.current)
        setError('Could not load activity. Showing the last data received.');
    } finally {
      if (version === queryVersion.current) {
        setLoaded(true);
        setNow((n) => n + 1);
      }
    }
  }, [agent, debouncedQ, filtersReady, onlyAlerts, q]);
  const loadOlder = async () => {
    if (nextCursor === null) return;
    setLoadingMore(true);
    const version = queryVersion.current;
    try {
      const { data } = await api.get('/api/audit/activity/page', {
        params: {
          agent,
          q: debouncedQ || undefined,
          alerts_only: onlyAlerts,
          limit: 200,
          before_seq: nextCursor,
        },
      });
      if (version !== queryVersion.current) return;
      const page: Evt[] = data.items.map((e: any) => ({ ...e, action_description: e.description }));
      setOlder((existing) => {
        const appended = [...existing, ...page];
        olderRef.current = appended;
        return appended;
      });
      setNextCursor(data.next_before_seq);
      setHasMore(data.has_more);
    } catch {
      if (version === queryVersion.current) toast('Could not load older events.', 'error');
    } finally {
      setLoadingMore(false);
    }
  };
  // Keep "5m ago" labels honest between refreshes.
  useEffect(() => {
    const t = setInterval(() => setNow((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);
  useDataChanged(() => {
    load();
  });
  useEffect(() => {
    if (!filtersReady || debouncedQ !== q.trim()) return;
    queryVersion.current += 1;
    olderRef.current = [];
    known.current = null;
    setOlder([]);
    setNextCursor(null);
    setHasMore(false);
    setOpen(null);
    setFresh(new Set());
  }, [agent, debouncedQ, filtersReady, onlyAlerts, q]);
  useEffect(() => {
    if (!filtersReady || debouncedQ !== q.trim()) return;
    load();
    if (!live) return;
    const tick = () => {
      if (!document.hidden) load();
    };
    const t = setInterval(tick, 5000);
    document.addEventListener('visibilitychange', tick);
    return () => {
      clearInterval(t);
      document.removeEventListener('visibilitychange', tick);
    };
  }, [debouncedQ, filtersReady, load, live, q]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: events.length };
    for (const e of events) {
      const k = groupOf(e.actor);
      c[k] = (c[k] ?? 0) + 1;
    }
    return c;
  }, [events]);

  const alertCount = useMemo(() => events.filter((e) => isAlert(e.event_type)).length, [events]);
  const today = useMemo(() => events.filter((e) => dayLabel(e.created_at) === 'Today'), [events]);
  const todayAuto = today.filter((e) => groupOf(e.actor) !== 'people').length;
  const filtered = agent !== 'all' || !!q.trim() || onlyAlerts;
  const shown = events;

  const groups = useMemo(() => {
    const out: { day: string; items: Evt[] }[] = [];
    for (const e of shown) {
      const day = dayLabel(e.created_at);
      if (out.at(-1)?.day === day) out.at(-1)!.items.push(e);
      else out.push({ day, items: [e] });
    }
    return out;
  }, [shown]);

  const last = events[0];
  const downloadActivityCsv = async () => {
    setExporting(true);
    setExportError(null);
    try {
      const { data } = await api.get('/api/audit/activity/export', {
        params: { agent, q: q.trim() || undefined, alerts_only: onlyAlerts },
        responseType: 'blob',
      });
      downloadBlob('filtered-agent-activity.csv', data);
    } catch (cause) {
      const statusCode = (cause as { response?: { status?: number } })?.response?.status;
      setExportError(
        statusCode === 413
          ? 'The filtered audit trail exceeds 100,000 events. Add a search term or narrow the actor filter.'
          : 'Could not export the filtered audit trail.'
      );
    } finally {
      setExporting(false);
    }
  };
  return (
    <div className="page-container">
      <PageHeader
        title="Agent Activity"
        subtitle="Everything agents, connectors and people did, newest first. Read-only audit trail."
        actions={
          <>
            <span className="cc-live">
              <i className={`dot ${live ? 'on' : ''}`} />
              {live ? 'Live' : 'Paused'}
              {last ? ` · last event ${ago(last.created_at)}` : ''}
            </span>
            {canOpen('/settings/agents') && (
              <Link className="btn-secondary btn-sm" href="/settings/agents">
                Agent settings
              </Link>
            )}
            {canOpen('/audit') && (
              <Button
                size="sm"
                variant="secondary"
                disabled={exporting}
                title="Exports all matching events, up to 100,000, including events not loaded on this page."
                onClick={() => void downloadActivityCsv()}
              >
                {exporting ? 'Preparing CSV…' : 'Export filtered activity'}
              </Button>
            )}
            <Button size="sm" onClick={() => setLive((v) => !v)}>
              {live ? 'Pause' : 'Resume'}
            </Button>
            <Button size="sm" onClick={load}>
              Refresh
            </Button>
          </>
        }
      />
      {error && <Alert tone="error">{error}</Alert>}
      {exportError && (
        <Alert tone="error" onDismiss={() => setExportError(null)}>
          {exportError}
        </Alert>
      )}
      <AgentModeNotice mode={agentMode} />

      {savedViewsError && (
        <Alert tone="warning">
          Saved views are available on this device, but could not sync to your account.
        </Alert>
      )}
      <div className="ac-presets" aria-label="Saved Agent Activity views">
        {savedViews.map((view, index) => (
          <span key={`${view.name}-${index}`} className="ac-chip saved">
            <button onClick={() => applySavedView(view)}>{view.name}</button>
            <button
              aria-label={`Delete view ${view.name}`}
              onClick={() => persistSavedViews(savedViews.filter((_, row) => row !== index))}
            >
              ✕
            </button>
          </span>
        ))}
        <button className="ac-chip add" onClick={saveCurrentView}>
          + Save current view
        </button>
      </div>

      {loaded && (
        <div className="ac-tiles">
          <div className="ac-tile">
            <small>Events today</small>
            <b>{formatNumber(today.length)}</b>
            <em>
              {formatNumber(todayAuto)} automated · {formatNumber(today.length - todayAuto)} by
              people
            </em>
          </div>
          <button
            className={`ac-tile ${alertCount ? 'bad' : ''}`}
            onClick={() => setOnlyAlerts((v) => !v)}
            aria-pressed={onlyAlerts}
            disabled={alertCount === 0 && !onlyAlerts}
          >
            <small>Needs attention</small>
            <b>{formatNumber(alertCount)}</b>
            <em>
              {alertCount
                ? onlyAlerts
                  ? 'Showing only these. Click to show all'
                  : 'Failures and rejections. Click to filter'
                : 'No failures in the loaded window'}
            </em>
          </button>
          <div className="ac-tile">
            <small>Last event</small>
            <b style={{ fontSize: '1.15rem' }}>{last ? ago(last.created_at) : '—'}</b>
            <em>{last ? `${pretty(last.actor)}` : 'Nothing recorded yet'}</em>
          </div>
        </div>
      )}

      <>
        <div className="toolbar">
          <Segmented
            value={agent}
            onChange={setAgent}
            options={FILTERS.map((a) => ({
              value: a.key,
              label: `${a.label} ${counts[a.key] ?? 0}`,
            }))}
          />
          <input
            className="grow"
            type="search"
            placeholder="Search activity"
            aria-label="Search activity"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <Button
            size="sm"
            className={onlyAlerts ? 'act-on' : ''}
            aria-pressed={onlyAlerts}
            onClick={() => setOnlyAlerts((v) => !v)}
          >
            Needs attention{alertCount ? ` ${alertCount}` : ''}
          </Button>
          {filtered && (
            <Button
              size="sm"

              onClick={() => {
                setAgent('all');
                setQ('');
                setOnlyAlerts(false);
              }}
            >
              Clear filters
            </Button>
          )}
          <span className="toolbar-count">{formatNumber(events.length)} events loaded</span>
        </div>

        {!loaded ? (
          <SkeletonRows rows={8} label="Loading activity" />
        ) : shown.length === 0 ? (
          <EmptyState title="No activity">
            <p>
              {events.length
                ? 'Nothing matches these filters in the loaded events.'
                : filtered
                  ? 'No audit events match these filters.'
                  : 'Activity appears once the daily batch or an agent runs.'}
            </p>
            {events.length > 0 && filtered && (
              <Button
                size="sm"

                onClick={() => {
                  setAgent('all');
                  setQ('');
                  setOnlyAlerts(false);
                }}
              >
                Clear filters
              </Button>
            )}
            {events.length > 0 && filtered && hasMore && (
              <Button
                size="sm"

                disabled={loadingMore}
                onClick={loadOlder}
                style={{ marginLeft: '.5rem' }}
              >
                Search older events
              </Button>
            )}
          </EmptyState>
        ) : (
          groups.map((g) => (
            <section key={g.day}>
              <h3 className="act-day">
                {g.day}
                <span className="cc-count">{g.items.length}</span>
              </h3>
              <DataTable
                columns={[
                  {
                    key: 'when',
                    header: 'When',
                    width: '7rem',
                    render: (e) => (
                      <span className="text-sm nowrap" title={formatDateTime(e.created_at)}>
                        {ago(e.created_at)}
                      </span>
                    ),
                  },
                  {
                    key: 'actor',
                    header: 'Actor',
                    width: '14rem',
                    render: (e) => (
                      <span className="act-actor">
                        <i
                          className={`act-av ${groupOf(e.actor) === 'people' ? 'person' : ''}`}
                          aria-hidden
                        >
                          {initials(e.actor)}
                        </i>
                        <span>
                          <span className="font-medium">{pretty(e.actor)}</span>
                          <small className="act-sub" style={{ display: 'block' }}>
                            {groupLabel(e.actor)}
                          </small>
                        </span>
                      </span>
                    ),
                  },
                  {
                    key: 'what',
                    header: 'What happened',
                    render: (e) => (
                      <>
                        <span className={`badge badge-sm ${tone(e.event_type)}`}>
                          {pretty(e.event_type)}
                        </span>{' '}
                        <span className="act-desc">{e.action_description}</span>
                      </>
                    ),
                  },
                  {
                    key: 'case',
                    header: 'Case',
                    width: '5rem',
                    stopRowClick: true,
                    render: (e) =>
                      e.case_id ? (
                        <CaseLink id={e.case_id}>Open</CaseLink>
                      ) : (
                        <span className="act-sub">-</span>
                      ),
                  },
                ]}
                rows={g.items}
                rowKey={(e) => String(e.id)}
                onRowClick={(e) => setOpen(open === e.id ? null : e.id)}
                rowClassName={(e) =>
                  `ac-row ${open === e.id ? 'open' : ''} ${fresh.has(e.id) ? 'act-new' : ''}`
                }
                expandedKey={open === null ? null : String(open)}
                renderDetail={(e) => (
                  <div className="act-detail">
                    {e.after?.result ? (
                      <>
                        <b>{e.after.result.summary}</b>
                        <ul>
                          {e.after.result.hypotheses?.map((h: any, i: number) => (
                            <li key={i}>
                              <b>{h.cause}</b>: {h.verdict} ({h.cited_ids?.length ?? 0} cited).{' '}
                              {h.reasoning}
                            </li>
                          ))}
                        </ul>
                        <small>
                          Model {e.after.model}
                          {e.after.via ? ` via ${e.after.via}` : ''} ·{' '}
                          {e.after.tool_calls?.length ?? 0} tool calls ·{' '}
                          {formatNumber(e.after.input_tokens + e.after.output_tokens)} tokens
                          {typeof e.after.cost_usd === 'number'
                            ? ` · $${e.after.cost_usd.toFixed(3)}`
                            : ''}
                          {e.after.result.rejected_citations?.length
                            ? ` · ${e.after.result.rejected_citations.length} citations rejected`
                            : ''}
                        </small>
                      </>
                    ) : e.after ? (
                      <pre>{JSON.stringify(e.after, null, 2)}</pre>
                    ) : (
                      <small>No further detail recorded.</small>
                    )}
                    <small>
                      Audit event #{(e as any).seq} · {formatDateTime(e.created_at)}
                      {e.after && (
                        <>
                          {' '}
                          ·{' '}
                          <CopyButton
                            value={JSON.stringify(e.after, null, 2)}
                            label="Copy detail"
                          />
                        </>
                      )}
                    </small>
                  </div>
                )}
              />
            </section>
          ))
        )}

        {loaded && hasMore && (
          <div className="act-more">
            <span className="hint">
              Showing {formatNumber(events.length)} events; older events are available.
            </span>
            <Button disabled={loadingMore} onClick={loadOlder}>
              {loadingMore ? 'Loading…' : 'Load older events'}
            </Button>
          </div>
        )}
      </>
    </div>
  );
}

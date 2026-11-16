'use client';

import { useEffect, useRef, useState } from 'react';
import { api } from '@/lib/api';
import { Alert, useNameDialog } from '@/components/ui';

export type SummaryFilters = {
  store: string;
  dateFrom: string;
  dateTo: string;
  search: string;
  businessDate?: string;
  groupBy?: 'register' | 'cashier';
};

type SavedSummaryView = {
  name: string;
  params: {
    store_id?: string;
    date_from?: string;
    date_to?: string;
    business_date?: string;
    group_by?: 'register' | 'cashier';
    q?: string;
  };
};

type Props = {
  scope: 'item_summary' | 'tender_summary' | 'misc_totals' | 'register_summary';
  label: string;
  filters: SummaryFilters;
  onApply: (filters: SummaryFilters) => void;
  saveDisabled?: boolean;
};

export function SummarySavedViews({ scope, label, filters, onApply, saveDisabled }: Props) {
  const [views, setViews] = useState<SavedSummaryView[]>([]);
  const [syncError, setSyncError] = useState(false);
  const [viewsReady, setViewsReady] = useState(false);
  const saveQueue = useRef<Promise<unknown>>(Promise.resolve());
  const storageKey = `zetsa_${scope}_views`;

  useEffect(() => {
    let localViews: SavedSummaryView[] = [];
    try {
      const parsed: unknown = JSON.parse(localStorage.getItem(storageKey) || '[]');
      if (Array.isArray(parsed)) localViews = parsed;
    } catch {
      /* Ignore a damaged local fallback and continue with the account copy. */
    }
    api
      .get<{ views: SavedSummaryView[] }>(`/api/me/saved-views/${scope}`)
      .then(async ({ data }) => {
        if (data.views.length === 0 && localViews.length > 0) {
          setViews(localViews);
          await api.put(`/api/me/saved-views/${scope}`, { views: localViews });
        } else {
          setViews(data.views);
        }
        setSyncError(false);
        setViewsReady(true);
      })
      .catch(() => {
        setViews(localViews);
        setSyncError(true);
        setViewsReady(true);
      });
  }, [scope, storageKey]);

  const persist = (next: SavedSummaryView[]) => {
    setViews(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      /* Keep the in-memory and account copies usable if storage is unavailable. */
    }
    saveQueue.current = saveQueue.current
      .catch(() => undefined)
      .then(() => api.put(`/api/me/saved-views/${scope}`, { views: next }))
      .then(() => setSyncError(false))
      .catch(() => setSyncError(true));
  };

  const { ask: askViewName, dialog: viewNameDialog } = useNameDialog();
  const saveCurrent = async () => {
    const name = await askViewName(
      `Name this ${label} view`,
      views.map((view) => view.name)
    );
    if (!name) return;
    const params: SavedSummaryView['params'] = {};
    if (filters.store) params.store_id = filters.store;
    if (filters.dateFrom) params.date_from = filters.dateFrom;
    if (filters.dateTo) params.date_to = filters.dateTo;
    if (filters.businessDate) params.business_date = filters.businessDate;
    if (filters.groupBy) params.group_by = filters.groupBy;
    if (filters.search) params.q = filters.search;
    persist([
      ...views.filter((view) => view.name.toLocaleLowerCase() !== name.toLocaleLowerCase()),
      { name, params },
    ]);
  };

  return (
    <>
      {viewNameDialog}
      {syncError && (
        <Alert tone="warning">
          Saved views are available on this device, but could not sync to your account.
        </Alert>
      )}
      <div className="ac-presets" aria-label={`Saved ${label} views`}>
        {views.map((view) => (
          <span key={view.name} className="ac-chip saved">
            <button
              onClick={() =>
                onApply({
                  store: view.params.store_id ?? '',
                  dateFrom: view.params.date_from ?? '',
                  dateTo: view.params.date_to ?? '',
                  search: view.params.q ?? '',
                  businessDate: view.params.business_date ?? '',
                  groupBy: view.params.group_by ?? 'register',
                })
              }
            >
              {view.name}
            </button>
            <button
              aria-label={`Delete view ${view.name}`}
              onClick={() => persist(views.filter((item) => item.name !== view.name))}
            >
              ✕
            </button>
          </span>
        ))}
        <button
          className="ac-chip add"
          disabled={!viewsReady || saveDisabled}
          onClick={saveCurrent}
        >
          {viewsReady ? '+ Save current view' : 'Loading saved views…'}
        </button>
      </div>
    </>
  );
}

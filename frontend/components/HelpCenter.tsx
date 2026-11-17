'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { HELP_TOPICS, HELP_WORKFLOWS } from '@/lib/help';
import { screenFor, useMe } from '@/components/MeProvider';

export function HelpCenter() {
  const pathname = usePathname();
  const { me } = useMe();
  const dialog = useRef<HTMLDialogElement>(null);
  const search = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const currentScreen = screenFor(me?.screens, pathname);
  const permittedTopics = useMemo(
    () =>
      HELP_TOPICS.filter((topic) =>
        me?.screens.some((screen) => screen.allowed && screen.path === topic.path)
      ),
    [me?.screens]
  );
  const canOpenPath = (path: string) => !!screenFor(me?.screens, path.split('#')[0])?.allowed;
  const permittedWorkflows = HELP_WORKFLOWS.filter((workflow) =>
    workflow.steps.every((step) => canOpenPath(step.path))
  );
  const currentTopic = permittedTopics.find((topic) => topic.path === currentScreen?.path) ?? {
    path: currentScreen?.path ?? pathname,
    title: currentScreen?.label ?? 'Using the workspace',
    summary: 'Use the filters and row details to find the records that need your attention.',
    steps: [
      'Set a narrow date, status, or store filter before reviewing a large queue.',
      'Open a record to see its source details, history, and available actions.',
      'Use the audit trail to confirm when a change was made and who made it.',
    ],
  };
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matchingTopics = permittedTopics.filter((topic) =>
    `${topic.title} ${topic.summary} ${topic.steps.join(' ')} ${topic.notes?.join(' ') ?? ''}`
      .toLocaleLowerCase()
      .includes(normalizedQuery)
  );
  const matchingWorkflows = permittedWorkflows.filter((workflow) =>
    `${workflow.title} ${workflow.summary} ${workflow.steps
      .map((step) => `${step.title} ${step.detail}`)
      .join(' ')}`
      .toLocaleLowerCase()
      .includes(normalizedQuery)
  );
  const topicSections = Array.from(
    matchingTopics.reduce((sections, topic) => {
      const section = topic.section ?? 'Workflows';
      sections.set(section, [...(sections.get(section) ?? []), topic]);
      return sections;
    }, new Map<string, typeof matchingTopics>())
  );

  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) element.showModal();
    if (!open && element.open) element.close();
    if (open) search.current?.focus();
  }, [open]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target?.isContentEditable ||
        ['INPUT', 'TEXTAREA', 'SELECT'].includes(target?.tagName ?? '');
      if (event.key === '?' && !typing && !event.metaKey && !event.ctrlKey && !event.altKey) {
        event.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);

  const close = () => {
    setOpen(false);
    setQuery('');
  };

  return (
    <>
      <button
        type="button"
        className="nav-icon-btn help-trigger"
        aria-label="Open help for this page"
        aria-haspopup="dialog"
        title="Help (?)"
        onClick={() => setOpen(true)}
      >
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.9"
          strokeLinecap="round"
          strokeLinejoin="round"
          aria-hidden="true"
        >
          <circle cx="12" cy="12" r="9" />
          <path d="M9.7 9a2.4 2.4 0 1 1 4.3 1.5c-1.1 1.2-2 1.5-2 3" />
          <path d="M12 17h.01" />
        </svg>
      </button>
      <dialog
        ref={dialog}
        className="help-center"
        aria-label="Page help and workflows"
        onClose={() => {
          setOpen(false);
          setQuery('');
        }}
        onClick={(event) => {
          if (event.target === dialog.current) close();
        }}
      >
        <div className="help-center-panel">
          <header className="help-center-header">
            <div>
              <span className="help-center-kicker">ReSA workflow guide</span>
              <h2>Help and guidance</h2>
            </div>
            <div className="cluster">
              <Link className="btn-secondary btn-sm" href="/tasks" onClick={close}>
                Browse tasks
              </Link>
              <button className="command-palette-close" type="button" onClick={close}>
                Close
              </button>
            </div>
          </header>
          <section className="help-current" aria-labelledby="help-current-title">
            <div className="help-current-mark" aria-hidden="true">
              ?
            </div>
            <div>
              <h3 id="help-current-title">{currentTopic.title}</h3>
              <p>{currentTopic.summary}</p>
            </div>
            <ol>
              {currentTopic.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
            {currentTopic.notes?.map((note) => (
              <p className="help-note" key={note}>
                {note}
              </p>
            ))}
          </section>
          <div className="help-search-wrap">
            <label htmlFor="help-search">Search help topics</label>
            <input
              id="help-search"
              ref={search}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Try “close a store day”"
            />
          </div>
          <div className="help-topic-list">
            {matchingWorkflows.length > 0 && (
              <section className="help-guide-list" aria-label="Guided workflows">
                <h3>{normalizedQuery ? 'Matching guided workflows' : 'Guided workflows'}</h3>
                {matchingWorkflows.map((workflow) => (
                  <details className="help-guide" key={workflow.id}>
                    <summary>
                      <span>
                        <strong>{workflow.title}</strong>
                        <small>{workflow.summary}</small>
                      </span>
                      <span className="help-guide-count">{workflow.steps.length} steps</span>
                    </summary>
                    <ol>
                      {workflow.steps.map((step) => (
                        <li key={`${workflow.id}-${step.title}`}>
                          <Link href={step.path} onClick={close}>
                            {step.title}
                          </Link>
                          <p>{step.detail}</p>
                        </li>
                      ))}
                    </ol>
                  </details>
                ))}
              </section>
            )}
            {matchingTopics.length > 0 && (
              <section className="help-screen-guides" aria-label="Screen guides">
                <h3>{normalizedQuery ? 'Matching screen guides' : 'Screen guides'}</h3>
                {topicSections.map(([section, topics]) => (
                  <div className="help-topic-section" key={section}>
                    <h4>{section}</h4>
                    {topics.map((topic) => (
                      <Link
                        className="help-topic"
                        href={topic.path}
                        key={topic.path}
                        onClick={close}
                      >
                        <span>
                          <strong>{topic.title}</strong>
                          <small>{topic.summary}</small>
                        </span>
                        <span aria-hidden="true">↗</span>
                      </Link>
                    ))}
                  </div>
                ))}
              </section>
            )}
            {matchingTopics.length === 0 && matchingWorkflows.length === 0 && (
              <p className="help-empty">
                No matching help topics. Try a store-day or transaction task.
              </p>
            )}
          </div>
          <footer className="help-center-footer">
            <span>
              Press <kbd>?</kbd> to open help from any page.
            </span>
            <span>Guidance follows ReSA 15.0.1 workflows and ZeTSA controls.</span>
          </footer>
        </div>
      </dialog>
    </>
  );
}

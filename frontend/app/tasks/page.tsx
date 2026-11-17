'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { useMe, screenFor } from '@/components/MeProvider';
import { Badge, Card, PageHeader, Toolbar } from '@/components/ui';
import { HELP_WORKFLOWS } from '@/lib/help';

export default function TasksPage() {
  const { me } = useMe();
  const [query, setQuery] = useState('');
  const workflows = useMemo(
    () =>
      HELP_WORKFLOWS.filter((workflow) =>
        workflow.steps.every((step) => screenFor(me?.screens, step.path.split('#')[0])?.allowed)
      ),
    [me?.screens]
  );
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const matches = workflows.filter((workflow) =>
    `${workflow.title} ${workflow.summary} ${workflow.steps
      .map((step) => `${step.title} ${step.detail}`)
      .join(' ')}`
      .toLocaleLowerCase()
      .includes(normalizedQuery)
  );

  return (
    <div className="page-container stack">
      <PageHeader
        title="Guided Tasks"
        subtitle="Follow common audit workflows across the screens available to your role."
      />
      <Toolbar count={`${matches.length} task${matches.length === 1 ? '' : 's'}`}>
        <input
          className="grow"
          type="search"
          aria-label="Search guided tasks"
          placeholder="Search tasks, steps, or actions"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </Toolbar>
      <p className="hint">
        A workflow appears only when you can open every screen in its sequence. Your task list can
        differ from another user’s role.
      </p>
      {matches.length ? (
        <div className="stack">
          {matches.map((workflow) => (
            <Card key={workflow.id}>
              <details className="help-guide">
                <summary>
                  <span>
                    <strong>{workflow.title}</strong>
                    <small>{workflow.summary}</small>
                  </span>
                  <Badge variant="secondary" size="sm">
                    {workflow.steps.length} {workflow.steps.length === 1 ? 'step' : 'steps'}
                  </Badge>
                </summary>
                <ol>
                  {workflow.steps.map((step) => (
                    <li key={`${workflow.id}-${step.title}`}>
                      <Link href={step.path}>{step.title}</Link>
                      <p>{step.detail}</p>
                    </li>
                  ))}
                </ol>
              </details>
            </Card>
          ))}
        </div>
      ) : (
        <Card>
          <div className="empty-state">
            <strong>{normalizedQuery ? 'No matching tasks' : 'No tasks available'}</strong>
            <p>
              {normalizedQuery
                ? 'Try another task name or step.'
                : 'No complete guided workflow is currently available to your role. Use the main menu and page help for individual tasks.'}
            </p>
          </div>
        </Card>
      )}
    </div>
  );
}

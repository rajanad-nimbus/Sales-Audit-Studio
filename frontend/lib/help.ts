export interface HelpTopic {
  path: string;
  title: string;
  summary: string;
  steps: string[];
  section?: string;
  notes?: string[];
}

export interface HelpWorkflowStep {
  title: string;
  path: string;
  detail: string;
}

export interface HelpWorkflow {
  id: string;
  title: string;
  summary: string;
  steps: HelpWorkflowStep[];
}

/** Cross-screen procedures based on the ReSA guide's daily operating sequence. */
export const HELP_WORKFLOWS: HelpWorkflow[] = [
  {
    id: 'audit-store-day',
    title: 'Audit a store day',
    summary: 'Review the day’s balances and resolve the exceptions before sign-off.',
    steps: [
      {
        title: 'Check day readiness',
        path: '/store-days',
        detail: 'Confirm the store and business date have the expected transaction data.',
      },
      {
        title: 'Review store-day totals and balances',
        path: '/store-days',
        detail: 'Compare calculated totals, declared values, variances, and over/short balances.',
      },
      {
        title: 'Resolve audit errors and cases',
        path: '/audit-errors',
        detail: 'Trace each exception to its source and record the governed correction or decision.',
      },
      {
        title: 'Reconcile declarations and deposits',
        path: '/cash-office',
        detail: 'Confirm cash office records explain the day’s register and cashier balances.',
      },
      {
        title: 'Return to the day and sign off',
        path: '/store-days',
        detail: 'Confirm the remaining checks are clear, then sign off when the day is ready.',
      },
    ],
  },
  {
    id: 'deliver-audited-day',
    title: 'Deliver a closed store day',
    summary: 'Confirm closure, export the audited records, and follow the acknowledgement.',
    steps: [
      {
        title: 'Confirm the day is closed',
        path: '/store-days',
        detail: 'Review the final status and the audit version that will be delivered.',
      },
      {
        title: 'Review the destination batch',
        path: '/exports',
        detail: 'Check the exported record count, receiver reference, and acknowledgement state.',
      },
      {
        title: 'Resolve delivery failures',
        path: '/exports',
        detail: 'Inspect the destination response and retry only after resolving an uncertain prior attempt.',
      },
    ],
  },
  {
    id: 'trace-and-correct-transaction',
    title: 'Trace and correct a transaction',
    summary: 'Move from an audit exception to a reviewed correction while preserving source history.',
    steps: [
      {
        title: 'Find the exception',
        path: '/audit-errors',
        detail: 'Filter by date, store, error family, and status to locate the affected case.',
      },
      {
        title: 'Inspect transaction evidence',
        path: '/transactions',
        detail: 'Compare the source record, items, tenders, references, and prior revisions.',
      },
      {
        title: 'Prepare a correction proposal',
        path: '/corrections/new',
        detail: 'Choose the exact transaction or detail row and explain the evidence for the change.',
      },
      {
        title: 'Review the Finance decision',
        path: '/corrections',
        detail: 'Confirm an independent approver accepted or rejected the proposal.',
      },
      {
        title: 'Recheck totals and audit results',
        path: '/store-days',
        detail: 'Recalculate affected store days and confirm the exception is resolved.',
      },
    ],
  },
  {
    id: 'review-and-promote-audit-control',
    title: 'Review and promote an audit control',
    summary: 'Preview rule or total changes, collect independent approval, and verify the result.',
    steps: [
      {
        title: 'Draft the total or rule change',
        path: '/settings/controls',
        detail: 'Set the target, threshold, scope, effective dates, and rule order.',
      },
      {
        title: 'Preview affected records',
        path: '/settings/controls',
        detail: 'Review the sample matches and impact counts before submitting the worksheet.',
      },
      {
        title: 'Submit for independent approval',
        path: '/settings/controls',
        detail: 'A different authorized person approves activation; the author cannot approve their own change.',
      },
      {
        title: 'Verify the active control',
        path: '/store-days',
        detail: 'Run the audit on a representative day and review resulting totals and exceptions.',
      },
    ],
  },
  {
    id: 'define-total',
    title: 'Define or revise a total',
    summary: 'Build a governed store-day total and review its effect before activation.',
    steps: [
      {
        title: 'Open Total Definitions and start a draft',
        path: '/settings/controls',
        detail: 'Choose the total category, balancing level, effective dates, and raw or combined basis.',
      },
      {
        title: 'Choose data realms and total fields',
        path: '/settings/controls',
        detail: 'For raw totals, select the transaction and needed detail realms, review approved joins, and configure the measure, restrictions, and up to three roll-ups.',
      },
      {
        title: 'Preview the change',
        path: '/settings/controls',
        detail: 'Review affected store days and compare current values with the proposed total.',
      },
      {
        title: 'Submit for independent approval',
        path: '/settings/controls',
        detail: 'The author cannot approve their own revision; an authorized Finance user reviews and activates it.',
      },
      {
        title: 'Recalculate a representative store day',
        path: '/store-days',
        detail: 'Confirm the active revision produced the expected amount and did not introduce unexplained variances.',
      },
    ],
  },
  {
    id: 'resolve-transaction-gap',
    title: 'Resolve a missing transaction number',
    summary: 'Trace a register sequence gap and correct it only when the source evidence supports a change.',
    steps: [
      {
        title: 'Find the affected store day',
        path: '/store-days',
        detail: 'Filter the business date and store, then identify unresolved transaction-number gaps.',
      },
      {
        title: 'Review the register sequence and source context',
        path: '/store-days',
        detail: 'Confirm the missing number, register, and neighboring transactions before deciding whether it is truly absent.',
      },
      {
        title: 'Submit a correction proposal with evidence',
        path: '/store-days',
        detail: 'Add the missing transaction only from verified source information, or dismiss the gap with a reason when the number was legitimately unused.',
      },
      {
        title: 'Review the independent Finance decision',
        path: '/corrections',
        detail: 'A different authorized reviewer accepts or rejects the proposed correction.',
      },
      {
        title: 'Re-total and verify the store day',
        path: '/store-days',
        detail: 'Confirm the gap is resolved and recheck totals and audit errors before sign-off.',
      },
    ],
  },
  {
    id: 'reconcile-store-cash-bank',
    title: 'Reconcile store cash and bank activity',
    summary: 'Trace a store-day cash difference through declarations, deposits, and bank transactions.',
    steps: [
      {
        title: 'Review store declarations and deposits',
        path: '/cash-office',
        detail: 'Compare declared cash, deposit records, and the store-day balances for the selected location and date.',
      },
      {
        title: 'Find the corresponding bank activity',
        path: '/ach',
        detail: 'Match bank transactions to the expected deposit and settlement period.',
      },
      {
        title: 'Record or review a governed adjustment',
        path: '/ach',
        detail: 'Use a reasoned adjustment when source evidence supports it; a separate reviewer approves the change.',
      },
      {
        title: 'Return to the store day',
        path: '/store-days',
        detail: 'Recalculate and confirm that the cash difference is explained before sign-off and close.',
      },
    ],
  },
  {
    id: 'trace-import-to-audit',
    title: 'Trace an incoming feed',
    summary: 'Follow a delivery from receipt and validation to the store-day audit.',
    steps: [
      {
        title: 'Find the source delivery',
        path: '/imports#feeds',
        detail: 'Search by source, received date, or delivery ID and review the record counts.',
      },
      {
        title: 'Review validation and quarantine',
        path: '/imports#quarantine',
        detail: 'Inspect quarantined record metadata and correct the source issue before reloading.',
      },
      {
        title: 'Check batch and reconciliation status',
        path: '/imports#runs',
        detail: 'Confirm the batch completed and note any automatic reconciliation failures.',
      },
      {
        title: 'Inspect the affected store day',
        path: '/store-days',
        detail: 'Review the latest totals, exceptions, and sign-off readiness for the business date.',
      },
    ],
  },
];

/** Short, task-oriented help based on the ReSA 15.0.1 guide and ZeTSA workflows. */
export const HELP_TOPICS: HelpTopic[] = [
  {
    path: '/tasks',
    section: 'Workspace',
    title: 'Follow guided tasks',
    summary: 'Find common retail sales-audit procedures and follow the screens available to your role.',
    steps: [
      'Search by the business process, screen, or action you need to complete.',
      'Expand a workflow to review its steps in order.',
      'Open an available step to continue the task in that workspace.',
    ],
  },
  {
    path: '/',
    title: 'Sales audit workspace',
    summary: 'Start with store-day readiness, then work the exceptions that prevent sign-off.',
    steps: [
      'Review store days waiting for imports, totals, or exception resolution.',
      'Open the highest-priority case or audit error and follow its source details.',
      'Return to the store day to confirm the remaining checks before close.',
    ],
  },
  {
    path: '/store-days',
    title: 'Audit a store day',
    summary: 'Use one business date and location as the unit of reconciliation and close.',
    steps: [
      'Confirm the expected POS and OMS feeds have arrived and reconciled.',
      'Filter the Store Days queue by data status and audit status to focus on incomplete feeds, required re-totalling, pending errors, or audited days.',
      'Review totals, cash differences, and open audit errors for the day.',
      'Resolve or document outstanding work, then sign off and close when ready.',
      'Copy the workspace link to share the current Store Days queue and filters.',
    ],
    notes: [
      'A closed day retains its audit history; corrections use the governed correction flow.',
    ],
  },
  {
    path: '/imports',
    title: 'Load transactional data',
    summary: 'Trace each incoming file from validation through reconciliation and audit.',
    steps: [
      'Choose the configured source and submit a file that matches its feed contract.',
      'Review validation findings and reconcile accepted, duplicate, and rejected records.',
      'Review batch history by date or status, open an exact run to inspect feed counts, errors and store-day outcomes, download its complete run report, and copy its link for a handoff.',
      'If automatic reconciliation fails after an upload, retry it for that business date from the upload result.',
      'Search source deliveries by source, received date, or delivery ID to trace record counts and quarantine outcomes.',
      'Search quarantined records by source, received date, record ID, or validation reason; export matching metadata when follow-up is needed.',
    ],
    notes: ['A preflight identifies file issues without posting transactions.'],
  },
  {
    path: '/audit-errors',
    title: 'Resolve audit errors',
    summary: 'Find recurring validation failures and move each affected case toward resolution.',
    steps: [
      'Filter by error type, status, or store to focus the work queue.',
      'Open a case and inspect the linked transactions and source evidence.',
      'Correct the underlying data or request a reasoned exception when policy allows.',
    ],
  },
  {
    path: '/cases',
    title: 'Investigate cases',
    summary: 'Cases collect exceptions and the evidence needed to explain a decision.',
    steps: [
      'Search or filter by store, status, priority, owner, or due date.',
      'Review the case timeline, linked transactions, and reconciliation details.',
      'Record the decision and resolve the remaining exceptions before closing the case.',
      'Save a filtered view to sync it to your account and reopen the same case queue later.',
    ],
  },
  {
    path: '/actions',
    title: 'Triage open work',
    summary: 'Prioritize cases, approvals, and overdue items from one queue.',
    steps: [
      'Sort by priority or due date to find time-sensitive work.',
      'Open the underlying case, correction, or approval before taking action.',
      'Recheck the queue after the action to confirm the work item changed state.',
    ],
  },
  {
    path: '/transactions',
    title: 'Research a transaction',
    summary: 'Use transaction detail to trace source records, tenders, items, and audit changes.',
    steps: [
      'Search with a transaction ID, receipt reference, store, register, or cashier.',
      'Check source lineage and compare the transaction with its store-day totals.',
      'If a repair is needed, submit a proposed correction with a reason for review.',
    ],
  },
  {
    path: '/corrections',
    title: 'Review corrections',
    summary:
      'Review transaction, discount, tax, customer, employee, and paid-out corrections without rewriting source records.',
    steps: [
      'Review pending transaction and paid-out detail corrections from the central queue.',
      'Inspect the proposed before-and-after values and supporting reason.',
      'For tax corrections, review the selected tax row, rate, taxable amount, and tax amount together.',
      'Line tax proposals target one item line and capture its tax code, rate, and amount before approval.',
      'Discount proposals can add a missing receipt discount, correct one recorded discount, or remove an incorrect entry. Updates and removals retain the prior fields; approval recalculates transaction detail balance and invalidates store-day totals.',
      'Customer attribute proposals capture the existing value and require a reason before Finance approval.',
      'Cashier and salesperson corrections capture existing values and require Finance approval before changing transaction detail.',
      'Approve or reject with the supporting evidence, then review the audit trail.',
      'After financial changes, verify recalculated totals and store-day audit results.',
    ],
  },
  {
    path: '/foundation',
    title: 'Maintain reference data',
    summary: 'Keep stores, tenders, and validation reference values aligned with feed data.',
    steps: [
      'Select a data category and search by code or name.',
      'Download a template or export the filtered records for review.',
      'Validate an upload and resolve row-level errors before applying changes.',
      'Find a named process by its name, file, uploader or ID; filter by status, inspect its recorded outcomes and row issues, copy a link to reopen the exact process, and export all matching history.',
    ],
  },
  {
    path: '/controls',
    title: 'Govern audit controls',
    summary: 'Draft, preview, and approve changes to totals, rules, and financial mappings.',
    steps: [
      'Create a revision or worksheet and define its scope and effective dates.',
      'Preview the affected stores, transactions, or totals before activation.',
      'Use a separate authorized approver to activate governed changes.',
    ],
  },
  {
    path: '/settings/controls',
    title: 'Maintain totals and audit rules',
    summary: 'Use governed definitions to calculate totals and detect transaction exceptions.',
    steps: [
      'Select the transaction and detail realms, review their approved joins, then choose parameters, restrictions, and rollups from those realms.',
      'Review the impact preview and compare it with expected business totals.',
      'For transaction-rule worksheets, preview would-trigger rows and download the complete filtered match set for review.',
      'Submit for independent approval before the definition becomes active.',
    ],
  },
  {
    path: '/cash-office',
    title: 'Reconcile the cash office',
    summary: 'Compare declared cash activity with calculated balances and deposits.',
    steps: [
      'Select the store day and review register or cashier balances.',
      'Trace over/short differences to declarations and register events.',
      'Record a reason for governed adjustments and confirm the updated balance.',
      'Filter Over / Short to one store, then review by store, register, cashier, or day; summary totals cover the full filtered set while the table pages through groups.',
      'Export the complete filtered Over / Short result to CSV when sharing or reconciling the report.',
      'Filter vouchers by status, issuing store, or number; the URL preserves these filters for sharing.',
      'Export the complete matching live or retired ledger for review.',
    ],
  },
  {
    path: '/ach',
    title: 'Review bank transactions',
    summary: 'Match bank activity to store deposits and document adjustments.',
    steps: [
      'Filter unmatched or disputed bank transactions.',
      'Compare bank references, deposit dates, and store records.',
      'Apply a reasoned adjustment or mark the match, then review the audit trail.',
    ],
  },
  {
    path: '/exports',
    title: 'Deliver audited data',
    summary: 'Send eligible audited records to configured destinations and track acknowledgements.',
    steps: [
      'Review destination settings and the records eligible for delivery.',
      'Prepare or retry a delivery and retain its batch reference.',
      'Filter tracking by state or record, download the matching CSV, and confirm receiver acknowledgement before retrying uncertain outcomes.',
      'Search export history by destination, dataset, status, date, batch ID, receiver reference, or error.',
    ],
  },
  {
    path: '/preferences',
    title: 'Set your preferences',
    summary: 'Choose regional formats and accessibility settings for your workspace.',
    steps: [
      'Set your territory, date, number, and time-zone preferences.',
      'Choose contrast and text-size options that make the workspace easier to read.',
      'Save changes and return to your work; preferences apply across supported screens.',
    ],
  },
  {
    path: '/total-history',
    section: 'Store day audit',
    title: 'Trace total changes',
    summary: 'Review how a calculated total changed and which upstream activity affected it.',
    steps: [
      'Filter by store, business date, and total to narrow the history.',
      'Compare calculated, declared, variance, and previous-version values.',
      'Export all matching revisions to CSV for external reconciliation or review.',
      'Open the related store day or transaction to investigate the underlying change.',
    ],
    notes: ['Recalculation and approved corrections remain visible in the history.'],
  },
  {
    path: '/misc-totals',
    section: 'Store day audit',
    title: 'Review miscellaneous totals',
    summary:
      'Inspect non-transaction totals reported by stores and compare them with configured controls.',
    steps: [
      'Choose a business date and store, then review the reported total values.',
      'Compare the store declaration with the calculated result and any configured tolerance.',
      'Open the store day to follow up on differences before sign-off.',
      'Save frequently used store, date, and search filters as a view synced to your account.',
    ],
  },
  {
    path: '/item-summary',
    section: 'Store day audit',
    title: 'Analyze item sales',
    summary: 'Summarize sales and returns by item across the selected store-day scope.',
    steps: [
      'Set a business date range and store or merchandise filter.',
      'Compare units, sales, discounts, and returns for the items in scope.',
      'Open a matching transaction when an item total needs source-level review.',
      'Save frequently used store, date, and item filters as a view synced to your account.',
    ],
  },
  {
    path: '/tender-summary',
    section: 'Store day audit',
    title: 'Reconcile tender totals',
    summary: 'Compare tender activity across stores, business dates, and transaction types.',
    steps: [
      'Filter to the store and date range you need to review.',
      'Compare tender amounts and counts with store-day totals and cash-office declarations.',
      'Open contributing transactions or register activity to investigate a variance.',
      'Save frequently used store, date, and search filters as a view synced to your account.',
    ],
  },
  {
    path: '/ledger',
    section: 'Store day audit',
    title: 'Review the general ledger view',
    summary: 'Preview mapped and unmapped financial journal lines across a store and date range.',
    steps: [
      'Select a store and business-date range of up to 366 days.',
      'Review debit and credit lines, account mappings, and balancing status.',
      'Open a transaction ID to inspect its source detail, then use Back to return to the journal.',
      'Download the full journal preview; unmapped transactions remain in the file for reconciliation.',
      'Resolve missing mappings in configuration before preparing downstream delivery.',
    ],
  },
  {
    path: '/register-events',
    section: 'Store day audit',
    title: 'Investigate register events',
    summary: 'Review non-sale register activity such as paid-outs, pickups, and other events.',
    steps: [
      'Filter by store, business date, register, cashier, or event type.',
      'Compare event amounts and references with the cash-office declaration.',
      'Open the event context to review neighboring activity and paid-out vendor details.',
      'Propose a reasoned paid-out detail correction when the source evidence supports it.',
    ],
  },
  {
    path: '/register-summary',
    section: 'Store day audit',
    title: 'Compare cashier and register balances',
    summary: 'Roll transaction and tender totals up to cashier and register level.',
    steps: [
      'Choose the business date and store, then compare register or cashier rows.',
      'Review sales, returns, register events, cash declarations, and over/short amounts together.',
      'Export all cashier or register rows for the selected store day to CSV.',
      'Open the contributing transactions or register events for an unexpected balance.',
      'Save a store-day view by register or cashier for quick reuse across devices.',
    ],
  },
  {
    path: '/audit',
    section: 'Investigate and correct',
    title: 'Review the audit trail',
    summary: 'Search the append-only history of decisions and changes across the application.',
    steps: [
      'Filter by actor, event type, date, or business object identifier.',
      'Inspect before-and-after details to understand what changed.',
      'Use the object link or identifier to return to the source workflow.',
    ],
    notes: ['Audit events record the action and do not provide an edit or delete path.'],
  },
  {
    path: '/reports/variances',
    section: 'Reports',
    title: 'Review total variances',
    summary: 'Find store-day totals outside configured tolerances.',
    steps: [
      'Choose the date range, store scope, and total definition.',
      'Sort by variance amount or status to prioritize the largest differences.',
      'Open the affected store day and review its totals, explanations, and audit status.',
    ],
  },
  {
    path: '/reports/errors',
    section: 'Reports',
    title: 'Compare errors by store',
    summary: 'Spot stores with recurring or unusually high audit-error counts.',
    steps: [
      'Set a date range and error-status filter.',
      'Compare counts across stores and identify repeated error types.',
      'Open the error queue to investigate the underlying records.',
    ],
  },
  {
    path: '/reports/store-days',
    section: 'Reports',
    title: 'Track store-day status',
    summary: 'Monitor import, audit, and close progress across business dates.',
    steps: [
      'Filter by business date, store, and workflow status.',
      'Use late or stuck indicators to prioritize days that need attention.',
      'Open a store day to inspect readiness, unresolved exceptions, and close controls.',
    ],
  },
  {
    path: '/reports/tax',
    section: 'Reports',
    title: 'Reconcile tax and VAT',
    summary: 'Compare tax totals by jurisdiction and business date.',
    steps: [
      'Choose the date range, store scope, and tax jurisdiction.',
      'Compare recorded tax amounts with configured rates and expected totals.',
      'Inspect transaction-level detail where tax values differ from policy.',
    ],
  },
  {
    path: '/reports/tax-corrections',
    section: 'Reports',
    title: 'Review tax corrections',
    summary: 'Track tax amount, summary row, and item line corrections with reviewer details.',
    steps: [
      'Filter by approval status, store, jurisdiction, or effective date.',
      'Compare the original and corrected values with the stated reason.',
      'Open the correction record to review approval and export history.',
    ],
  },
  {
    path: '/reports/flash-totals',
    section: 'Reports',
    title: 'Review Flash Totals',
    summary: 'Review calculated total values selected for the Flash Totals report.',
    steps: [
      'Choose the business-date range and an optional store.',
      'Review the current, non-stale total values and their balancing dimensions.',
      'Use Total Definitions to choose which totals feed this report.',
      'Download the matching rows or copy the report link to share this view.',
    ],
  },
  {
    path: '/reports/flash-sales-ytd',
    section: 'Reports',
    title: 'Review Flash Sales Year to Date',
    summary: 'Compare year-to-date values from totals selected for the Flash Sales report.',
    steps: [
      'Choose the as-of date and optional store; the report starts at January 1 of that year.',
      'Compare sales totals and the number of store days included.',
      'Use Total Definitions to choose which totals feed this report.',
      'Download the matching rows or copy the report link to share this view.',
    ],
  },
  {
    path: '/activity',
    section: 'Diagnostics',
    title: 'Review agent activity',
    summary: 'Inspect the audit trail for automated, connector, and employee activity.',
    steps: [
      'Filter by actor group, search the actor or event description, or show only attention events.',
      'Searches and filters apply across the audit trail; use Load older events to continue through matching results.',
      'Copy the page URL to share the current filters, or save a named view to your account.',
      'Finance and IT can export every event matching these filters, including events older than the loaded page.',
      'Review the evidence and explanation attached to each recommendation.',
      'Open the linked case or work item and use its normal approval controls.',
    ],
  },
  {
    path: '/diagnostics/health',
    section: 'Diagnostics',
    title: 'Check system health',
    summary: 'Review application readiness, dependencies, and operational health signals.',
    steps: [
      'Start with failed or degraded checks and their latest details.',
      'Open the related connector, feed, or configuration area to investigate.',
      'Recheck after the underlying issue is addressed.',
    ],
  },
  {
    path: '/diagnostics/feeds',
    section: 'Diagnostics',
    title: 'Monitor data feeds',
    summary: 'Inspect feed freshness and delivery status for required source systems.',
    steps: [
      'Filter by source system and business date.',
      'Compare received time, record counts, and reconciliation outcomes.',
      'Open Imports or the source connector to resolve missing or failed deliveries.',
    ],
  },
  {
    path: '/diagnostics/integrity',
    section: 'Diagnostics',
    title: 'Verify audit integrity',
    summary: 'Check the append-only audit history and its integrity chain.',
    steps: [
      'Review the latest integrity check and the covered event sequence.',
      'Investigate any reported gap or verification failure with an administrator.',
      'Record the resolution in the incident workflow and recheck integrity.',
    ],
  },
  {
    path: '/diagnostics/activity',
    section: 'Diagnostics',
    title: 'Inspect audit activity',
    summary: 'Review recent user and system actions across the audit workspace.',
    steps: [
      'Filter by event, actor, time, or object identifier.',
      'Open event details to inspect the recorded state change.',
      'Follow the object identifier back to its business workflow when needed.',
    ],
  },
  {
    path: '/settings',
    section: 'Configuration',
    title: 'Set workspace appearance',
    summary: 'Manage personal theme and account preferences for the audit workspace.',
    steps: [
      'Choose a light, dark, or system-matched theme.',
      'Use the configuration navigation to open the settings allowed for your role.',
      'Open My Preferences for regional formats, density, contrast, and motion settings.',
    ],
  },
  {
    path: '/settings/exchange',
    section: 'Configuration',
    title: 'Exchange configuration and foundation data',
    summary:
      'Transfer supported JSON packages from one role-aware workspace while preserving each domain’s safeguards.',
    steps: [
      'Download a package or select a supported package type and upload its JSON file.',
      'Inspect the validation result and complete change details before applying a valid preview.',
      'Enter an audit reason where required; imported rules remain worksheets until approved separately.',
      'Use Foundation Data for validated CSV reference-data imports and exports.',
    ],
    notes: [
      'The destination API enforces role checks, stale-preview checks, domain approvals, and audit records.',
    ],
  },
  {
    path: '/settings/integrations',
    section: 'Configuration',
    title: 'Configure feeds and operational options',
    summary: 'Maintain source profiles, reconciliation policy, and integration readiness.',
    steps: [
      'Review feed contracts, source schedules, and production-readiness checks.',
      'Set matching and audit options to the approved retailer policy.',
      'Promote operational system options as a reviewed package when moving policy between environments.',
      'Promote voucher aging, retention, and jurisdiction routing together from a reviewed policy package.',
      'Use the status panels to confirm expected sources are arriving and reconciling.',
    ],
  },
  {
    path: '/settings/controls',
    section: 'Configuration',
    title: 'Govern totals, rules, and GL mapping',
    summary: 'Maintain the controls that calculate store-day totals and detect exceptions.',
    steps: [
      'Create a draft total, rule, or mapping revision with a clear effective scope.',
      'Preview its impact and review affected stores or transactions.',
      'Submit for an authorized independent approval before activation.',
    ],
  },
  {
    path: '/settings/close',
    section: 'Configuration',
    title: 'Configure tolerances and period close',
    summary: 'Set variance thresholds and govern financial period close.',
    steps: [
      'Review existing thresholds and store-day ordering policy.',
      'Set close-period dates and required approvals to match the finance calendar.',
      'Review open store days and variances before approving a period close.',
    ],
  },
  {
    path: '/settings/tenders',
    section: 'Configuration',
    title: 'Maintain tender types',
    summary: 'Keep accepted payment types and their validation rules aligned with source feeds.',
    steps: [
      'Review tender codes, active status, and allowed source mappings.',
      'Update tender constraints through the governed configuration flow.',
      'Confirm imports and summaries use the expected tender classification.',
    ],
  },
  {
    path: '/settings/retention',
    section: 'Configuration',
    title: 'Manage data retention',
    summary: 'Review retention windows and the history of approved purge runs.',
    steps: [
      'Inspect the retention policy for each data family.',
      'Check eligibility and related legal or finance holds before changing a window.',
      'Review each purge run and its outcome in the retention history.',
    ],
  },
  {
    path: '/settings/agents',
    section: 'Configuration',
    title: 'Govern audit agents',
    summary: 'Control which investigation agents are enabled and what actions they may take.',
    steps: [
      'Review each agent’s purpose, available tools, and operating status.',
      'Limit actions to the approval policy and data access required for the role.',
      'Inspect activity and recommendations after changing an agent setting.',
    ],
  },
  {
    path: '/settings/destinations',
    section: 'Configuration',
    title: 'Configure export destinations',
    summary: 'Manage the endpoints and formats used to deliver audited data.',
    steps: [
      'Review the destination owner, endpoint, format, and enabled status.',
      'Verify receiver-side idempotency and acknowledgement handling before activation.',
      'Use Exports to monitor delivery attempts and resolve failures.',
    ],
  },
  {
    path: '/settings/access',
    section: 'Configuration',
    title: 'Manage roles and store access',
    summary:
      'Review role capabilities and assign Sales Auditors and store employees to stores or location traits.',
    steps: [
      'Review the role matrix and each screen’s access requirements.',
      'Filter the verified employee directory by time since last activity to find accounts for review; inactivity does not prove the identity was removed upstream.',
      'Check each store assignment’s last verified identity request; missing or old activity is informational and does not revoke its scope.',
      'Filter assignments with no verified activity or 90+ days of inactivity, then confirm the person’s status in Ontology Studio before changing access.',
      'Filter business-role assignments by time since last activity and export the full matching directory for access review.',
      'Choose a verified Sales Auditor or store employee, assign stores or active-store traits, and save the scope.',
      'For bulk changes, preview the CSV rows and validation results before applying them.',
    ],
    notes: [
      'A person must sign in through Ontology Studio once before a new role or store assignment can be created. Sales Auditors without a local portfolio cannot access store data.',
    ],
  },
];

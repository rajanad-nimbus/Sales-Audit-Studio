import type { RoutingDecision } from '@/lib/vouchers';

export function VoucherRouting({
  decision,
  explain = false,
}: {
  decision?: RoutingDecision | null;
  explain?: boolean;
}) {
  if (!decision) return <span>Manual escheatment</span>;
  return (
    <>
      <span>{decision.label}</span>
      {(decision.location_of || decision.income_partner) && (
        <small className="act-sub" style={{ display: 'block' }}>
          {decision.location_of
            ? `From ${decision.location_of}`
            : `Partner: ${decision.income_partner}`}
        </small>
      )}
      {explain && decision.trace.length > 0 && (
        <details>
          <summary>Decision path</summary>
          <ol>
            {decision.trace.map((step) => (
              <li key={step.location_of}>
                {step.location_of}:{' '}
                {step.jurisdiction
                  ? `${step.jurisdiction.country}${step.jurisdiction.state ? ` / ${step.jurisdiction.state}` : ''}`
                  : 'No jurisdiction'}
                {' · '}
                {step.outcome}
                <small className="act-sub" style={{ display: 'block' }}>
                  {step.source}
                </small>
              </li>
            ))}
          </ol>
        </details>
      )}
    </>
  );
}

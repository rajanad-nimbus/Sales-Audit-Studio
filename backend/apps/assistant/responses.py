"""Deterministic, read-only responses grounded in assembled assistant context."""

import re
from decimal import Decimal

from integrations.ontology import ontology

VAGUE = re.compile(r"\b(this|that|it|the case|here)\b", re.I)


def money(v, currency=None) -> str:
    return f"{currency or 'currency unknown'} {Decimal(str(v)):,.2f}"


def _src(c: dict) -> dict:
    return {
        "type": "case",
        "id": c["id"],
        "label": c.get("case_number") or c.get("type"),
        "href": f"/cases/{c['id']}",
    }


def _needs_src(c: dict) -> dict:
    return {
        "type": "case",
        "id": c["id"],
        "label": f"{c['case_number']} · {c['type']} · {money(c['amount'], c.get('currency'))}",
        "href": f"/cases/{c['id']}",
    }


def _ont_match(m: str):
    for key, od in ontology.EXCEPTION_TYPES.items():
        if key.lower().replace("_", " ") in m or od["label"].lower() in m:
            return key, od
    return None, None


def reply(
    direct: str,
    bullets: list[str] | None = None,
    nxt: str | None = None,
    sources=None,
    followups=None,
) -> dict:
    parts = [direct]
    if bullets:
        parts.append("\n".join(f"- {b}" for b in bullets))
    if nxt:
        parts.append(f"**Next:** {nxt}")
    return {
        "answer": "\n\n".join(parts),
        "source": "rules",
        "sources": sources or [],
        "followups": followups or [],
    }


CASE_FOLLOWUPS = [
    "What should I do next?",
    "What is the exposure?",
    "How complete is the evidence?",
]
GLOBAL_FOLLOWUPS = [
    "What needs my decision?",
    "Give me a summary",
    "Which connectors are unhealthy?",
]


def rule_answer(msg: str, ctx: dict, persona: str = "") -> dict:
    m = msg.lower().strip()
    if re.fullmatch(r"(hi|hello|hey|help|what can you do\??|who are you\??)[!. ]*", m):
        return help_response()
    clarification = clarify_vague_case_reference(m, ctx)
    if clarification is not None:
        return clarification
    return dispatch_context_answer(m, ctx, ctx.get("selected_case"), persona)


def help_response() -> dict:
    """Describe the assistant's supported read-only capabilities."""
    return reply(
        "I'm the **Nimbus Assistant**. I explain what is in your data and point you to the right controls. I can't approve, execute or change anything.",
        [
            "**Cases and exposure**: what needs a decision, the biggest exposure, SLA status",
            "**Evidence and findings**: why a case happened and how complete the evidence is",
            "**Policy and decisions**: what the policy gate decided and what approving would do",
            "**Definitions**: evidence needed and known causes for each exception type",
            "**Operations**: connector health, the daily batch, recent activity",
        ],
        "Select a case, or mention one by number (for example ZA-20261005-0004).",
        followups=GLOBAL_FOLLOWUPS,
    )


def clarify_vague_case_reference(message: str, context: dict) -> dict | None:
    """Ask which case is meant when a case-specific question has no selection."""
    if (
        context.get("selected_case")
        or not VAGUE.search(message)
        or not re.search(
            r"\b(why|exposure|evidence|next|status|approve|should|finding)\b",
            message,
        )
    ):
        return None
    cases = context["needs_decision"][:4]
    bullets = [
        f"{case['case_number']}: {case['type']} at {case['store']}, "
        f"{money(case['amount'], case.get('currency'))}"
        for case in cases
    ] or ["No cases are awaiting a decision right now."]
    return reply(
        "**Which case do you mean?** I don't have one selected, and your question refers to a specific case.",
        bullets,
        "Select a case in the Command Center or Action Center, or mention its number.",
        sources=[_needs_src(case) for case in cases],
        followups=GLOBAL_FOLLOWUPS,
    )


def dispatch_context_answer(
    message: str, context: dict, selected_case: dict | None, persona: str
) -> dict:
    """Route a normalized message through ordered, grounded answer handlers."""
    handlers = [
        lambda: answer_selected_case(message, selected_case) if selected_case else None,
        lambda: answer_ontology_definition(message),
        lambda: answer_decision_queue(message, context, persona),
        lambda: answer_largest_exposure(message, context),
        lambda: answer_connector_health(message, context),
        lambda: answer_batch_status(message, context),
        lambda: answer_recent_activity(message, context),
        lambda: answer_portfolio_summary(message, context),
    ]
    for handler in handlers:
        answer = handler()
        if answer is not None:
            return answer
    return unsupported_response()


def answer_connector_health(message: str, context: dict) -> dict | None:
    """Report connector health and configuration state when requested."""
    if not re.search(r"connector|health|feed|integration", message):
        return None
    connectors = context["connectors"]
    unhealthy = [connector for connector in connectors if connector["state"] != "Healthy"]
    headline = (
        f"**{len(connectors) - len(unhealthy)} of {len(connectors)} connectors are healthy.**"
        if connectors
        else "**No connectors are registered yet.**"
    )
    bullets = [f"{item['name']}: {item['state']} ({item['mode']})" for item in connectors]
    next_step = (
        "Configure the source adapters before enabling automated connector controls."
        if unhealthy
        else None
    )
    return reply(headline, bullets, next_step, followups=GLOBAL_FOLLOWUPS)


def answer_batch_status(message: str, context: dict) -> dict | None:
    """Report the latest successful batch when requested."""
    if not re.search(r"batch|nightly|daily|data as of|fresh|stale", message):
        return None
    batch = context.get("last_batch")
    headline = (
        f"**Last successful batch: business date {batch['business_date']}.**"
        if batch
        else "**The daily batch has not run yet.**"
    )
    bullets = (
        [f"Finished {batch['finished_at'][:16]}", f"Cases raised: {batch['cases_created']}"]
        if batch
        else None
    )
    return reply(
        headline,
        bullets,
        "Transactions sync once a day; cases appear when the batch finishes." if batch else None,
        followups=GLOBAL_FOLLOWUPS,
    )


def answer_recent_activity(message: str, context: dict) -> dict | None:
    """Report a short audit activity summary when requested."""
    if not re.search(r"recent|activity|happened|latest|audit", message):
        return None
    activity = context["recent_activity"][:5]
    headline = "**Latest activity:**" if activity else "**No activity yet.**"
    bullets = [f"{item['actor']}: {item['what']}" for item in activity]
    return reply(
        headline,
        bullets,
        "See the Audit Trail for the full, verified record.",
        followups=GLOBAL_FOLLOWUPS,
    )


def unsupported_response() -> dict:
    """Return guidance for requests outside the grounded answer set."""
    return reply(
        "**I can't answer that from the data I have.**",
        [
            "Try: what needs my decision, a summary, the biggest exposure, connector health, the daily batch, or recent activity.",
            "For a case: why it happened, its exposure, the evidence, the policy decision, or what to do next.",
            "For definitions: ask what evidence a specific exception type needs.",
        ],
        "Select a case or mention one by number.",
        followups=GLOBAL_FOLLOWUPS,
    )


def answer_decision_queue(message: str, context: dict, persona: str) -> dict | None:
    """Summarize decision queue volume and selected cases when requested."""
    if not re.search(
        r"decision|waiting|attention|pending|approve|review|need|work on|priorit", message
    ):
        return None
    cases = context["needs_decision"]
    decision_count = context.get("needs_decision_count", len(cases))
    headline = (
        f"**{decision_count} case(s) are waiting for a decision.**"
        if decision_count
        else "**Nothing is waiting for your decision.**"
    )
    additional = []
    if persona == "it":
        unhealthy = [
            connector for connector in context["connectors"] if connector["state"] != "Healthy"
        ]
        additional = (
            [
                f"Connector issues: {
                    ', '.join(item['name'] + ' (' + item['state'] + ')' for item in unhealthy)
                }"
            ]
            if unhealthy
            else ["All connectors are healthy."]
        )
    bullets = [
        f"{case['case_number']}: {case['type']} at {case['store']}, "
        f"exposure {money(case['exposure'], case.get('currency'))}"
        for case in cases[:5]
    ] + additional
    return reply(
        headline,
        bullets,
        "Review the cases in the Action Center." if cases else None,
        [_needs_src(case) for case in cases[:5]],
        GLOBAL_FOLLOWUPS,
    )


def answer_ontology_definition(message: str) -> dict | None:
    """Explain recognized exception types from the versioned ontology."""
    key, definition = _ont_match(message)
    if not key:
        return None
    exposure = (
        "none by default" if definition["exposure"] == "zero" else "equal to the exception amount"
    )
    bullets = [
        f"Required evidence: {', '.join(definition['evidence'])}",
        f"Known causes: {', '.join(definition['known_causes'])}",
        f"Typical disposition: {definition['disposition'][0]} via {definition['disposition'][1]}",
        f"Financial exposure: {exposure}",
    ]
    return reply(
        f"**{definition['label']}** (`{key}`) is a {definition['family'].lower()} exception.",
        bullets,
        None,
        [{"type": "ontology", "label": ontology.ONTOLOGY_VERSION, "href": None}],
        ["What evidence does a missing refund need?", "Give me a summary"],
    )


def answer_largest_exposure(message: str, context: dict) -> dict | None:
    """Rank decision-queue exposure only when the currency basis is comparable."""
    if not re.search(r"biggest|largest|top|worst", message):
        return None
    cases = context["needs_decision"]
    currencies = set(
        context.get("needs_decision_currencies", [case.get("currency") for case in cases])
    )
    if len(currencies) > 1 or None in currencies:
        return reply(
            "**Exposures cannot be ranked across different or unknown currencies.**",
            [
                f"{case['case_number']}: {money(case['exposure'], case.get('currency'))}"
                for case in cases[:5]
            ],
            None,
            [_needs_src(case) for case in cases[:5]],
            GLOBAL_FOLLOWUPS,
        )
    largest = sorted(cases, key=lambda case: -Decimal(case["exposure"]))[:3]
    headline = (
        "**Largest open exposure among cases awaiting a decision:**"
        if largest
        else "**No cases are awaiting a decision.**"
    )
    bullets = [
        f"{case['case_number']}: {money(case['exposure'], case.get('currency'))} "
        f"({case['type']}, {case['store']})"
        for case in largest
    ]
    return reply(headline, bullets, None, [_needs_src(case) for case in largest], GLOBAL_FOLLOWUPS)


def answer_portfolio_summary(message: str, context: dict) -> dict | None:
    """Summarize portfolio counts and amounts after higher-priority intents."""
    if not re.search(r"summary|overview|today|how many|status|total|portfolio", message):
        return None
    statuses = context["cases_by_status"]
    amounts = context.get("open_amounts_by_currency", [])
    bullets = [f"{count:,} {status.lower()}" for status, count in statuses.items()]
    if context.get("stale_amount_cases"):
        bullets.append(
            f"{context['stale_amount_cases']} open case(s) have amounts awaiting review "
            "and are excluded from totals."
        )
    bullets.extend(
        f"{money(item['amount'], item['currency'])} open amount; "
        f"{money(item['exposure'], item['currency'])} exposure"
        for item in amounts
    )
    return reply(
        f"**{sum(statuses.values()):,} cases.**",
        bullets,
        None,
        followups=["What needs my decision?", "Which connectors are unhealthy?"],
    )


def answer_selected_case(message: str, case: dict) -> dict | None:
    """Answer supported intents using the selected case's grounded context."""
    recommendation = case["recommendations"][0] if case["recommendations"] else None
    sources = [_src(case)] + (
        [{"type": "ontology", "label": ontology.ONTOLOGY_VERSION, "href": None}]
        if case.get("ontology")
        else []
    )
    heading = f"{case['case_number']} ({case['type']}, {case['store']})"
    if re.search(r"why|cause|root|finding|happen|explain", message):
        return answer_case_cause(case, heading, sources)
    if re.search(r"exposure|loss|amount|money|impact|how much|cost", message):
        return answer_case_exposure(case, heading, sources)
    if re.search(r"policy|authority|allowed|permit|rule", message):
        evaluation = case["policy"][0] if case["policy"] else None
        headline = (
            f"Policy {evaluation['version']} decided: {evaluation['outcome']}"
            if evaluation
            else "No policy evaluation yet."
        )
        return reply(
            f"**{headline}**",
            evaluation["reasons"]
            if evaluation
            else ["The policy gate runs when a recommendation is approved."],
            None,
            sources,
            CASE_FOLLOWUPS,
        )
    if re.search(r"evidence|complete|missing", message):
        ontology_data = case.get("ontology")
        return reply(
            f"**Evidence completeness for {heading} is {case['evidence_completeness']}%.**",
            (
                [f"Required by the ontology: {', '.join(ontology_data['required_evidence'])}"]
                if ontology_data
                else []
            )
            + ["Completeness is computed from required evidence, not asserted by a model."],
            "Run the investigation to collect missing evidence."
            if case["evidence_completeness"] < 100
            else None,
            sources,
            CASE_FOLLOWUPS,
        )
    if re.search(r"next|status|stage|what now|should|recommend|approve|do", message):
        if recommendation:
            return reply(
                f"**{heading} is '{case['status']}'. Proposed action: {recommendation['disposition']} "
                f"via {recommendation['action_class']} ({recommendation['status']}).**",
                [
                    f"What happens: {recommendation['workflow']}",
                    f"Financial movement: {money(recommendation['financial_impact'], case.get('currency'))}",
                ],
                "Use Approve, Escalate or Request more evidence on the case. I can't decide for you.",
                sources,
                CASE_FOLLOWUPS,
            )
        return reply(
            f"**{heading} is '{case['status']}' and has no recommendation yet.**",
            None,
            "Run the investigation.",
            sources,
            CASE_FOLLOWUPS,
        )
    return None


def answer_case_cause(case: dict, heading: str, sources: list[dict]) -> dict:
    findings = case["findings"]
    ontology_data = case.get("ontology")
    bullets = [f"Confidence: {findings[0]['confidence']}"] if findings else []
    bullets.append(f"Evidence completeness: {case['evidence_completeness']}%")
    if ontology_data:
        bullets.extend(
            (
                f"Known causes for this type: {', '.join(ontology_data['known_causes'])}",
                "These are candidate causes from the ontology, not proven for this case.",
            )
        )
    if not findings:
        next_step = "Run the investigation."
    elif case["evidence_completeness"] < 100 or findings[0]["confidence"] == "Low":
        next_step = "Provide the missing evidence, then re-run the investigation."
    else:
        next_step = "Review the recommendation and decide."
    headline = (
        f"**{findings[0]['conclusion']}**"
        if findings
        else f"**No finding yet for {heading}.** The investigation hasn't run."
    )
    return reply(headline, bullets, next_step, sources, CASE_FOLLOWUPS)


def answer_case_exposure(case: dict, heading: str, sources: list[dict]) -> dict:
    if case.get("amount_needs_review"):
        return reply(
            f"**{heading}: recorded amounts require finance review after a correction.**",
            ["The previous amount and exposure are historical and are not current figures."],
            "Review and record the updated amounts before investigation or export.",
            sources,
            CASE_FOLLOWUPS,
        )
    close_impact = case["exceptions"][0]["close_impact"] if case["exceptions"] else "unknown"
    sla = f", SLA due {case['sla_due_at'][:16]}" if case["sla_due_at"] else ""
    return reply(
        f"**{heading}: exception amount {money(case['exception_amount'], case.get('currency'))}, "
        f"estimated loss exposure {money(case['loss_exposure'], case.get('currency'))}.**",
        [
            "Amount and exposure are tracked separately; exposure can be lower (for example a pure timing difference).",
            f"Close impact: {close_impact}",
            f"Priority {case['priority']}{sla}",
        ],
        None,
        sources,
        CASE_FOLLOWUPS,
    )

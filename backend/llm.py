"""Optional LLM narrator. Facts, amounts and authority stay deterministic; the model only drafts prose."""
import os


def credentials() -> str | None:
    """Which Claude credential is configured: 'sdk' (Claude Code OAuth token, preferred), 'api' (API key) or None."""
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        return "sdk"
    return "api" if os.getenv("ANTHROPIC_API_KEY") else None


async def narrate(exception_label: str, amount: str, evidence: list[str], known_causes: list[str]) -> str | None:
    prompt = (f"Write 2 sentences for a retail finance reviewer explaining a '{exception_label}' exception "
              f"of {amount}. Evidence collected: {', '.join(evidence)}. Candidate causes from the ontology: "
              f"{', '.join(known_causes)}. Do not assert a cause as proven, invent figures, or recommend approval.")
    if credentials() == "sdk":
        text = await chat_via_agent_sdk("You write concise, factual notes for retail finance reviewers.", [], prompt)
        if text:
            return text
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=key)
        msg = await client.messages.create(
            model=os.getenv("NIMBUS_LLM_MODEL", "claude-sonnet-5-5"),
            max_tokens=250, messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip() or None
    except Exception:
        return None


SYSTEM = """You are the Nimbus Assistant for retail sales-audit finance and IT users.

<role>
You explain what is in the provided context: cases, exposure, evidence, findings, policy decisions, workflows, validations, connectors, the daily batch, and ontology definitions.
</role>

<guidelines>
1. Answer directly first, in business terms, then give the supporting facts.
2. Always cite case numbers and exact figures from the context. Keep exception amount and loss exposure separate.
3. Separate supported findings from candidate causes. Candidate causes from the ontology are not proven.
4. Be specific and grounded. Never invent cases, amounts, evidence or causes.
</guidelines>

<handling_ambiguity>
If the question depends on a case and none is selected or mentioned, name the ambiguity and ask which case, listing the most relevant ones from the context. Never guess.
</handling_ambiguity>

<output_format>
Markdown. Start with a one-sentence direct answer in bold. Then short bullets of supporting facts. End with "**Next:**" and the control the user should use. Under 150 words.
</output_format>

<limitations>
- Answer only from the context. If it lacks the answer, say so.
- You cannot approve, escalate, execute, validate or change anything. Direct the user to the decision controls.
- Do not give legal, tax or accounting advice.
</limitations>
{persona}
<reference_context>
{context}
</reference_context>"""


def _lens(persona: str) -> str:
    return {"finance": "\n<persona>The user is in Finance: emphasise exposure, approvals and close impact.</persona>\n",
            "it": "\n<persona>The user is in IT Operations: emphasise connectors, the daily batch, evidence gaps and recovery.</persona>\n",
            "analyst": "\n<persona>The user is a Sales Auditor: emphasise evidence, candidate causes and what is still unproven.</persona>\n",
            "auditor": "\n<persona>The user is Internal Audit: emphasise controls, the audit trail and agent decisions.</persona>\n",
            }.get(persona, "")


CHAT_TIMEOUT_S = 60


async def chat_via_agent_sdk(system: str, history: list[dict], message: str, query_fn=None) -> str | None:
    """Chat through the Claude Agent SDK, authenticated with a Claude Code OAuth token.

    The assistant is read-only and grounded in the supplied context, so every built-in tool is disabled, no
    settings files are loaded, and it is limited to a single turn.
    """
    token = os.getenv("CLAUDE_CODE_OAUTH_TOKEN")
    if not token:
        return None
    try:
        import asyncio

        from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock, query

        options = ClaudeAgentOptions(
            system_prompt=system, model=os.getenv("NIMBUS_CHAT_MODEL", os.getenv("NIMBUS_LLM_MODEL", "claude-sonnet-5-5")),
            max_turns=1, tools=[], allowed_tools=[], setting_sources=[], permission_mode="dontAsk",
            env={"CLAUDE_CODE_OAUTH_TOKEN": token})
        transcript = "".join(f"{h['role'].capitalize()}: {h['content']}\n" for h in history[-10:]
                             if h.get("role") in ("user", "assistant"))
        prompt = (f"<conversation_so_far>\n{transcript}</conversation_so_far>\n\n" if transcript else "") + f"User: {message}"

        async def collect():
            parts, final = [], None
            async for m in (query_fn or query)(prompt=prompt, options=options):
                if isinstance(m, AssistantMessage):
                    parts += [b.text for b in m.content if isinstance(b, TextBlock)]
                elif isinstance(m, ResultMessage):
                    final = None if m.is_error else m.result
            return (final or "".join(parts)).strip() or None
        return await asyncio.wait_for(collect(), CHAT_TIMEOUT_S)
    except Exception:
        return None


async def chat(context: dict, history: list[dict], message: str, persona: str = "") -> str | None:
    """Prefer the Agent SDK with an OAuth token; otherwise the Messages API with an API key."""
    import json
    system = SYSTEM.format(persona=_lens(persona), context=json.dumps(context, default=str))
    if os.getenv("CLAUDE_CODE_OAUTH_TOKEN"):
        text = await chat_via_agent_sdk(system, history, message)
        if text:
            return text
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None
    try:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=key)
        msgs = [{"role": h["role"], "content": h["content"]} for h in history[-10:] if h.get("role") in ("user", "assistant")]
        msgs.append({"role": "user", "content": message})
        r = await client.messages.create(model=os.getenv("NIMBUS_LLM_MODEL", "claude-sonnet-5-5"), max_tokens=600,
                                         system=system, messages=msgs)
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip() or None
    except Exception:
        return None

"""
prompt_speculation.py — Follow-up Vorschläge nach Agent-Antwort (#488)

Inspiriert von Claude Code speculation.ts.
Schneller Haiku-Call generiert 2-3 wahrscheinliche Follow-up-Fragen.
"""
import json
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Du generierst 2-3 kurze Follow-up-Vorschläge basierend auf der Konversation. "
    "Regeln:\n"
    "- Jeder Vorschlag max 6 Wörter\n"
    "- Praktisch und handlungsorientiert\n"
    "- In der Sprache des Users (Deutsch wenn User Deutsch schreibt)\n"
    "- Antworte NUR mit einem JSON-Array: [\"Vorschlag 1\", \"Vorschlag 2\", \"Vorschlag 3\"]\n"
    "- Keine Erklärung, kein Markdown, nur das Array"
)


async def generate_suggestions(
    user_text: str,
    assistant_response: str,
    max_suggestions: int = 3,
) -> list[str]:
    """Generiert Follow-up-Vorschläge basierend auf dem letzten Turn."""
    if not user_text or not assistant_response:
        return []
    # Zu kurze Antworten brauchen keine Suggestions
    if len(assistant_response) < 50:
        return []

    from .orchestrator import _load_claude_oauth_token
    oauth_token = _load_claude_oauth_token()
    if not oauth_token:
        return []

    try:
        import anthropic
        client = anthropic.AsyncAnthropic(
            api_key="",
            auth_token=oauth_token,
            default_headers={
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                "user-agent": "claude-cli/2.1.62",
                "x-app": "cli",
            },
        )
        # Kontext kürzen für schnellen Call
        user_short = user_text[:300]
        asst_short = assistant_response[:500]

        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            system=[{"type": "text", "text": _SYSTEM_PROMPT}],
            messages=[{
                "role": "user",
                "content": f"User sagte: {user_short}\n\nAssistant antwortete: {asst_short}",
            }],
        )
        raw = (resp.content[0].text if resp.content else "").strip()
        # JSON-Array parsen
        suggestions = json.loads(raw)
        if isinstance(suggestions, list):
            return [str(s).strip() for s in suggestions[:max_suggestions] if s]
    except Exception as e:
        logger.debug("Prompt speculation error: %s", e)

    return []

"""Generate short structured spending insights via the Anthropic Messages API."""

import json
import logging
import os

import anthropic
from anthropic.types import Message
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 300
_MAX_ATTEMPTS = 2
_SYSTEM_PROMPT = (
    "You are a financial assistant. Given a list of expenses (amounts are in "
    "minor units of the base currency), respond with JSON only, no prose, no "
    "code fences. The JSON object must have exactly two keys: \"summary\", a "
    "short string describing overall spending, and \"bullets\", an array of "
    "exactly three short strings highlighting notable spending patterns."
)
_FALLBACK: dict[str, object] = {
    "summary": "Insights are unavailable right now. Please try again later.",
    "bullets": [],
}


def _build_summary(expenses: list[dict]) -> str:
    """Build a compact one-line-per-expense text summary for the prompt."""
    lines = [
        f"- amount_base_minor={expense.get('amount_base_minor')}, "
        f"category={expense.get('category')}, status={expense.get('status')}"
        for expense in expenses
    ]
    return "\n".join(lines)


def _extract_text(response: Message) -> str:
    """Pull the text content out of a Messages API response."""
    return next((block.text for block in response.content if block.type == "text"), "")


def _parse_insight(text: str) -> dict[str, object] | None:
    """Parse and validate the model's JSON insight payload.

    Returns None if the text is not valid JSON or does not match the
    {"summary": str, "bullets": [str, str, str]} shape.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    summary = data.get("summary")
    bullets = data.get("bullets")
    if not isinstance(summary, str):
        return None
    if not isinstance(bullets, list) or len(bullets) != 3:
        return None
    if not all(isinstance(bullet, str) for bullet in bullets):
        return None

    return {"summary": summary, "bullets": bullets}


def generate_insight(expenses: list[dict]) -> dict[str, object]:
    """Ask Claude for a structured spending insight over a list of expenses.

    Args:
        expenses: Expense dicts, each expected to carry amount_base_minor,
            category, and status keys.

    Returns:
        A dict with a "summary" string and a "bullets" list of exactly three
        strings. Falls back to a safe default object if the API call fails or
        the model's response cannot be parsed as valid JSON of that shape,
        even after one retry.
    """
    if not expenses:
        return {"summary": "No expenses to analyze yet.", "bullets": []}

    summary_text = _build_summary(expenses)
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key is not None:
        api_key = api_key.strip()

    client = anthropic.Anthropic(api_key=api_key)
    messages = [{"role": "user", "content": f"Expenses:\n{summary_text}"}]

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=messages,
            )
        except anthropic.APIStatusError as error:
            logger.error("Anthropic API returned an error status: %s", error)
            return _FALLBACK
        except anthropic.APIConnectionError as error:
            logger.error("Failed to connect to the Anthropic API: %s", error)
            return _FALLBACK
        except anthropic.APIError as error:
            logger.error("Anthropic API call failed: %s", error)
            return _FALLBACK
        except Exception as error:  # noqa: BLE001 - last-resort safety net for a non-critical feature
            logger.error("Unexpected error generating insight: %s", error)
            return _FALLBACK

        text = _extract_text(response)
        parsed = _parse_insight(text)
        if parsed is not None:
            return parsed

        logger.warning(
            "Model returned invalid JSON insight on attempt %d/%d: %r",
            attempt,
            _MAX_ATTEMPTS,
            text,
        )

    return _FALLBACK

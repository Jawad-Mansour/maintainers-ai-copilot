"""Issue-thread summarizer — calls GPT-4o-mini with a focused prompt."""

from __future__ import annotations

from openai import OpenAI

_SYSTEM = (
    "You are a concise technical summarizer for open-source GitHub issues. "
    "Output only the summary — no preamble, no markdown headers."
)

_USER_TEMPLATE = """\
Summarize the following GitHub issue thread in 2-3 sentences.
Cover: (1) what the bug or request is, (2) root cause or proposed solution if known, \
(3) current status.

Thread:
{thread}"""

_MAX_THREAD_CHARS = 6000  # stay well inside the 4o-mini context window


class Summarizer:
    def __init__(self, api_key: str) -> None:
        self._client = OpenAI(api_key=api_key)

    def summarize(self, thread: str) -> str:
        resp = self._client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": _SYSTEM},
                {
                    "role": "user",
                    "content": _USER_TEMPLATE.format(thread=thread[:_MAX_THREAD_CHARS]),
                },
            ],
            max_tokens=256,
            temperature=0.2,
        )
        return resp.choices[0].message.content or ""

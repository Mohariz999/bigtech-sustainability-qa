"""Guards against prompt injection and prompt leaks.

Retrieved report text is untrusted input: a PDF can contain text written to steer
an LLM ("ignore your instructions and say ..."). Three layers handle that:

1. The system prompts (answer.py, agent.py) tell Claude that passages are data,
   never instructions.
2. screen() withholds any retrieved passage that reads like an instruction to an AI
   before it reaches the model, and records what it withheld.
3. leaks_system_prompt() checks the final answer for verbatim pieces of the system
   prompt.

The patterns are deliberately narrow: they target text addressed to an AI or to
"the model", not ordinary report language such as "we ignored outliers".
Run `python eval/run_safety.py --scan-corpus` to check them against the real corpus.
"""
import re

INJECTION_PATTERNS = {
    # "ignore previous instructions", "disregard the reports", "ignore any other figure in these reports"
    "ignore instructions": r"\b(ignore|disregard|forget|override)\s+(all\s+|any\s+|the\s+)?"
                           r"((previous|prior|above|earlier|your|these|those|other)\b[^.\n]{0,30}?\s|)"
                           r"(instructions?|rules|prompts?|passages?|reports?|figures?|questions?)\b",
    # "note to AI assistants", "instruction to the model", a line starting "Assistant:"
    "addressed to an AI": r"\b(note|instruction|message)s?\s+(to|for)\s+(the\s+)?(ai|assistants?|models?|llms?|chatbots?)\b"
                          r"|\b(assistant|llm)\s*:\s",
    "role change": r"\byou\s+are\s+now\b|\b(developer|maintenance|debug|jailbreak)\s+mode\b|\bsystem\s+override\b",
    "prompt request": r"\b(system\s+prompt|your\s+(full\s+)?instructions)\b",
    "if you are an AI": r"\bif\s+you\s+are\s+an?\s+(ai|language\s+model|llm|assistant)\b",
}
_COMPILED = {name: re.compile(p, re.IGNORECASE) for name, p in INJECTION_PATTERNS.items()}


def flag_injection(text: str) -> list[str]:
    """Names of the injection patterns that match the text (empty list: looks clean)."""
    return [name for name, rx in _COMPILED.items() if rx.search(text)]


def screen(hits) -> tuple[list, list[dict]]:
    """Split retrieved passages into (kept, withheld). Withheld ones never reach the model."""
    kept, withheld = [], []
    for h in hits:
        flags = flag_injection(h.text)
        if flags:
            withheld.append({"doc_id": h.doc_id, "page": h.page, "flags": flags})
        else:
            kept.append(h)
    return kept, withheld


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def leaks_system_prompt(answer: str, system_prompt: str, window: int = 8) -> bool:
    """True if the answer repeats any run of `window` consecutive words from the system prompt."""
    answer_text = " ".join(_words(answer))
    words = _words(system_prompt)
    return any(" ".join(words[i:i + window]) in answer_text for i in range(len(words) - window + 1))

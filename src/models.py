"""Which models exist, what they cost, and which can use tools.

One file. The provider retires models without a warning -- an outdated id
returns a hard 404 -- so every model id used anywhere is named here.

Checked live on 2026-10-03 via GET /models with this account's key.
`llama-3.3-70b-versatile` and `llama-3.1-8b-instant` return 404;
`qwen/qwen3.8-27b` is flagged preview.
"""

# id: (label, tools, input $/1M, output $/1M)
MODELS = {
    "openai/gpt-oss-120b": ("GPT-OSS 120B", True, 0.15, 0.60),
    "openai/gpt-oss-20b": ("GPT-OSS 20B", True, 0.075, 0.30),
    "qwen/qwen3.8-27b": ("Qwen 3.8 27B (preview)", True, 0.80, 4.00),
    "allam-2-7b": ("ALLaM 2 7B", False, 0.30, 0.30),
}

DEFAULT_MODEL = "openai/gpt-oss-120b"
TOOL_MODELS = [m for m, (_, t, _, _) in MODELS.items() if t]


class UnknownModel(ValueError):
    """A model id that is not in the table above."""


def resolve(model_id: str = None) -> str:
    candidate = model_id or DEFAULT_MODEL
    if candidate not in MODELS:
        raise UnknownModel(
            f"{candidate!r} is not a known model. Available: {', '.join(MODELS)}"
        )
    return candidate


def supports_tools(model_id: str) -> bool:
    return MODELS[resolve(model_id)][1]

"""VLM fallback tier — cost-gated, only called when local OCR (mineru) isn't available
or fails. Delegates all provider-specific request shaping to litellm; this module's only
job is role resolution, the vision-message shape, and a disk cache so repeat runs over
the same page don't re-bill.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

from .config import Config, VLMRole

CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "vlm"

DEFAULT_PROMPT = (
    "Transcribe this document page to clean Markdown. Preserve headings, tables "
    "(as Markdown tables), and reading order. Describe any figures/diagrams briefly "
    "in place of transcribing them. Output only the Markdown, no commentary."
)


def _cache_key(image_bytes: bytes, role: str, prompt: str) -> str:
    h = hashlib.sha256()
    h.update(image_bytes)
    h.update(role.encode())
    h.update(prompt.encode())
    return h.hexdigest()


def _cache_path(key: str) -> Path:
    return CACHE_DIR / f"{key}.json"


def describe_page(
    image_bytes: bytes,
    config: Config,
    role: str = "default",
    prompt: str = DEFAULT_PROMPT,
    use_cache: bool = True,
) -> tuple[str | None, str]:
    """Returns (markdown_text_or_None, error_message). Tries `role`, then "fallback" if set."""
    key = _cache_key(image_bytes, role, prompt)
    cache_file = _cache_path(key)
    if use_cache and cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))["text"], ""

    for candidate_role in (role, "fallback"):
        vlm_role = config.vlm.get(candidate_role)
        if vlm_role is None:
            continue
        text, err = _call_litellm(image_bytes, vlm_role, prompt)
        if text is not None:
            if use_cache:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({"text": text}), encoding="utf-8")
            return text, ""
        last_err = err
        if candidate_role == role and "fallback" not in config.vlm:
            break

    return None, last_err if "last_err" in dir() else "no vlm role configured"


def _call_litellm(image_bytes: bytes, role: VLMRole, prompt: str) -> tuple[str | None, str]:
    try:
        import litellm
    except ImportError:
        return None, "litellm not installed (pip install litellm)"

    data_uri = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
    kwargs: dict = {
        "model": role.model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    }
    api_key = role.resolve_api_key()
    if api_key:
        kwargs["api_key"] = api_key
    if role.api_base:
        kwargs["api_base"] = role.api_base

    try:
        response = litellm.completion(**kwargs)
        return response["choices"][0]["message"]["content"], ""
    except Exception as e:  # noqa: BLE001 - deliberately broad: any provider/network failure should fall through to the next role
        return None, f"{role.model}: {e}"

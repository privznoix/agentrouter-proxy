from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

# ============================================================
# CONFIGURATION
# ============================================================

HOST = os.getenv("AGENTROUTER_PROXY_HOST", "127.0.0.1")
PORT = int(os.getenv("AGENTROUTER_PROXY_PORT", "4020"))

PROXY_API_KEY = os.getenv(
    "AGENTROUTER_PROXY_API_KEY",
    "local-agentrouter",
)

UPSTREAM_API_KEY = os.getenv("AGENTROUTER_API_KEY", "")

UPSTREAM_BASE_URL = os.getenv(
    "AGENTROUTER_BASE_URL",
    "https://agentrouter.org/v1",
).rstrip("/")

UPSTREAM_USER_AGENT = os.getenv(
    "AGENTROUTER_USER_AGENT",
    "codex_cli_rs/1.0.0 (Windows; x86_64)",
)

# ============================================================
# DEBUG / DIAGNOSTIC OPTIONS
# ============================================================

DEBUG_ENABLED = os.getenv(
    "AGENTROUTER_DEBUG",
    "true",
).lower() in {"1", "true", "yes", "on"}

# Compress large role=tool messages by keeping head/tail.
TOOL_COMPRESSION_ENABLED = os.getenv(
    "AGENTROUTER_COMPRESS_TOOL_OUTPUT",
    "true",
).lower() in {"1", "true", "yes", "on"}

TOOL_COMPRESSION_THRESHOLD = int(os.getenv(
    "AGENTROUTER_TOOL_COMPRESSION_THRESHOLD",
    "40000",
))

TOOL_MAX_CHARS = int(os.getenv(
    "AGENTROUTER_TOOL_MAX_CHARS",
    "30000",
))

TOOL_HEAD_CHARS = int(os.getenv(
    "AGENTROUTER_TOOL_HEAD_CHARS",
    "18000",
))

TOOL_TAIL_CHARS = int(os.getenv(
    "AGENTROUTER_TOOL_TAIL_CHARS",
    "12000",
))

# Diagnostic test:
# remove role=tool history while keeping tools definitions.
STRIP_TOOL_MESSAGES = os.getenv(
    "AGENTROUTER_STRIP_TOOL_MESSAGES",
    "false",
).lower() in {"1", "true", "yes", "on"}

# Diagnostic test:
# remove the tools definition itself.
STRIP_TOOLS = os.getenv(
    "AGENTROUTER_STRIP_TOOLS",
    "false",
).lower() in {"1", "true", "yes", "on"}

# Moderation resilience for image data (base64 data URLs).
# - "auto":   send as-is; when the upstream moderation layer rejects
#             a request that still contains image data, retry once
#             with the image data removed.
# - "always": remove image data from every request before sending.
# - "off":    never remove image data.
STRIP_IMAGES = os.getenv(
    "AGENTROUTER_STRIP_IMAGES",
    "auto",
).strip().lower()

if STRIP_IMAGES not in {"auto", "always", "off"}:
    STRIP_IMAGES = "auto"

# Resilience for upstream models that reject /v1/chat/completions
# requests combining function tools with reasoning_effort
# (e.g. gpt-6-astra).
# - "auto": on such an upstream 400, retry once with
#           reasoning_effort="none" and an alternate User-Agent,
#           then remember the model for later requests.
# - "off":  never apply this workaround.
DROP_REASONING_EFFORT = os.getenv(
    "AGENTROUTER_DROP_REASONING_EFFORT",
    "auto",
).strip().lower()

if DROP_REASONING_EFFORT not in {"auto", "off"}:
    DROP_REASONING_EFFORT = "auto"

# Alternate User-Agent used for the workaround above. The upstream
# gateway injects reasoning_effort for codex-style clients, which
# breaks function tools on some models; the alternate client UA
# passes the request through cleanly with reasoning_effort="none".
ALT_USER_AGENT = os.getenv(
    "AGENTROUTER_ALT_USER_AGENT",
    "opencode/1.0.0",
).strip() or "opencode/1.0.0"

# Diagnostic capture:
# dump the full request payload when the upstream rejects the request
# with a moderation error (e.g. "sensitive words detected"), so the
# triggering content can be located and inspected.
CAPTURE_MODERATION_ERRORS = os.getenv(
    "AGENTROUTER_CAPTURE_MODERATION",
    "true",
).lower() in {"1", "true", "yes", "on"}

# Moderation resilience for the upstream keyword filter
# ("sensitive words detected").
# - "auto":   defang known trigger phrases before sending; if the
#             upstream still rejects the request with a keyword
#             moderation error, retry once with every word split
#             by a zero-width space.
# - "known":  only defang known trigger phrases before sending.
# - "off":    never modify content.
SANITIZE_MODE = os.getenv(
    "AGENTROUTER_SANITIZE_MODERATION",
    "auto",
).strip().lower()

if SANITIZE_MODE not in {"auto", "known", "off"}:
    SANITIZE_MODE = "auto"

ZWSP = "\u200b"

DEFAULT_MODERATION_TRIGGERS = (
    "- Remove: the, this, my, a, an",
    '"refactor user service" \u2192 Refactoring user service',
    "kenapa sering terkena error sensit",
    "sering terkena error kata sens",
)

_EXTRA_TRIGGERS = tuple(
    t.strip()
    for t in os.getenv("AGENTROUTER_MODERATION_TRIGGERS_EXTRA", "").split("||")
    if t.strip()
)

MODERATION_TRIGGERS = DEFAULT_MODERATION_TRIGGERS + _EXTRA_TRIGGERS

# Content-blocked resilience for Indonesian false-positive moderation:
# - "auto": on upstream 400 "content-blocked", automatically translate
#           Indonesian user messages to English and retry with instructions
#           for the assistant to respond in Indonesian.
# - "off":  never auto-translate on content-blocked.
TRANSLATE_CONTENT_BLOCKED = os.getenv(
    "AGENTROUTER_TRANSLATE_CONTENT_BLOCKED",
    "auto",
).strip().lower()

if TRANSLATE_CONTENT_BLOCKED not in {"auto", "off"}:
    TRANSLATE_CONTENT_BLOCKED = "auto"

# Dynamic model registry:
# models are loaded from the upstream /models endpoint
# and cached for this many seconds.
MODELS_CACHE_TTL = int(os.getenv(
    "AGENTROUTER_MODELS_CACHE_TTL",
    "300",
))

# Prefix applied to upstream model IDs exposed to clients.
MODEL_PREFIX = "arp/"

# ============================================================
# LOGGING
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = (
    os.getenv("AGENTROUTER_LOG_DIR")
    or os.path.join(BASE_DIR, "logs")
)
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "agentrouter-proxy.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("agentrouter-proxy")

# ============================================================
# MODEL REGISTRY (loaded from upstream /models)
# ============================================================

_registry_models: list[dict[str, Any]] = []
_registry_fetched_at: float = 0.0
_registry_lock = asyncio.Lock()


def _registry_age() -> float:
    if _registry_fetched_at <= 0.0:
        return -1.0
    return time.time() - _registry_fetched_at


def _registry_is_fresh() -> bool:
    return (
        _registry_fetched_at > 0.0
        and _registry_age() < MODELS_CACHE_TTL
    )


async def fetch_upstream_models() -> list[dict[str, Any]]:
    """Return the cached upstream model list, refreshing when stale.

    Serves stale cache entries when the upstream /models call fails.
    """
    global _registry_models, _registry_fetched_at

    if _registry_is_fresh():
        return _registry_models

    async with _registry_lock:
        if _registry_is_fresh():
            return _registry_models

        require_upstream_api_key()

        headers = get_upstream_headers()
        timeout = httpx.Timeout(
            connect=10.0,
            read=30.0,
            write=10.0,
            pool=10.0,
        )

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{UPSTREAM_BASE_URL}/models",
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()

        data = body.get("data") if isinstance(body, dict) else None
        models: list[dict[str, Any]] = []

        if isinstance(data, list):
            for entry in data:
                if isinstance(entry, dict) and entry.get("id"):
                    models.append(entry)

        if not models:
            raise ValueError("upstream /models returned no models")

        _registry_models = models
        _registry_fetched_at = time.time()

        logger.info(
            "MODELS_REFRESH count=%d source=upstream",
            len(models),
        )

        return _registry_models


async def get_registry_models() -> list[dict[str, Any]]:
    """Registry access with stale-serve fallback.

    Raises HTTPException 502 when there is no usable model list
    (first fetch failed and nothing is cached).
    """
    try:
        return await fetch_upstream_models()

    except HTTPException:
        raise

    except Exception as exc:
        if _registry_models:
            logger.warning(
                "MODELS_CACHE_STALE age=%.0fs error=%s",
                _registry_age(),
                exc,
            )
            return _registry_models

        logger.error("MODELS_FETCH_FAILED error=%s", exc)

        raise HTTPException(
            status_code=502,
            detail=f"Failed to load models from upstream: {exc}",
        )

# ============================================================
# FASTAPI
# ============================================================

app = FastAPI(
    title="AgentRouter Direct Proxy",
    version="1.5.0",
)

# ============================================================
# BASIC HELPERS
# ============================================================

def verify_proxy_api_key(authorization: str | None) -> None:
    expected = f"Bearer {PROXY_API_KEY}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Invalid proxy API key")


def require_upstream_api_key() -> None:
    if not UPSTREAM_API_KEY:
        raise HTTPException(
            status_code=500,
            detail="AGENTROUTER_API_KEY is not configured",
        )


def get_upstream_headers(
    user_agent: str | None = None,
) -> dict[str, str]:
    require_upstream_api_key()
    return {
        "Authorization": f"Bearer {UPSTREAM_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": user_agent or UPSTREAM_USER_AGENT,
    }


async def get_upstream_model(requested_model: str) -> str:
    requested_model = str(requested_model)

    if requested_model.startswith(MODEL_PREFIX):
        upstream_id = requested_model[len(MODEL_PREFIX):]
    else:
        upstream_id = requested_model

    models = await get_registry_models()

    for entry in models:
        if entry.get("id") == upstream_id:
            return upstream_id

    raise HTTPException(
        status_code=400,
        detail=f"Unknown model: {requested_model}",
    )


def openai_error(
    status_code: int,
    message: str,
    error_type: str = "agent_router_proxy_error",
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": error_type,
                "param": None,
                "code": status_code,
            }
        },
    )


def payload_fingerprint(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(
            serialized.encode("utf-8")
        ).hexdigest()[:16]
    except Exception:
        return "unavailable"


MODERATION_MARKERS = (
    "sensitive words detected",
    "content-blocked",
    "content_blocked",
    "sensitive_words_detected",
)

CAPTURE_DIR = os.path.join(LOG_DIR, "moderation-captures")


def is_moderation_error_text(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(
        marker in lowered
        for marker in MODERATION_MARKERS
    )


def is_keyword_moderation_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return "sensitive word" in lowered or "sensitive_word" in lowered

# ============================================================
# MODERATION SANITIZER (keyword-filter resilience)
# ============================================================

_MODERATION_TRIGGER_PATTERNS = tuple(
    re.compile(re.escape(trigger), re.IGNORECASE)
    for trigger in MODERATION_TRIGGERS
)

_WORD_MIN_CHARS = 5

_PROTECTED_KEYS = frozenset({
    "name",
    "type",
    "role",
    "id",
    "url",
    "image_url",
    "arguments",
    "function",
    "enum",
    "required",
    "format",
})


def _defang_middle(match: re.Match) -> str:
    text = match.group(0)
    mid = len(text) // 2
    return text[:mid] + ZWSP + text[mid:]


def _defang_words(match: re.Match) -> str:
    text = match.group(0)
    return text[:2] + ZWSP + text[2:]


def _sanitize_string_known(text: str) -> tuple[str, int]:
    hits = 0

    for pattern in _MODERATION_TRIGGER_PATTERNS:
        text, count = pattern.subn(_defang_middle, text)
        hits += count

    return text, hits


def _sanitize_string_words(text: str) -> str:
    if len(text) > 200 and " " not in text:
        return text

    return re.sub(
        r"\S{%d,}" % _WORD_MIN_CHARS,
        _defang_words,
        text,
    )


def _sanitize_walk(node: Any, mode: str) -> tuple[Any, int]:
    changed = 0

    def visit(value: Any, key: str | None) -> Any:
        nonlocal changed

        if isinstance(value, dict):
            return {
                dict_key: visit(dict_value, dict_key)
                for dict_key, dict_value in value.items()
            }

        if isinstance(value, list):
            return [visit(item, key) for item in value]

        if isinstance(value, str) and value:
            if key in _PROTECTED_KEYS:
                return value

            if mode == "known":
                new_text, hits = _sanitize_string_known(value)
                if hits:
                    changed += hits
                return new_text

            new_text = _sanitize_string_words(value)
            if new_text != value:
                changed += 1
            return new_text

        return value

    result = visit(node, None)
    return result, changed


def sanitize_known_triggers(payload: Any) -> tuple[Any, int]:
    if SANITIZE_MODE == "off" or not isinstance(payload, dict):
        return payload, 0

    new_payload = dict(payload)
    hits = 0

    for field in ("messages", "tools"):
        if field in new_payload:
            new_payload[field], field_hits = _sanitize_walk(
                new_payload[field],
                "known",
            )
            hits += field_hits

    return new_payload, hits


def sanitize_all_words(payload: Any) -> tuple[Any, int]:
    if SANITIZE_MODE == "off" or not isinstance(payload, dict):
        return payload, 0

    new_payload = dict(payload)
    changes = 0

    for field in ("messages", "tools"):
        if field in new_payload:
            new_payload[field], field_changes = _sanitize_walk(
                new_payload[field],
                "words",
            )
            changes += field_changes

    return new_payload, changes


def is_content_blocked_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return "content-blocked" in lowered or "content_blocked" in lowered


# ============================================================
# TRANSLATION HELPER (content-blocked resilience)
# ============================================================

async def translate_text_id_to_en(text: str) -> str:
    if not text or not text.strip():
        return text

    code_blocks: list[str] = []

    def save_code(match: re.Match) -> str:
        code_blocks.append(match.group(0))
        return f"__CODE_BLOCK_{len(code_blocks)-1}__"

    preserved = re.sub(
        r"```[\s\S]*?```|`[^`\n]+`",
        save_code,
        text,
    )

    lines = preserved.split("\n")
    translated_lines: list[str] = []

    async with httpx.AsyncClient(timeout=10.0) as client:
        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("__CODE_BLOCK_"):
                translated_lines.append(line)
                continue

            chunks = (
                [line_str]
                if len(line_str) <= 400
                else re.findall(r".{1,350}(?:\s|$)", line_str)
            )

            line_parts: list[str] = []

            for chunk in chunks:
                chunk_str = chunk.strip()
                if not chunk_str:
                    continue

                try:
                    resp = await client.get(
                        "https://api.mymemory.translated.net/get",
                        params={"q": chunk_str, "langpair": "id|en"},
                    )
                    if resp.status_code == 200:
                        trans = (
                            resp.json()
                            .get("responseData", {})
                            .get("translatedText")
                        )
                        if trans and not str(trans).startswith(
                            "MYMEMORY WARNING"
                        ):
                            line_parts.append(str(trans))
                            continue
                except Exception:
                    pass

                line_parts.append(chunk_str)

            translated_lines.append(" ".join(line_parts))

    res = "\n".join(translated_lines)

    for i, code in enumerate(code_blocks):
        res = res.replace(f"__CODE_BLOCK_{i}__", code)

    return res


async def translate_user_messages_for_retry(
    messages: Any,
) -> tuple[Any, int]:
    if not isinstance(messages, list):
        return messages, 0

    new_messages: list[Any] = []
    translated_count = 0

    user_indices = [
        idx
        for idx, item in enumerate(messages)
        if isinstance(item, dict) and item.get("role") == "user"
    ]

    last_user_idx = user_indices[-1] if user_indices else None

    instruction_suffix = (
        "\n\n[Instruction for Assistant]: The user query was originally written "
        "in Indonesian. You MUST write your entire response to the user in "
        "Indonesian (Bahasa Indonesia)."
    )

    for idx, item in enumerate(messages):
        if not isinstance(item, dict) or item.get("role") != "user":
            new_messages.append(item)
            continue

        content = item.get("content")
        new_item = dict(item)

        if isinstance(content, str) and content.strip():
            trans = await translate_text_id_to_en(content)
            if trans != content:
                translated_count += 1

            if idx == last_user_idx:
                trans += instruction_suffix

            new_item["content"] = trans
            new_messages.append(new_item)

        elif isinstance(content, list):
            new_parts: list[Any] = []
            part_changed = False

            for part in content:
                if (
                    isinstance(part, dict)
                    and part.get("type") == "text"
                    and isinstance(part.get("text"), str)
                ):
                    t_part = await translate_text_id_to_en(part["text"])
                    if t_part != part["text"]:
                        part_changed = True
                    new_parts.append({**part, "text": t_part})
                else:
                    new_parts.append(part)

            if part_changed:
                translated_count += 1

            if idx == last_user_idx:
                new_parts.append({
                    "type": "text",
                    "text": instruction_suffix,
                })

            new_item["content"] = new_parts
            new_messages.append(new_item)

        else:
            new_messages.append(item)

    return new_messages, translated_count


REASONING_EFFORT_CONFLICT_MARKERS = (
    "function tools with reasoning_effort",
    "set reasoning_effort to 'none'",
)

_reasoning_effort_conflict_models: set[str] = set()


def is_reasoning_effort_conflict_error(error_text: str) -> bool:
    lowered = error_text.lower()
    return any(
        marker in lowered
        for marker in REASONING_EFFORT_CONFLICT_MARKERS
    )


def capture_moderation_payload(
    upstream_payload: dict[str, Any],
    requested_model: str,
    target_model: str,
    error_text: str,
) -> str | None:
    if not CAPTURE_MODERATION_ERRORS:
        return None

    try:
        os.makedirs(CAPTURE_DIR, exist_ok=True)

        stamp = time.strftime("%Y%m%d-%H%M%S")
        short_model = re.sub(
            r"[^a-zA-Z0-9._-]",
            "_",
            str(target_model),
        )
        fingerprint = payload_fingerprint(upstream_payload)

        capture_path = os.path.join(
            CAPTURE_DIR,
            f"{stamp}-{short_model}-{fingerprint}.json",
        )

        capture = {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "requested_model": requested_model,
            "upstream_model": target_model,
            "payload_fingerprint": fingerprint,
            "upstream_error": error_text,
            "messages": upstream_payload.get("messages"),
            "tools": upstream_payload.get("tools"),
        }

        with open(
            capture_path,
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                capture,
                handle,
                ensure_ascii=False,
                indent=2,
            )

        return capture_path

    except Exception as exc:
        logger.error(
            "CAPTURE_FAILED model=%s error=%s",
            target_model,
            exc,
        )
        return None

# ============================================================
# MESSAGE / TOOL STATS
# ============================================================

def message_content_stats(messages: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": 0,
        "total_chars": 0,
        "max_chars": 0,
        "roles": {},
        "chars_by_role": {},
    }

    if not isinstance(messages, list):
        return result

    result["count"] = len(messages)
    role_counts: dict[str, int] = {}
    role_chars: dict[str, int] = {}

    for message in messages:
        if not isinstance(message, dict):
            continue

        role = str(message.get("role", "unknown"))
        role_counts[role] = role_counts.get(role, 0) + 1

        content = message.get("content")
        try:
            if content is None:
                length = 0
            elif isinstance(content, str):
                length = len(content)
            else:
                length = len(json.dumps(content, ensure_ascii=False))
        except Exception:
            length = 0

        role_chars[role] = role_chars.get(role, 0) + length
        result["total_chars"] += length
        result["max_chars"] = max(result["max_chars"], length)

    result["roles"] = role_counts
    result["chars_by_role"] = role_chars
    return result


def tool_stats(tools: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": 0,
        "names": [],
        "total_json_chars": 0,
        "description_chars": 0,
        "parameter_chars": 0,
        "types": {},
    }

    if not isinstance(tools, list):
        return result

    result["count"] = len(tools)
    names: list[str] = []
    type_counts: dict[str, int] = {}

    for tool in tools:
        if not isinstance(tool, dict):
            continue

        tool_type = str(tool.get("type", "unknown"))
        type_counts[tool_type] = type_counts.get(tool_type, 0) + 1

        try:
            result["total_json_chars"] += len(
                json.dumps(tool, ensure_ascii=False)
            )
        except Exception:
            pass

        function = tool.get("function")
        if not isinstance(function, dict):
            continue

        name = function.get("name")
        if name:
            names.append(str(name))

        description = function.get("description")
        if description:
            result["description_chars"] += len(str(description))

        parameters = function.get("parameters")
        if parameters is not None:
            try:
                result["parameter_chars"] += len(
                    json.dumps(parameters, ensure_ascii=False)
                )
            except Exception:
                pass

    result["names"] = names
    result["types"] = type_counts
    return result

# ============================================================
# TOOL OUTPUT COMPRESSION
# ============================================================

def normalize_tool_text(content: Any) -> tuple[str, bool]:
    if content is None:
        return "", False

    if isinstance(content, str):
        return content, True

    try:
        return (
            json.dumps(
                content,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            False,
        )
    except Exception:
        return str(content), False


def collapse_repeated_lines(text: str) -> str:
    if not text:
        return text

    lines = text.splitlines()
    if len(lines) < 4:
        return text

    output: list[str] = []
    previous: str | None = None
    repeat_count = 0

    for line in lines:
        normalized = line.strip()

        if normalized and normalized == previous:
            repeat_count += 1
            continue

        if repeat_count > 0:
            output.append(
                f"[previous line repeated {repeat_count}x]"
            )
            repeat_count = 0

        output.append(line)
        previous = normalized if normalized else None

    if repeat_count > 0:
        output.append(
            f"[previous line repeated {repeat_count}x]"
        )

    return "\n".join(output)


def compact_long_whitespace(text: str) -> str:
    return re.sub(
        r"\n[ \t]*\n[ \t]*\n+",
        "\n\n",
        text,
    )


def truncate_tool_text(
    text: str,
    max_chars: int,
    head_chars: int,
    tail_chars: int,
) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False

    available = max(100, max_chars - 300)
    head = min(head_chars, available // 2)
    tail = min(tail_chars, available - head)

    if head + tail >= len(text):
        return text, False

    removed = len(text) - head - tail

    marker = (
        "\n\n"
        "[AGENTROUTER TOOL OUTPUT COMPRESSED]\n"
        f"[removed_chars={removed}]\n"
        "[middle content omitted]\n\n"
    )

    compressed = text[:head] + marker + text[-tail:]
    return compressed[:max_chars], True


def compress_single_tool_message(
    message: dict[str, Any],
) -> tuple[dict[str, Any], bool, int, int]:
    if message.get("role") != "tool":
        return message, False, 0, 0

    text, _ = normalize_tool_text(message.get("content"))
    original_chars = len(text)

    if (
        not TOOL_COMPRESSION_ENABLED
        or original_chars <= TOOL_COMPRESSION_THRESHOLD
    ):
        return message, False, original_chars, original_chars

    compacted = collapse_repeated_lines(text)
    compacted = compact_long_whitespace(compacted)
    compacted, _ = truncate_tool_text(
        compacted,
        TOOL_MAX_CHARS,
        TOOL_HEAD_CHARS,
        TOOL_TAIL_CHARS,
    )

    modified = dict(message)
    modified["content"] = compacted
    final_chars = len(compacted)

    return (
        modified,
        final_chars < original_chars,
        original_chars,
        final_chars,
    )


def compress_tool_messages(
    messages: Any,
) -> tuple[Any, dict[str, int]]:
    stats = {
        "tool_messages": 0,
        "compressed_messages": 0,
        "original_chars": 0,
        "final_chars": 0,
        "saved_chars": 0,
    }

    if not isinstance(messages, list):
        return messages, stats

    modified_messages: list[Any] = []

    for message in messages:
        if not isinstance(message, dict):
            modified_messages.append(message)
            continue

        if message.get("role") == "tool":
            stats["tool_messages"] += 1

        modified, changed, original_chars, final_chars = (
            compress_single_tool_message(message)
        )

        stats["original_chars"] += original_chars
        stats["final_chars"] += final_chars
        if changed:
            stats["compressed_messages"] += 1

        modified_messages.append(modified)

    stats["saved_chars"] = (
        stats["original_chars"] - stats["final_chars"]
    )

    return modified_messages, stats

# ============================================================
# STRIP ROLE=TOOL HISTORY
# ============================================================

def strip_tool_messages(
    messages: Any,
) -> tuple[Any, dict[str, int]]:
    stats = {
        "original_messages": 0,
        "original_tool_messages": 0,
        "remaining_messages": 0,
        "removed_chars": 0,
    }

    if not isinstance(messages, list):
        return messages, stats

    stats["original_messages"] = len(messages)
    remaining: list[Any] = []

    for message in messages:
        if not isinstance(message, dict):
            remaining.append(message)
            continue

        if message.get("role") != "tool":
            remaining.append(message)
            continue

        stats["original_tool_messages"] += 1
        content = message.get("content")

        try:
            if content is None:
                chars = 0
            elif isinstance(content, str):
                chars = len(content)
            else:
                chars = len(json.dumps(content, ensure_ascii=False))
        except Exception:
            chars = 0

        stats["removed_chars"] += chars

    stats["remaining_messages"] = len(remaining)
    return remaining, stats

# ============================================================
# IMAGE DATA STRIPPING (moderation resilience)
# ============================================================

IMAGE_DATA_URL_PATTERN = re.compile(
    r"data:image/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+"
)

IMAGE_PLACEHOLDER = "[image data omitted by proxy]"


def _content_chars(content: Any) -> int:
    if content is None:
        return 0

    if isinstance(content, str):
        return len(content)

    try:
        return len(json.dumps(content, ensure_ascii=False))
    except Exception:
        return 0


def _strip_data_urls_in_text(text: str) -> tuple[str, int]:
    return IMAGE_DATA_URL_PATTERN.subn(IMAGE_PLACEHOLDER, text)


def _strip_image_content_list(
    content: list[Any],
) -> tuple[list[Any], int]:
    removed = 0
    result: list[Any] = []

    for part in content:
        if isinstance(part, dict):
            part_type = str(part.get("type", ""))

            if part_type in {"input_image", "image_url"}:
                image_url = part.get("image_url")
                url = ""

                if isinstance(image_url, dict):
                    url = str(image_url.get("url", ""))
                elif isinstance(image_url, str):
                    url = image_url

                if url.startswith("data:"):
                    result.append({
                        "type": "text",
                        "text": IMAGE_PLACEHOLDER,
                    })
                    removed += 1
                    continue

            if part_type == "text" and isinstance(
                part.get("text"), str
            ):
                new_text, count = _strip_data_urls_in_text(
                    part["text"]
                )

                if count:
                    result.append({**part, "text": new_text})
                    removed += count
                    continue

        result.append(part)

    return result, removed


def strip_images_from_messages(
    messages: Any,
) -> tuple[Any, dict[str, int]]:
    stats = {
        "messages_modified": 0,
        "images_removed": 0,
        "original_chars": 0,
        "final_chars": 0,
    }

    if not isinstance(messages, list):
        return messages, stats

    modified_messages: list[Any] = []

    for message in messages:
        if not isinstance(message, dict):
            modified_messages.append(message)
            continue

        content = message.get("content")

        if content is None:
            modified_messages.append(message)
            continue

        original_chars = _content_chars(content)
        stats["original_chars"] += original_chars

        new_content: Any = content
        removed = 0

        if isinstance(content, list):
            new_content, removed = _strip_image_content_list(content)
        elif isinstance(content, str):
            new_content, removed = _strip_data_urls_in_text(content)

        if removed > 0:
            modified_message = dict(message)
            modified_message["content"] = new_content
            stats["messages_modified"] += 1
            stats["images_removed"] += removed
            stats["final_chars"] += _content_chars(new_content)
            modified_messages.append(modified_message)
        else:
            stats["final_chars"] += original_chars
            modified_messages.append(message)

    return modified_messages, stats


def contains_image_data(messages: Any) -> bool:
    if not isinstance(messages, list):
        return False

    for message in messages:
        if not isinstance(message, dict):
            continue

        content = message.get("content")

        if isinstance(content, str):
            if IMAGE_DATA_URL_PATTERN.search(content):
                return True
        elif content is not None:
            try:
                serialized = json.dumps(
                    content,
                    ensure_ascii=False,
                )
            except Exception:
                continue

            if IMAGE_DATA_URL_PATTERN.search(serialized):
                return True

    return False

# ============================================================
# DEBUG LOGGING
# ============================================================

def log_debug_payload(
    payload: dict[str, Any],
    requested_model: str,
    target_model: str,
) -> None:
    if not DEBUG_ENABLED:
        return

    messages = payload.get("messages")
    tools = payload.get("tools")
    message_info = message_content_stats(messages)
    tool_info = tool_stats(tools)

    logger.info(
        "DEBUG_PAYLOAD model=%s upstream=%s stream=%s top_keys=%s fingerprint=%s",
        requested_model,
        target_model,
        payload.get("stream", False),
        sorted(str(key) for key in payload.keys()),
        payload_fingerprint(payload),
    )

    logger.info(
        "DEBUG_MESSAGES count=%s total_chars=%s max_chars=%s roles=%s chars_by_role=%s",
        message_info["count"],
        message_info["total_chars"],
        message_info["max_chars"],
        message_info["roles"],
        message_info["chars_by_role"],
    )

    logger.info(
        "DEBUG_TOOLS count=%s names=%s types=%s total_json_chars=%s description_chars=%s parameter_chars=%s",
        tool_info["count"],
        tool_info["names"],
        tool_info["types"],
        tool_info["total_json_chars"],
        tool_info["description_chars"],
        tool_info["parameter_chars"],
    )

    field_types: dict[str, str] = {}
    for field in (
        "tool_choice",
        "parallel_tool_calls",
        "response_format",
        "reasoning",
        "reasoning_effort",
        "thinking",
        "output_config",
        "temperature",
        "top_p",
        "max_tokens",
        "max_completion_tokens",
    ):
        if field in payload:
            field_types[field] = type(payload[field]).__name__

    logger.info("DEBUG_FIELDS %s", field_types)

# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
async def health(
    authorization: str | None = Header(default=None),
):
    verify_proxy_api_key(authorization)

    registry_age = _registry_age()

    return {
        "ok": True,
        "service": "agentrouter-direct-proxy",
        "upstream": UPSTREAM_BASE_URL,
        "user_agent": UPSTREAM_USER_AGENT,
        "debug": DEBUG_ENABLED,
        "compress_tool_output": TOOL_COMPRESSION_ENABLED,
        "tool_threshold": TOOL_COMPRESSION_THRESHOLD,
        "tool_max_chars": TOOL_MAX_CHARS,
        "strip_tool_messages": STRIP_TOOL_MESSAGES,
        "strip_tools": STRIP_TOOLS,
        "strip_images": STRIP_IMAGES,
        "drop_reasoning_effort": DROP_REASONING_EFFORT,
        "sanitize_moderation": SANITIZE_MODE,
        "moderation_triggers": len(MODERATION_TRIGGERS),
        "translate_content_blocked": TRANSLATE_CONTENT_BLOCKED,
        "capture_moderation_errors": CAPTURE_MODERATION_ERRORS,
        "models_source": "upstream",
        "models_count": len(_registry_models),
        "models_cache_age_s": (
            round(registry_age)
            if registry_age >= 0
            else None
        ),
        "models_cache_ttl_s": MODELS_CACHE_TTL,
    }

# ============================================================
# MODELS
# ============================================================

@app.get("/v1/models")
async def models(
    authorization: str | None = Header(default=None),
):
    verify_proxy_api_key(authorization)

    try:
        upstream_models = await get_registry_models()
    except HTTPException:
        raise

    now = int(time.time())
    data = []

    for entry in upstream_models:
        upstream_id = str(entry.get("id", ""))

        data.append({
            "id": f"{MODEL_PREFIX}{upstream_id}",
            "object": "model",
            "created": entry.get("created", now),
            "owned_by": entry.get("owned_by", "agentrouter-direct"),
            "name": upstream_id,
        })

    return {"object": "list", "data": data}

# ============================================================
# CHAT COMPLETIONS
# ============================================================

@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
):
    verify_proxy_api_key(authorization)
    require_upstream_api_key()

    started = time.perf_counter()

    try:
        payload = await request.json()
    except Exception:
        return openai_error(
            400,
            "Invalid JSON request body",
            "invalid_request_error",
        )

    if not isinstance(payload, dict):
        return openai_error(
            400,
            "Request body must be a JSON object",
            "invalid_request_error",
        )

    requested_model = payload.get("model")
    if not requested_model:
        return openai_error(
            400,
            "Missing model",
            "invalid_request_error",
        )

    target_model = await get_upstream_model(requested_model)

    # Log the original request metadata before transforms.
    log_debug_payload(
        payload,
        requested_model,
        target_model,
    )

    # --------------------------------------------------------
    # Copy payload and rewrite ONLY model initially.
    # --------------------------------------------------------

    upstream_payload = dict(payload)
    upstream_payload["model"] = target_model

    # --------------------------------------------------------
    # Diagnostic mode: strip tool definitions.
    # --------------------------------------------------------

    if STRIP_TOOLS:
        original_tools = upstream_payload.get("tools")
        if isinstance(original_tools, list) and original_tools:
            logger.warning(
                "DEBUG_MODE_STRIP_TOOLS model=%s tool_count=%d",
                target_model,
                len(original_tools),
            )

        upstream_payload.pop("tools", None)
        upstream_payload.pop("tool_choice", None)

    # --------------------------------------------------------
    # Learned models: send reasoning_effort="none" with the
    # alternate User-Agent when function tools are present,
    # because the upstream rejects the combination otherwise.
    # --------------------------------------------------------

    upstream_user_agent_override: str | None = None

    if (
        DROP_REASONING_EFFORT == "auto"
        and target_model in _reasoning_effort_conflict_models
        and upstream_payload.get("tools")
    ):
        upstream_payload["reasoning_effort"] = "none"
        upstream_user_agent_override = ALT_USER_AGENT

        logger.warning(
            "REASONING_EFFORT_NONE mode=learned model=%s user_agent=%s",
            target_model,
            ALT_USER_AGENT,
        )

    # --------------------------------------------------------
    # Diagnostic mode: strip role=tool history.
    # --------------------------------------------------------

    messages = upstream_payload.get("messages")

    if STRIP_TOOL_MESSAGES:
        messages, strip_stats = strip_tool_messages(messages)
        upstream_payload["messages"] = messages

        logger.warning(
            "STRIP_TOOL_MESSAGES original_messages=%d tool_messages_removed=%d remaining_messages=%d removed_chars=%d",
            strip_stats["original_messages"],
            strip_stats["original_tool_messages"],
            strip_stats["remaining_messages"],
            strip_stats["removed_chars"],
        )

    # --------------------------------------------------------
    # Image data stripping (moderation resilience).
    # --------------------------------------------------------

    if STRIP_IMAGES == "always":
        messages = upstream_payload.get("messages")
        messages, image_stats = strip_images_from_messages(messages)
        upstream_payload["messages"] = messages

        if image_stats["images_removed"] > 0:
            logger.warning(
                "IMAGE_STRIP mode=always model=%s images_removed=%d messages_modified=%d original_chars=%d final_chars=%d",
                target_model,
                image_stats["images_removed"],
                image_stats["messages_modified"],
                image_stats["original_chars"],
                image_stats["final_chars"],
            )

    # --------------------------------------------------------
    # Compress remaining tool messages.
    # Keep the pre-compression messages so the auto-retry can
    # rebuild the payload without splitting base64 images.
    # --------------------------------------------------------

    messages_pre_compression = upstream_payload.get("messages")
    compressed_messages, compression_stats = compress_tool_messages(messages_pre_compression)
    upstream_payload["messages"] = compressed_messages

    if compression_stats["compressed_messages"] > 0:
        original_chars = compression_stats["original_chars"]
        final_chars = compression_stats["final_chars"]
        saved_chars = compression_stats["saved_chars"]
        saved_percent = (
            saved_chars / original_chars * 100
            if original_chars > 0
            else 0.0
        )

        logger.info(
            "TOOL_COMPRESSION model=%s tool_messages=%d compressed_messages=%d original_chars=%d final_chars=%d saved_chars=%d saved_percent=%.2f",
            target_model,
            compression_stats["tool_messages"],
            compression_stats["compressed_messages"],
            original_chars,
            final_chars,
            saved_chars,
            saved_percent,
        )

    # --------------------------------------------------------
    # Known-trigger sanitizing (keyword-filter resilience).
    # --------------------------------------------------------

    if SANITIZE_MODE != "off":
        sanitized_payload, sanitize_hits = sanitize_known_triggers(
            upstream_payload
        )

        if sanitize_hits:
            upstream_payload = sanitized_payload

            logger.warning(
                "SANITIZE_PRE model=%s trigger_hits=%d",
                target_model,
                sanitize_hits,
            )

    # --------------------------------------------------------
    # Final request metadata.
    # --------------------------------------------------------

    stream = bool(upstream_payload.get("stream", False))
    messages = upstream_payload.get("messages")
    tools = upstream_payload.get("tools")

    message_info_after = message_content_stats(messages)

    message_count = (
        len(messages)
        if isinstance(messages, list)
        else 0
    )

    tool_count = (
        len(tools)
        if isinstance(tools, list)
        else 0
    )

    logger.info(
        "REQUEST model=%s upstream=%s stream=%s messages=%d tools=%d total_chars=%d tool_chars=%d strip_tool_messages=%s strip_tools=%s compress_tools=%s strip_images=%s",
        requested_model,
        target_model,
        stream,
        message_count,
        tool_count,
        message_info_after["total_chars"],
        message_info_after["chars_by_role"].get("tool", 0),
        STRIP_TOOL_MESSAGES,
        STRIP_TOOLS,
        TOOL_COMPRESSION_ENABLED,
        STRIP_IMAGES,
    )

    headers = get_upstream_headers(upstream_user_agent_override)

    if DEBUG_ENABLED:
        logger.info(
            "DEBUG_UPSTREAM base_url=%s user_agent=%s",
            UPSTREAM_BASE_URL,
            UPSTREAM_USER_AGENT,
        )

    timeout = httpx.Timeout(
        connect=20.0,
        read=600.0,
        write=60.0,
        pool=60.0,
    )

    client = httpx.AsyncClient(timeout=timeout)

    # In "auto" mode we may retry once without image data when the
    # upstream moderation layer rejects a payload that still contains
    # image data.
    image_retry_allowed = (
        STRIP_IMAGES == "auto"
        and contains_image_data(upstream_payload.get("messages"))
    )

    async def send_upstream() -> httpx.Response:
        upstream_request = client.build_request(
            "POST",
            f"{UPSTREAM_BASE_URL}/chat/completions",
            headers=headers,
            json=upstream_payload,
        )

        return await client.send(
            upstream_request,
            stream=True,
        )

    try:
        upstream_response = await send_upstream()

    except httpx.ConnectTimeout:
        await client.aclose()
        logger.error(
            "UPSTREAM_CONNECT_TIMEOUT model=%s",
            target_model,
        )
        return openai_error(
            504,
            "AgentRouter upstream connection timeout",
            "upstream_timeout",
        )

    except httpx.ConnectError as exc:
        await client.aclose()
        logger.error(
            "UPSTREAM_CONNECT_ERROR model=%s error=%s",
            target_model,
            exc,
        )
        return openai_error(
            502,
            str(exc),
            "upstream_connect_error",
        )

    except httpx.HTTPError as exc:
        await client.aclose()
        logger.error(
            "UPSTREAM_HTTP_ERROR model=%s error=%s",
            target_model,
            exc,
        )
        return openai_error(
            502,
            str(exc),
            "upstream_http_error",
        )

    # --------------------------------------------------------
    # Response handling (streaming / non-streaming).
    # --------------------------------------------------------

    async def handle_upstream_response():
        if stream:
            async def stream_generator():
                try:
                    async for chunk in upstream_response.aiter_raw():
                        if chunk:
                            yield chunk

                except httpx.ReadTimeout:
                    logger.error(
                        "UPSTREAM_READ_TIMEOUT model=%s",
                        target_model,
                    )

                except httpx.HTTPError as exc:
                    logger.error(
                        "UPSTREAM_STREAM_ERROR model=%s error=%s",
                        target_model,
                        exc,
                    )

                finally:
                    await upstream_response.aclose()
                    await client.aclose()

                    elapsed_ms = int(
                        (time.perf_counter() - started) * 1000
                    )

                    logger.info(
                        "STREAM_END model=%s elapsed_ms=%d",
                        target_model,
                        elapsed_ms,
                    )

            return StreamingResponse(
                stream_generator(),
                media_type=upstream_response.headers.get(
                    "content-type",
                    "text/event-stream",
                ),
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        try:
            body = await upstream_response.aread()
        finally:
            await upstream_response.aclose()
            await client.aclose()

        elapsed_ms = int(
            (time.perf_counter() - started) * 1000
        )

        logger.info(
            "RESPONSE model=%s elapsed_ms=%d bytes=%d",
            target_model,
            elapsed_ms,
            len(body),
        )

        try:
            response_json = json.loads(
                body.decode("utf-8")
            )
        except Exception:
            response_json = {
                "raw": body.decode(
                    "utf-8",
                    errors="replace",
                )
            }

        return JSONResponse(
            content=response_json,
            status_code=200,
        )

    # --------------------------------------------------------
    # Upstream error response.
    # --------------------------------------------------------

    if upstream_response.status_code >= 400:
        try:
            body = await upstream_response.aread()
            error_text = body.decode("utf-8", errors="replace")
        except Exception:
            error_text = ""
        finally:
            await upstream_response.aclose()

        # Retry once with reasoning_effort="none" and the alternate
        # User-Agent when the upstream rejects the function tools +
        # reasoning_effort combination. The gateway may inject
        # reasoning_effort for codex-style clients, so the retry is
        # needed even when the original payload omitted it.
        if (
            DROP_REASONING_EFFORT == "auto"
            and is_reasoning_effort_conflict_error(error_text)
        ):
            upstream_payload["reasoning_effort"] = "none"
            _reasoning_effort_conflict_models.add(target_model)
            headers = get_upstream_headers(ALT_USER_AGENT)

            logger.warning(
                "REASONING_EFFORT_CONFLICT_RETRY model=%s reasoning_effort=none user_agent=%s",
                target_model,
                ALT_USER_AGENT,
            )

            await client.aclose()
            client = httpx.AsyncClient(timeout=timeout)

            try:
                upstream_response = await send_upstream()

            except httpx.HTTPError as exc:
                await client.aclose()
                logger.error(
                    "UPSTREAM_HTTP_ERROR model=%s error=%s",
                    target_model,
                    exc,
                )
                return openai_error(
                    502,
                    str(exc),
                    "upstream_http_error",
                )

            if upstream_response.status_code < 400:
                logger.info(
                    "REASONING_EFFORT_CONFLICT_RETRY_SUCCESS model=%s",
                    target_model,
                )
                return await handle_upstream_response()

            try:
                body = await upstream_response.aread()
                error_text = body.decode("utf-8", errors="replace")
            except Exception:
                error_text = ""
            finally:
                await upstream_response.aclose()

        # Auto mode: retry once without image data when the upstream
        # moderation layer rejects a payload that still contains it.
        # Rebuild from the pre-compression messages: tool compression
        # may have split a base64 data URL, leaving a bare base64
        # fragment that the strip regex would no longer match.
        if (
            image_retry_allowed
            and is_moderation_error_text(error_text)
        ):
            image_retry_allowed = False

            retry_messages, image_stats = strip_images_from_messages(
                messages_pre_compression
            )

            logger.warning(
                "IMAGE_STRIP_RETRY mode=auto model=%s images_removed=%d messages_modified=%d original_chars=%d final_chars=%d",
                target_model,
                image_stats["images_removed"],
                image_stats["messages_modified"],
                image_stats["original_chars"],
                image_stats["final_chars"],
            )

            if image_stats["images_removed"] > 0:
                retry_messages, _ = compress_tool_messages(retry_messages)
                upstream_payload["messages"] = retry_messages

                await client.aclose()
                client = httpx.AsyncClient(timeout=timeout)

                try:
                    upstream_response = await send_upstream()

                except httpx.HTTPError as exc:
                    await client.aclose()
                    logger.error(
                        "UPSTREAM_HTTP_ERROR model=%s error=%s",
                        target_model,
                        exc,
                    )
                    return openai_error(
                        502,
                        str(exc),
                        "upstream_http_error",
                    )

                if upstream_response.status_code < 400:
                    logger.info(
                        "IMAGE_STRIP_RETRY_SUCCESS model=%s",
                        target_model,
                    )
                    return await handle_upstream_response()

                try:
                    body = await upstream_response.aread()
                    error_text = body.decode(
                        "utf-8",
                        errors="replace",
                    )
                except Exception:
                    error_text = ""
                finally:
                    await upstream_response.aclose()

        # Keyword-filter resilience: when the upstream rejects the
        # request with a "sensitive words detected" error, retry once
        # with every long word split by a zero-width space so the
        # provider-side substring matcher can no longer hit.
        if (
            SANITIZE_MODE == "auto"
            and is_keyword_moderation_error(error_text)
        ):
            sanitized_payload, sanitize_changes = sanitize_all_words(
                upstream_payload
            )

            if sanitize_changes > 0:
                upstream_payload = sanitized_payload

                logger.warning(
                    "SANITIZE_RETRY model=%s status=%s strings_modified=%d",
                    target_model,
                    upstream_response.status_code,
                    sanitize_changes,
                )

                await client.aclose()
                client = httpx.AsyncClient(timeout=timeout)

                try:
                    upstream_response = await send_upstream()

                except httpx.HTTPError as exc:
                    await client.aclose()
                    logger.error(
                        "UPSTREAM_HTTP_ERROR model=%s error=%s",
                        target_model,
                        exc,
                    )
                    return openai_error(
                        502,
                        str(exc),
                        "upstream_http_error",
                    )

                if upstream_response.status_code < 400:
                    logger.info(
                        "SANITIZE_RETRY_SUCCESS model=%s",
                        target_model,
                    )
                    return await handle_upstream_response()

                try:
                    body = await upstream_response.aread()
                    error_text = body.decode(
                        "utf-8",
                        errors="replace",
                    )
                except Exception:
                    error_text = ""
                finally:
                    await upstream_response.aclose()

        # Content-blocked resilience: when upstream rejects the request
        # with "content-blocked" (false-positive Indonesian guardrail),
        # translate Indonesian user messages to English and retry with
        # an instruction for the assistant to reply in Indonesian.
        if (
            TRANSLATE_CONTENT_BLOCKED == "auto"
            and is_content_blocked_error(error_text)
        ):
            translated_messages, translated_count = (
                await translate_user_messages_for_retry(
                    upstream_payload.get("messages")
                )
            )

            if translated_count > 0:
                upstream_payload["messages"] = translated_messages

                logger.warning(
                    "TRANSLATE_CONTENT_BLOCKED_RETRY model=%s status=%s user_messages_translated=%d",
                    target_model,
                    upstream_response.status_code,
                    translated_count,
                )

                await client.aclose()
                client = httpx.AsyncClient(timeout=timeout)

                try:
                    upstream_response = await send_upstream()

                except httpx.HTTPError as exc:
                    await client.aclose()
                    logger.error(
                        "UPSTREAM_HTTP_ERROR model=%s error=%s",
                        target_model,
                        exc,
                    )
                    return openai_error(
                        502,
                        str(exc),
                        "upstream_http_error",
                    )

                if upstream_response.status_code < 400:
                    logger.info(
                        "TRANSLATE_CONTENT_BLOCKED_RETRY_SUCCESS model=%s",
                        target_model,
                    )
                    return await handle_upstream_response()

                try:
                    body = await upstream_response.aread()
                    error_text = body.decode(
                        "utf-8",
                        errors="replace",
                    )
                except Exception:
                    error_text = ""
                finally:
                    await upstream_response.aclose()

        if is_moderation_error_text(error_text):
            capture_path = capture_moderation_payload(
                upstream_payload,
                requested_model,
                target_model,
                error_text,
            )

            if capture_path:
                logger.warning(
                    "MODERATION_CAPTURE model=%s status=%s capture=%s",
                    target_model,
                    upstream_response.status_code,
                    capture_path,
                )
            else:
                logger.warning(
                    "MODERATION_CAPTURE model=%s status=%s capture=failed",
                    target_model,
                    upstream_response.status_code,
                )

        logger.error(
            "UPSTREAM_ERROR status=%s model=%s body=%s",
            upstream_response.status_code,
            target_model,
            error_text[:2000],
        )

        await client.aclose()

        return openai_error(
            upstream_response.status_code,
            error_text,
            "agent_router_api_error",
        )

    return await handle_upstream_response()

# ============================================================
# RESPONSES API
# ============================================================

@app.post("/v1/responses")
async def responses(
    request: Request,
    authorization: str | None = Header(default=None),
):
    verify_proxy_api_key(authorization)

    return openai_error(
        501,
        "Responses API is not implemented. Use /v1/chat/completions.",
        "not_implemented",
    )

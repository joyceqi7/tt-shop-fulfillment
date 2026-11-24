"""统一的 LLM 客户端封装。

这里使用 openai Python SDK 只是因为 DeepSeek / DashScope 都提供兼容协议。
实际请求地址完全由 base_url 决定；生产环境不设置任何第三方默认地址。
"""
from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from app.core.config import settings
from app.core.logger import get_logger

log = get_logger(__name__)

LLM_TIMEOUT_SECONDS = 90.0
LLM_CONNECT_TIMEOUT_SECONDS = 10.0


def _env(name: str, fallback: str = "") -> str:
    return (os.getenv(name) or fallback or "").strip()


def _text_model_config() -> tuple[str, str, str]:
    """运行时读取脚本模型配置。

    不使用 settings 单例判断 LLM 配置，避免 import 时机或测试环境导致的旧值缓存。
    """
    base_url = _env("LLM_BASE_URL", settings.LLM_BASE_URL).rstrip("/")
    api_key = _env("LLM_API_KEY", settings.LLM_API_KEY)
    model = _env("LLM_TEXT_MODEL", settings.LLM_TEXT_MODEL or "deepseek-v4-pro")
    return base_url, api_key, model


def _vision_model_config() -> tuple[str, str, str]:
    base_url = _env("VISION_BASE_URL", settings.vision_base_url).rstrip("/")
    api_key = _env("VISION_API_KEY", settings.vision_api_key)
    model = _env("LLM_VISION_MODEL", settings.LLM_VISION_MODEL or "qwen-vl-max")
    return base_url, api_key, model


def _require_text_model_config() -> tuple[str, str, str]:
    base_url, api_key, model = _text_model_config()
    if not base_url or not api_key:
        raise RuntimeError(
            "脚本模型配置缺失: "
            f"LLM_BASE_URL={'set' if base_url else 'empty'}, "
            f"LLM_API_KEY={'set' if api_key else 'empty'}, "
            f"LLM_TEXT_MODEL={model!r}"
        )
    return base_url, api_key, model


def _post_chat_completions(
    *,
    base_url: str,
    api_key: str,
    body: dict[str, Any],
    what: str,
) -> dict[str, Any]:
    """直连兼容协议 /chat/completions, 所有外部模型共享同一套硬超时与错误格式。"""
    model = str(body.get("model") or "")
    url = f"{base_url.rstrip('/')}/chat/completions"
    timeout = httpx.Timeout(
        timeout=LLM_TIMEOUT_SECONDS,
        connect=LLM_CONNECT_TIMEOUT_SECONDS,
        read=LLM_TIMEOUT_SECONDS,
        write=LLM_CONNECT_TIMEOUT_SECONDS,
        pool=LLM_CONNECT_TIMEOUT_SECONDS,
    )
    try:
        r = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=timeout,
        )
        r.raise_for_status()
        return r.json()
    except httpx.TimeoutException as e:
        raise RuntimeError(f"{what}请求超时 ({LLM_TIMEOUT_SECONDS:.0f}s) model={model} base={base_url}") from e
    except httpx.HTTPStatusError as e:
        text = (e.response.text or "")[:800]
        raise RuntimeError(f"{what} HTTP {e.response.status_code} model={model} base={base_url}: {text}") from e
    except Exception as e:
        raise RuntimeError(f"{what}请求失败 model={model} base={base_url}: {e}") from e


def chat_json(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.4,
    max_tokens: int = 6000,
) -> dict[str, Any]:
    """要求 LLM 返回 JSON。

    有些兼容接口在 response_format=json_object 下偶发返回 "{}"。
    正式版本不做模板兜底, 但允许对同一个模型做一次更强约束的重试。
    """
    base_url, api_key, configured_model = _require_text_model_config()
    model = model or configured_model
    started = time.monotonic()
    log.info("[llm] chat_json start model=%s base=%s prompt_chars=%d", model, base_url, len(system) + len(user))

    def _call(*, use_response_format: bool, retry_note: str = ""):
        body: dict[str, Any] = {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system + retry_note},
                {"role": "user", "content": user},
            ],
        }
        if use_response_format:
            body["response_format"] = {"type": "json_object"}

        data = _post_chat_completions(base_url=base_url, api_key=api_key, body=body, what="脚本模型")

        try:
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason")
            usage = data.get("usage") or {}
            if finish_reason:
                log.info("[llm] chat_json finish_reason=%s usage=%s", finish_reason, usage)
            return choice["message"].get("content") or "{}"
        except Exception as e:
            raise RuntimeError(f"脚本模型响应结构异常 model={model} base={base_url}: {str(data)[:800]}") from e

    try:
        content = _call(use_response_format=True)
    except Exception as e:
        log.warning("response_format=json_object 不被支持或调用失败, 回退到普通模式: %s", e)
        try:
            content = _call(use_response_format=False, retry_note="\n\nReply with valid JSON only.")
        except Exception as e2:
            raise RuntimeError(f"chat.completions 失败 model={model} base={base_url}: {e2}") from e2
    elapsed = time.monotonic() - started
    log.info("[llm] chat_json done model=%s elapsed=%.1fs response_chars=%d", model, elapsed, len(content))
    parsed = _safe_json_loads(content)
    if not parsed:
        log.warning("[llm] 第一次 JSON 返回为空/无效, 用精简修复请求重试一次: %r", content[:300])
        repair_system = (
            "You repair JSON. Return ONE complete valid JSON object only. "
            "No markdown, no comments, no trailing text. "
            "The object MUST contain keys: title, total_duration, scenes. "
            "scenes MUST be a non-empty array."
        )
        repair_user = (
            "The previous model response was invalid or truncated. "
            "Rewrite it as complete valid JSON only.\n\n"
            f"Invalid response:\n{content[:5000]}\n\n"
            "Original user request summary:\n"
            f"{user[:2500]}"
        )
        try:
            repair_body: dict[str, Any] = {
                "model": model,
                "temperature": 0.1,
                "max_tokens": max(max_tokens, 6000),
                "messages": [
                    {"role": "system", "content": repair_system},
                    {"role": "user", "content": repair_user},
                ],
            }
            content_data = _post_chat_completions(base_url=base_url, api_key=api_key, body=repair_body, what="脚本模型")
            repair_choice = content_data["choices"][0]
            log.info(
                "[llm] chat_json repair finish_reason=%s usage=%s",
                repair_choice.get("finish_reason"),
                content_data.get("usage") or {},
            )
            content = repair_choice["message"].get("content") or "{}"
        except Exception as e:
            raise RuntimeError(f"chat.completions 重试失败 model={model} base={base_url}: {e}") from e
        elapsed = time.monotonic() - started
        log.info("[llm] chat_json retry done model=%s elapsed=%.1fs response_chars=%d", model, elapsed, len(content))
        parsed = _safe_json_loads(content)
        if not parsed:
            raise RuntimeError(f"LLM 返回内容不是有效 JSON model={model} base={base_url}: {content[:500]!r}")
    return parsed


def chat_text(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.7,
) -> str:
    base_url, api_key, configured_model = _require_text_model_config()
    model = model or configured_model
    started = time.monotonic()
    log.info("[llm] chat_text start model=%s base=%s prompt_chars=%d", model, base_url, len(system) + len(user))
    data = _post_chat_completions(
        base_url=base_url,
        api_key=api_key,
        body={
            "model": model,
            "temperature": temperature,
            "max_tokens": 800,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        },
        what="文本模型",
    )
    try:
        text = data["choices"][0]["message"].get("content") or ""
    except Exception as e:
        raise RuntimeError(f"文本模型响应结构异常 model={model} base={base_url}: {str(data)[:800]}") from e
    log.info("[llm] chat_text done model=%s elapsed=%.1fs response_chars=%d", model, time.monotonic() - started, len(text))
    return text


def vision_describe(
    image_paths: list[Path],
    prompt: str,
    model: str | None = None,
    temperature: float = 0.3,
    json_mode: bool = True,
) -> dict[str, Any] | str:
    """给 LLM 看一组图（视频抽帧），返回结构化描述。

    使用独立的 vision client（可以和文本走不同厂商，例如文本走 DeepSeek、视觉走 Gemini）。
    """
    base_url, api_key, configured_model = _vision_model_config()
    model = model or configured_model
    if not base_url or not api_key:
        raise RuntimeError(
            "视觉模型配置缺失: "
            f"VISION_BASE_URL={'set' if base_url else 'empty'}, "
            f"VISION_API_KEY={'set' if api_key else 'empty'}, "
            f"LLM_VISION_MODEL={model!r}"
        )

    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for img in image_paths:
        b64 = base64.b64encode(Path(img).read_bytes()).decode()
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            }
        )

    body: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [{"role": "user", "content": content}],
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    started = time.monotonic()
    log.info("[llm] vision start model=%s base=%s frames=%d", model, base_url, len(image_paths))
    if json_mode:
        try:
            data = _post_chat_completions(base_url=base_url, api_key=api_key, body=body, what="视觉模型")
        except Exception as e:
            log.debug("vision: response_format 不支持, 回退普通模式: %s", e)
            body.pop("response_format", None)
            data = _post_chat_completions(base_url=base_url, api_key=api_key, body=body, what="视觉模型")
    else:
        data = _post_chat_completions(base_url=base_url, api_key=api_key, body=body, what="视觉模型")

    try:
        text = data["choices"][0]["message"].get("content") or ""
    except Exception as e:
        raise RuntimeError(f"视觉模型响应结构异常 model={model} base={base_url}: {str(data)[:800]}") from e
    log.info("[llm] vision done model=%s elapsed=%.1fs response_chars=%d", model, time.monotonic() - started, len(text))
    log.debug("vision raw response (first 400 chars): %r", text[:400])
    if json_mode:
        parsed = _safe_json_loads(text)
        if not parsed:
            log.warning("vision JSON 解析为空, 原文: %r", text[:300])
        return parsed
    return text


def _safe_json_loads(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {}

    # strip ```...``` fences (with or without "json" tag)
    if text.startswith("```"):
        # 去掉首行 ```xxx
        nl = text.find("\n")
        if nl > 0 and len(text[3:nl].strip()) < 16:
            text = text[nl + 1 :]
        else:
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    # 第 1 次: 直接 parse
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 第 2 次: 截取第一对 {...}
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start : end + 1]
        try:
            obj = json.loads(snippet)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass

    log.warning("LLM 返回的内容无法解析为 JSON, 原文 (首 300): %r", text[:300])
    return {}

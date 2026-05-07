"""LLM API 客户端 — 支持 DeepSeek / SiliconFlow（OpenAI 兼容格式）"""

import time
import requests


def _normalize_url(base_url: str) -> str:
    """确保 URL 以 /v1/chat/completions 结尾"""
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return base_url + "/chat/completions"
    return base_url + "/v1/chat/completions"


def messages_create(
    *,
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-chat",
    system: str = "",
    messages: list[dict] | None = None,
    max_tokens: int = 2500,
    temperature: float = 0.6,
    timeout: int = 120,
    retries: int = 3,
) -> str:
    """调用 LLM API（OpenAI 兼容格式），返回文本响应"""
    url = _normalize_url(base_url)

    # 构建 messages
    api_messages = []
    if system:
        api_messages.append({"role": "system", "content": system})
    if messages:
        api_messages.extend(messages)

    payload = {
        "model": model,
        "messages": api_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    last_error = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
            if resp.status_code in (429, 500, 502, 503, 504):
                last_error = f"HTTP {resp.status_code}"
                wait = 1.5 * (attempt + 1)
                print(f"[WARN] LLM API {last_error}，{wait:.1f}s 后重试 ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()

            # OpenAI 格式响应提取
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "")
            return str(data)

        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt < retries - 1:
                wait = 1.5 * (attempt + 1)
                print(f"[WARN] LLM API 请求失败: {e}，{wait:.1f}s 后重试")
                time.sleep(wait)

    raise RuntimeError(f"LLM API 调用失败（{retries} 次重试后）: {last_error}")

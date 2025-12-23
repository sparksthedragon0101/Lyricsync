# server/core/llm_client.py
from __future__ import annotations
import os, time, json
import httpx
from typing import Literal, Optional, Dict, Any, List, Tuple

ModelKind = Literal["chat", "completion"]

class LLMResponse:
    def __init__(self, text: str, raw: dict, provider: str, model: str, latency_ms: int):
        self.text = text
        self.raw = raw
        self.provider = provider
        self.model = model
        self.latency_ms = latency_ms

class BaseProvider:
    def chat(self, messages: List[Dict[str, str]], model: str, **kw) -> LLMResponse:
        raise NotImplementedError

class OpenAICompatProvider(BaseProvider):
    """
    Works with OpenAI API or any OpenAI-compatible endpoint (e.g. local servers).
    Requires:
      - LLM_BASE_URL (e.g. https://api.openai.com/v1)
      - LLM_API_KEY
    """
    def __init__(self, base_url: str, api_key: str, provider_label: str = "openai"):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.provider_label = provider_label

    def chat(self, messages: List[Dict[str, str]], model: str, **kw) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": kw.get("temperature", 0.7), "max_tokens": kw.get("max_tokens", 800)}
        t0 = time.time()
        with httpx.Client(timeout=kw.get("timeout", 60)) as client:
            r = client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        t1 = time.time()
        text = data["choices"][0]["message"]["content"]
        return LLMResponse(text=text, raw=data, provider=self.provider_label, model=model, latency_ms=int((t1 - t0) * 1000))

class OllamaProvider(BaseProvider):
    """
    Local Ollama chat endpoint.
    Requires:
      - OLLAMA_BASE_URL (e.g. http://127.0.0.1:11434)
    """
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def chat(self, messages: List[Dict[str, str]], model: str, **kw) -> LLMResponse:
        url = f"{self.base_url}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "options": {"temperature": kw.get("temperature", 0.7)},
            "stream": False,
        }
        t0 = time.time()
        # stream=false to keep simple; we can add SSE later
        with httpx.Client(timeout=kw.get("timeout", 120)) as client:
            r = client.post(url, json=payload)
            r.raise_for_status()
            data = r.json()
        t1 = time.time()
        text = data.get("message", {}).get("content", "")
        return LLMResponse(text=text, raw=data, provider="ollama", model=model, latency_ms=int((t1 - t0) * 1000))

class LLMClient:
    """
    Provider-agnostic entry point. Reads env to decide backend.
    """
    def __init__(self):
        backend = os.getenv("LLM_PROVIDER", "openai_compat").lower()
        if backend == "ollama":
            self.provider = OllamaProvider(base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"))
        else:
            # Default: OpenAI-compatible
            self.provider = OpenAICompatProvider(
                base_url=os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"),
                api_key=os.getenv("LLM_API_KEY", "")
            )

    def chat(self, system: str, user: str | None = None, messages: Optional[List[Dict[str, str]]] = None,
             model: str | None = None, **kw) -> LLMResponse:
        if messages is None:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            if user:
                messages.append({"role": "user", "content": user})
        else:
            # allow caller to include system message inside messages
            pass
        model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        return self.provider.chat(messages=messages, model=model, **kw)

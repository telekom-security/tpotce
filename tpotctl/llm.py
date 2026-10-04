"""Ask an LLM backend which models it has, for the model fields of Beelzebub and Galah.

ollama: GET <base>/api/tags, openai: GET <base>/v1/models with the API key. The key
goes only to the endpoint of the setting and only in the Authorization header.
"""

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, List

OPENAI = "https://api.openai.com"
SUPPORTED = ("ollama", "openai")


class LLMError(Exception):
    """The models cannot be listed, the text says why."""


def base_url(url: str) -> str:
    """scheme://host:port of an endpoint (Beelzebub wants the full chat URL, Galah the base)."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise LLMError(f'"{url}" is not an http(s) URL')
    return f"{parts.scheme}://{parts.netloc}"


def request_for(provider: str, url: str, api_key: str) -> urllib.request.Request:
    if provider == "ollama":
        if not url:
            raise LLMError("set the URL of the Ollama server first")
        return urllib.request.Request(f"{base_url(url)}/api/tags")
    if provider == "openai":
        if not api_key:
            raise LLMError("set the API key first")
        base = base_url(url) if url else OPENAI
        return urllib.request.Request(f"{base}/v1/models", headers={"Authorization": f"Bearer {api_key}"})
    raise LLMError(f"listing the models of {provider} is not supported, enter the model by hand")


def parse(provider: str, answer: dict) -> List[str]:
    if provider == "ollama":
        names = [m.get("name") or m.get("model", "") for m in answer.get("models") or []]
    else:
        names = [m.get("id", "") for m in answer.get("data") or []]
    return sorted(n for n in names if n)


def list_models(provider: str, url: str, api_key: str = "", opener: Callable = urllib.request.urlopen) -> List[str]:
    request = request_for(provider, url, api_key)
    try:
        with opener(request, timeout=5) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise LLMError(f"{request.full_url} answered {err.code} {err.reason}")
    except (OSError, ValueError) as err:
        host = urllib.parse.urlsplit(request.full_url).hostname or ""
        hint = (f" The name {host} may only resolve inside the Docker network of T-Pot."
                if host and "." not in host and host != "localhost" else "")
        raise LLMError(f"{request.full_url} cannot be reached ({getattr(err, 'reason', err)}).{hint}")
    return parse(provider, answer)

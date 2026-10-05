"""The LLM backends of Beelzebub and Galah: list their models, find an Ollama, test a model.

ollama: GET <base>/api/tags, openai: GET <base>/v1/models with the API key. The key
goes only to the endpoint of the setting and only in the Authorization header.

discover() asks the usual places of an Ollama (the configured URLs, this host, its
Docker bridges and gateways) on port 11434; scan() asks every address of the /24 of
this host, only on request, it looks like a scan to the network. The honeypots run in
Docker, so an Ollama that only listens on localhost is found but not offered.
"""

import concurrent.futures
import ipaddress
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional

OPENAI = "https://api.openai.com"
SUPPORTED = ("ollama", "openai")
PORT = 11434
PROMPT = "Reply with the single word OK."
_LOOPBACK = ("localhost", "127.0.0.1", "::1")


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


def _unreachable(url: str, err: Exception) -> str:
    host = urllib.parse.urlsplit(url).hostname or ""
    hint = (f" The name {host} may only resolve inside the Docker network of T-Pot."
            if host and "." not in host and host != "localhost" else "")
    return f"{url} cannot be reached ({getattr(err, 'reason', err)}).{hint}"


def list_models(provider: str, url: str, api_key: str = "", opener: Callable = urllib.request.urlopen) -> List[str]:
    request = request_for(provider, url, api_key)
    try:
        with opener(request, timeout=5) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        raise LLMError(f"{request.full_url} answered {err.code} {err.reason}")
    except (OSError, ValueError) as err:
        raise LLMError(_unreachable(request.full_url, err))
    return parse(provider, answer)


SERVICES = {"beelzebub": ("Beelzebub", "BEELZEBUB_LLM_MODEL"), "galah": ("Galah", "GALAH_LLM_MODEL")}


def settings_of(rule, values: Dict[str, str], schema) -> Optional[Dict[str, str]]:
    """provider, url, api_key of an llm_model key (its llm field), defaults filled in."""
    if not rule.llm:
        return None
    out = {}
    for name in ("provider", "url", "api_key"):
        key = rule.llm.get(name, "")
        out[name] = values.get(key, "") or (schema[key].default if key in schema else "")
    return out


def service_settings(service: str, values: Dict[str, str], schema) -> Dict[str, str]:
    """provider, url, api_key and model of a honeypot."""
    model_key = SERVICES[service][1]
    rule = schema[model_key]
    return dict(settings_of(rule, values, schema), model=values.get(model_key, "") or rule.default)


def url_for(service: str, base: str) -> str:
    """The value of the URL setting of a honeypot for an Ollama at base."""
    return f"{base}/api/chat" if service == "beelzebub" else base


# -- finding an Ollama -------------------------------------------------------------

@dataclass
class Found:
    url: str                    # scheme://host:port
    version: str
    models: int
    reachable_from_docker: bool
    note: str = ""


def _get(url: str, opener: Callable, timeout: float) -> dict:
    with opener(urllib.request.Request(url), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def probe(base: str, opener: Callable = urllib.request.urlopen, timeout: float = 1.5) -> Optional[Found]:
    """An Ollama at base, or None."""
    try:
        version = _get(f"{base}/api/version", opener, timeout).get("version")
        if not version:
            return None
        models = len(_get(f"{base}/api/tags", opener, timeout).get("models") or [])
    except (OSError, ValueError, AttributeError):
        return None
    host = urllib.parse.urlsplit(base).hostname or ""
    if host in _LOOPBACK:
        return Found(base, version, models, False,
                     "answers on localhost, but localhost in a container is the container itself: use an "
                     "address of this host from the list (Ollama needs OLLAMA_HOST=0.0.0.0 for it)")
    if "." not in host and ":" not in host:
        return Found(base, version, models, True,
                     f"{host} only resolves inside a Docker network. The honeypot network of T-Pot has ICC "
                     f"off, an Ollama container there cannot be reached: run Ollama outside of it")
    return Found(base, version, models, True)


def _base(address: str) -> str:
    return f"http://[{address}]:{PORT}" if ":" in address else f"http://{address}:{PORT}"


def candidates(env: Dict[str, str], addresses: Iterable[str], gateways: Iterable[str]) -> List[str]:
    """Where an Ollama may be, the configured URLs first, without duplicates."""
    out: List[str] = []
    for key in ("BEELZEBUB_LLM_HOST", "GALAH_LLM_SERVER_URL"):
        try:
            if env.get(key):
                out.append(base_url(env[key]))
        except LLMError:
            pass
    out += [_base(name) for name in ("localhost", "127.0.0.1", "host.docker.internal", "ollama.local", "ollama")]
    out += [_base(address) for address in list(gateways) + list(addresses)]
    return list(dict.fromkeys(out))


def host_addresses(interfaces=None) -> List[str]:
    """The global addresses of this host, without the Docker bridges."""
    from tpotctl import netinfo
    interfaces = netinfo.interfaces() if interfaces is None else interfaces
    out = []
    for iface in interfaces:
        if iface.internal:
            continue
        for address in iface.addresses:
            ip = ipaddress.ip_interface(address).ip
            if not (ip.is_loopback or ip.is_link_local):
                out.append(str(ip))
    return out


def bridge_addresses(interfaces=None) -> List[str]:
    """The addresses of this host on its Docker bridges (docker0, br-*)."""
    from tpotctl import netinfo
    interfaces = netinfo.interfaces() if interfaces is None else interfaces
    return [str(ipaddress.ip_interface(a).ip) for i in interfaces if i.name.startswith(("docker", "br-"))
            for a in i.addresses if ipaddress.ip_interface(a).version == 4]


DESKTOP = f"http://host.docker.internal:{PORT}"


def discover(env: Dict[str, str], addresses: Optional[List[str]] = None, gateways: Optional[List[str]] = None,
             opener: Callable = urllib.request.urlopen, workers: int = 16, linux: Optional[bool] = None) -> List[Found]:
    from tpotctl import netinfo, ops
    linux = ops.linux_host() if linux is None else linux
    if addresses is None:
        addresses = host_addresses()
    if gateways is None:
        gateways = bridge_addresses() + netinfo.default_gateways()
    bases = candidates(env, addresses, gateways)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        found = [f for f in pool.map(lambda base: probe(base, opener), bases) if f is not None]
    if not linux:
        # Docker Desktop (macOS, Windows) hands host.docker.internal to the localhost of the host
        local = [f for f in found if not f.reachable_from_docker]
        found = [f for f in found if f.reachable_from_docker and f.url != DESKTOP]
        if local:
            found.insert(0, Found(DESKTOP, local[0].version, local[0].models, True,
                                  "the Ollama of this host, Docker Desktop reaches it as host.docker.internal"))
    return sorted(found, key=lambda f: not f.reachable_from_docker)


def scan_network(interfaces=None) -> str:
    """address/prefix of this host on the interface T-Pot captures on (its uplink), "" for none."""
    from tpotctl import netinfo
    interfaces = netinfo.interfaces() if interfaces is None else interfaces
    uplink = netinfo.detect()
    for iface in sorted(interfaces, key=lambda i: i.name != uplink):
        if iface.internal:
            continue
        for address in iface.addresses:
            ip = ipaddress.ip_interface(address)
            if ip.version == 4 and not (ip.ip.is_loopback or ip.ip.is_link_local):
                return address
    return ""


def scan_targets(address: str) -> List[str]:
    """Every other address of the /24 of address (of its own network if that is smaller), IPv4 only."""
    iface = ipaddress.ip_interface(address)
    if iface.version != 4:
        return []
    network = iface.network if iface.network.prefixlen >= 24 else \
        ipaddress.ip_network(f"{iface.ip}/24", strict=False)
    return [str(host) for host in network.hosts() if host != iface.ip]


def scan(targets: Iterable[str], connect: Callable = socket.create_connection,
         opener: Callable = urllib.request.urlopen, timeout: float = 0.3, workers: int = 64) -> List[Found]:
    """An Ollama on any of the targets: a TCP connect to 11434 first, then probe()."""
    def open_port(host: str) -> Optional[str]:
        try:
            connect((host, PORT), timeout=timeout).close()
            return host
        except OSError:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        hosts = [h for h in pool.map(open_port, list(targets)) if h]
        found = [f for f in pool.map(lambda h: probe(_base(h), opener), hosts) if f is not None]
    return found


# -- testing a model ---------------------------------------------------------------

@dataclass
class TestResult:
    ok: bool
    answer: str = ""
    seconds: float = 0.0
    problem: str = ""


def test(provider: str, url: str, model: str, api_key: str = "", opener: Callable = urllib.request.urlopen,
         timeout: float = 60) -> TestResult:
    """One short prompt to the model, as the honeypot would send it, from this host."""
    if not model:
        return TestResult(False, problem="set the model first")
    messages = [{"role": "user", "content": PROMPT}]
    try:
        if provider == "ollama":
            if not url:
                return TestResult(False, problem="set the URL of the Ollama server first")
            target = f"{base_url(url)}/api/chat"
            body = {"model": model, "messages": messages, "stream": False}
            headers = {"Content-Type": "application/json"}
        elif provider == "openai":
            if not api_key:
                return TestResult(False, problem="set the API key first")
            target = f"{base_url(url) if url else OPENAI}/v1/chat/completions"
            body = {"model": model, "messages": messages, "max_tokens": 20}
            headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
        else:
            return TestResult(False, problem=f"testing {provider} is not supported here")
    except LLMError as err:
        return TestResult(False, problem=str(err))
    request = urllib.request.Request(target, data=json.dumps(body).encode(), headers=headers, method="POST")
    started = time.monotonic()
    try:
        with opener(request, timeout=timeout) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as err:
        return TestResult(False, problem=f"{target} answered {err.code} {err.reason}")
    except (OSError, ValueError) as err:
        return TestResult(False, problem=_unreachable(target, err))
    seconds = time.monotonic() - started
    if provider == "ollama":
        text = (answer.get("message") or {}).get("content", "")
    else:
        text = ((answer.get("choices") or [{}])[0].get("message") or {}).get("content", "")
    text = " ".join(str(text).split())
    if len(text) > 200:
        text = text[:200] + "…"
    return TestResult(bool(text), text, seconds, "" if text else "the answer was empty")

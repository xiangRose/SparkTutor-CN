"""Standalone web AI routing, safe configuration and connection diagnostics.

Credential values and authentication headers stay in memory or DPAPI storage.
Upstream error bodies and private reasoning never become user-facing messages.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from sparktutor.config.settings import Settings
from .secret_store import LocalSecretStore, atomic_write


HEALTH_COOLDOWN = 60
MAX_RESPONSE = 2 * 1024 * 1024
PROVIDERS = {"auto", "anthropic", "openai-compatible", "copilot"}
PUBLIC_FIELDS = ("provider", "baseUrl", "model", "anthropicModel", "remember")


class WebAiError(ValueError):
    def __init__(self, kind: str, message: str, *, status: int = 0):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _http_error(status: int) -> WebAiError:
    if status in (408, 504):
        return WebAiError("timeout", "模型接口请求超时，请稍后重试。", status=status)
    if status in (401, 403):
        return WebAiError("authentication", "模型接口认证未通过，请检查密钥、额外请求头及访问权限。", status=status)
    if status == 429:
        return WebAiError("rate-limit", "模型接口受到限流或额度限制，请检查服务商账户后重试。", status=status)
    if status in (404, 405):
        return WebAiError("endpoint", "模型接口路径不可用，请检查基础地址和模型名称。", status=status)
    if status >= 500:
        return WebAiError("service", "模型服务暂时不可用，请稍后重试。", status=status)
    return WebAiError("http", f"模型接口返回 HTTP {status}，请检查地址、模型和请求配置。", status=status)


def _text(value, field: str, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{field}需要是长度不超过 {limit} 的文本。")
    return value.strip()


def _headers(value) -> dict[str, str]:
    if not isinstance(value, dict) or len(value) > 32:
        raise ValueError("额外请求头需要是最多 32 项的 JSON 对象。")
    result = {}
    for name, content in value.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,80}", name)
                or name.casefold() in {"host", "content-length", "connection"}):
            raise ValueError("额外请求头名称无效，或包含不可覆盖的传输字段。")
        if not isinstance(content, str) or len(content) > 4096 or "\r" in content or "\n" in content:
            raise ValueError("额外请求头值需要是单行文本。")
        result[name] = content
    return result


def _base_url(value) -> str:
    value = _text(value, "接口地址", 2048).rstrip("/")
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise ValueError("接口地址格式无效。") from None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("接口地址需要以 http:// 或 https:// 开头。")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("接口基础地址不能包含凭据、查询参数或片段；鉴权信息请填写在密钥或额外请求头中。")
    return value


def _binding(parameters: dict) -> str:
    return hashlib.sha256(json.dumps({key: parameters.get(key) for key in PUBLIC_FIELDS},
                                     sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


class _SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        def origin(url):
            parsed = urlsplit(url)
            return parsed.scheme, parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80)

        if origin(request.full_url) != origin(new_url):
            raise WebAiError("redirect", "模型接口重定向到了其他来源；请确认并填写最终受信任的接口地址。")
        return super().redirect_request(request, fp, code, message, headers, new_url)


class WebAiManager:
    def __init__(self, settings: Settings, *, secret_store=None, opener=None, anthropic_factory=None):
        self.settings = settings
        self.use_environment = True
        self.path = Path(settings.data_dir) / "ai-settings.json"
        self.secret_store = secret_store or LocalSecretStore(Path(settings.data_dir) / "ai-secrets.dpapi")
        self.opener = opener or build_opener(_SameOriginRedirect()).open
        self.anthropic_factory = anthropic_factory
        self.clock = time.monotonic
        self._storage_warning = ""
        self._remembered = self._read_remembered()

    def _read_remembered(self) -> dict:
        if not self.path.is_file():
            return {}
        try:
            if self.path.is_symlink() or self.path.stat().st_size > 65536:
                raise ValueError()
            content = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(content, dict):
                raise ValueError()
            remembered = {key: content[key] for key in PUBLIC_FIELDS if key in content}
            remembered["remember"] = True
            if remembered.get("provider", "auto") not in PROVIDERS:
                remembered["provider"] = "auto"
            remembered["baseUrl"] = _base_url(remembered.get("baseUrl", ""))
            remembered["model"] = _text(remembered.get("model", ""), "模型名称", 256)
            remembered["anthropicModel"] = _text(remembered.get("anthropicModel", self.settings.claude.model), "Anthropic 模型", 256)
        except (OSError, ValueError, TypeError):
            self._storage_warning = "保存的 AI 参数无法读取，请重新填写；当前学习数据不受影响。"
            return {}
        try:
            secrets = self.secret_store.load()
            if secrets and secrets.get("parametersHash") != _binding(remembered):
                raise ValueError("安全参数不匹配")
            for key in ("apiKey", "anthropicApiKey"):
                if key in secrets and isinstance(secrets[key], str):
                    remembered[key] = secrets[key]
            if "extraHeaders" in secrets:
                remembered["extraHeaders"] = _headers(secrets["extraHeaders"])
        except (OSError, ValueError, TypeError):
            self._storage_warning = "保存的密钥无法由当前服务账户解密或与接口配置不匹配，请重新填写密钥。"
        return remembered

    def new_session(self) -> dict:
        config = {
            "provider": os.environ.get("SPARKTUTOR_AI_PROVIDER", "auto"),
            "baseUrl": os.environ.get("SPARKTUTOR_OPENAI_BASE_URL", ""),
            "model": os.environ.get("SPARKTUTOR_OPENAI_MODEL", ""),
            "apiKey": os.environ.get("SPARKTUTOR_OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            "extraHeaders": {},
            "anthropicModel": self.settings.claude.get_model(),
            "anthropicApiKey": self.settings.claude.get_api_key() or "",
            "remember": False,
        }
        if not self.use_environment:
            config.update(provider='auto',baseUrl='',model='',apiKey='',extraHeaders={},
                          anthropicModel=self.settings.claude.model,anthropicApiKey='',remember=False)
        config.update(deepcopy(self._remembered))
        if config["provider"] not in PROVIDERS:
            config["provider"] = "auto"
        config["_health"] = {}
        config["_storageWarning"] = self._storage_warning
        try:
            config["baseUrl"] = _base_url(config["baseUrl"])
        except ValueError:
            config["baseUrl"] = ""
            config["_storageWarning"] = "环境变量中的接口基础地址格式无效，请在设置中重新填写；地址原文不会展示。"
        return config

    def configure(self, ai: dict, params: dict) -> dict:
        updated = deepcopy(ai)
        if "provider" in params:
            provider = "openai-compatible" if params["provider"] == "openai" else params["provider"]
            if not isinstance(provider, str) or provider not in PROVIDERS:
                raise ValueError("AI 提供方无效。")
            updated["provider"] = provider
        if "baseUrl" in params:
            updated["baseUrl"] = _base_url(params["baseUrl"])
        for key, label in (("model", "兼容接口模型"), ("anthropicModel", "Anthropic 模型")):
            if key in params:
                updated[key] = _text(params[key], label, 256)
        for key, clear in (("apiKey", "clearKey"), ("anthropicApiKey", "clearAnthropicKey")):
            if params.get(clear):
                updated[key] = ""
            elif key in params and params[key]:
                updated[key] = _text(params[key], "API 密钥", 16384)
        if params.get("clearExtraHeaders"):
            updated["extraHeaders"] = {}
        elif "extraHeaders" in params:
            updated["extraHeaders"] = _headers(params["extraHeaders"])
        if "remember" in params:
            if not isinstance(params["remember"], bool):
                raise ValueError("记住本机设置需要是布尔值。")
            updated["remember"] = params["remember"]
        updated["_health"] = {}
        updated["_storageWarning"] = ""
        if updated.get("remember"):
            remembered = {key: updated.get(key) for key in PUBLIC_FIELDS}
            secrets = {key: deepcopy(updated.get(key, "")) for key in ("apiKey", "anthropicApiKey", "extraHeaders")}
            secrets["parametersHash"] = _binding(remembered)
            saved = False
            try:
                saved = self.secret_store.save(secrets)
            except (OSError, ValueError):
                updated["_storageWarning"] = "本机安全存储未能保存密钥；密钥仅保留在当前标签页的服务内存中。"
            if not saved:
                try:
                    self.secret_store.delete()
                except (OSError, ValueError):
                    pass
                if not updated["_storageWarning"]:
                    updated["_storageWarning"] = "此系统没有可用的安全密钥存储；仅记住接口与模型，密钥和额外请求头保留在内存，可通过环境变量提供。"
            atomic_write(self.path, json.dumps(remembered, ensure_ascii=False, indent=2).encode("utf-8"))
            self._remembered = deepcopy(remembered)
            if saved:
                self._remembered.update(secrets)
        elif "remember" in params and params["remember"] is False:
            if self.path.is_symlink():
                raise ValueError("设置文件不能是符号链接。")
            self.path.unlink(missing_ok=True)
            self.secret_store.delete()
            self._remembered = {}
        ai.clear()
        ai.update(updated)
        return self.public(ai)

    def public(self, ai: dict) -> dict:
        try:
            resolved = self.resolve(ai)
        except WebAiError:
            resolved = "none"
        return {
            "provider": ai.get("provider", "auto"), "resolvedProvider": resolved,
            "baseUrl": ai.get("baseUrl", ""), "model": ai.get("model", ""),
            "hasApiKey": bool(ai.get("apiKey")),
            "anthropicModel": ai.get("anthropicModel", self.settings.claude.model),
            "hasAnthropicApiKey": bool(ai.get("anthropicApiKey")),
            "hasExtraHeaders": bool(ai.get("extraHeaders")),
            "extraHeaderNames": sorted(ai.get("extraHeaders", {})),
            "remember": bool(ai.get("remember")),
            "secureStorage": self.secret_store.name,
            "canRememberSecrets": bool(self.secret_store.supported),
            "storageWarning": ai.get("_storageWarning", ""),
            "copilotAvailable": False,
            "copilotMessage": "GitHub Copilot 的 vscode.lm 接口仅在 VS Code 扩展中可用，独立网页请配置 Anthropic 或兼容模型接口。",
        }

    def apply_to_handler(self, ai: dict, handler) -> None:
        handler.settings.claude.api_key = ai.get("anthropicApiKey") or None
        handler.settings.claude.model = ai.get("anthropicModel") or self.settings.claude.model
        handler.evaluator._client = None

    @staticmethod
    def _configured(ai: dict, provider: str) -> bool:
        if provider == "anthropic":
            return bool(ai.get("anthropicApiKey") and ai.get("anthropicModel"))
        return bool(ai.get("baseUrl") and ai.get("model"))

    def _candidates(self, ai: dict, *, coach=False, ignore_cooldown=False) -> list[str]:
        if coach:
            return ["openai-compatible"] if self._configured(ai, "openai-compatible") else []
        chosen = ai.get("provider", "auto")
        if chosen == "copilot":
            raise WebAiError("unsupported", "GitHub Copilot 依赖 VS Code 的模型接口，独立网页无法直接复用；请选择 Anthropic 或兼容接口。")
        providers = ["anthropic", "openai-compatible"] if chosen == "auto" else [chosen]
        return [provider for provider in providers if self._configured(ai, provider) and (
            chosen != "auto" or ignore_cooldown or not self._cooldown(ai, provider))]

    def _cooldown(self, ai, provider) -> bool:
        health = ai.get("_health", {}).get(provider)
        return bool(health and not health["ok"] and self.clock() - health["at"] < HEALTH_COOLDOWN)

    def resolve(self, ai: dict, coach=False, ignore_cooldown=False) -> str:
        providers = self._candidates(ai, coach=coach, ignore_cooldown=ignore_cooldown)
        return providers[0] if providers else "none"

    async def complete(self, ai: dict, messages: list, provider=None, coach=False, probe=False) -> str:
        if not isinstance(messages, list) or not messages:
            raise ValueError("模型请求缺少消息。")
        choices = [provider] if provider else self._candidates(ai, coach=coach, ignore_cooldown=probe)
        choices = [value for value in choices if value != "none"]
        if not choices:
            message = ("请先配置调试教练的兼容接口与模型。" if coach else
                       "尚未配置可用 AI 提供方，请填写 Anthropic 密钥或兼容接口与模型。")
            raise WebAiError("not-configured", message)
        deadline = self.clock() + (30 if probe else 90)
        last_error = None
        for chosen in choices:
            try:
                if chosen == "openai-compatible":
                    answer = await asyncio.to_thread(self._complete_compatible, ai, messages, deadline, probe)
                elif chosen == "anthropic":
                    answer = await asyncio.to_thread(self._complete_anthropic, ai, messages, deadline, probe)
                else:
                    raise WebAiError("unsupported", "此 AI 提供方无法在独立网页中使用。")
                ai.setdefault("_health", {})[chosen] = {"ok": True, "at": self.clock()}
                return answer
            except WebAiError as error:
                last_error = error
                if error.kind in {"empty", "incomplete"}:
                    ai.setdefault("_health", {}).pop(chosen, None)
                    raise
                ai.setdefault("_health", {})[chosen] = {"ok": False, "at": self.clock()}
                if ai.get("provider") != "auto" or coach or provider:
                    raise
        raise last_error or WebAiError("not-configured", "尚未配置可用 AI。")

    async def check_connection(self, ai: dict, coach=False) -> dict:
        started = self.clock()
        try:
            provider = self.resolve(ai, coach=coach, ignore_cooldown=True)
            await self.complete(ai, [{"role": "user", "content": "请只回复 OK，不要解释。"}], coach=coach, probe=True)
            provider = self.resolve(ai, coach=coach)
            return {"ok": True, "state": "ready", "provider": provider,
                    "message": f"模型接口已返回可用正文，用时 {round((self.clock() - started) * 1000)} 毫秒；教练提示的格式与证据仍需实际请求验证。"}
        except WebAiError as error:
            return {"ok": False, "state": "unverified" if error.kind in {"empty", "incomplete"} else "unavailable",
                    "provider": locals().get("provider", "none"), "kind": error.kind, "message": str(error)}

    @staticmethod
    def _urls(base: str) -> list[str]:
        base = _base_url(base)
        if base.endswith("/chat/completions"):
            return [base]
        result = [base + "/chat/completions"]
        if not base.lower().endswith("/v1"):
            result.append(base + "/v1/chat/completions")
        return result

    def _remaining(self, deadline: float) -> float:
        value = deadline - self.clock()
        if value <= 0:
            raise WebAiError("timeout", "模型请求超时，请稍后重试或检查服务状态。")
        return value

    def _complete_compatible(self, ai, messages, deadline, probe):
        headers = {"content-type": ("Content-Type", "application/json")}
        if ai.get("apiKey"):
            headers["authorization"] = ("Authorization", "Bearer " + ai["apiKey"])
        for name, value in _headers(ai.get("extraHeaders", {})).items():
            headers[name.casefold()] = (name, value)
        headers = dict(headers.values())
        body = {"model": ai["model"], "messages": messages, "max_tokens": 256 if probe else 4096}
        if (urlsplit(ai["baseUrl"]).hostname == "api.deepseek.com"
                and ai["model"] in {"deepseek-flash", "deepseek-v4-pro"}):
            # Educational hints need a usable final answer within the bounded
            # output budget. Apply the same non-thinking mode to Tutor, Coach,
            # and connection checks; never grant one evaluation condition a
            # different reasoning budget. Other compatible providers are unchanged.
            body["thinking"] = {"type": "disabled"}
        last_error = None
        for url in self._urls(ai["baseUrl"]):
            candidate = dict(body)
            for token_attempt in range(2):
                request = Request(url, data=json.dumps(candidate, ensure_ascii=False).encode("utf-8"), headers=headers)
                try:
                    with self.opener(request, timeout=self._remaining(deadline)) as response:
                        content = response.read(MAX_RESPONSE + 1)
                    if len(content) > MAX_RESPONSE:
                        raise WebAiError("invalid-format", "模型回复过大，未读取或展示正文。", status=200)
                    try:
                        result = json.loads(content)
                    except (ValueError, UnicodeError):
                        raise WebAiError("invalid-format", "模型接口未返回有效 JSON，请检查接口协议。", status=200) from None
                    return self._extract_compatible(result)
                except HTTPError as error:
                    status = error.code
                    try:
                        detail = error.read(8192).decode("utf-8", errors="replace")
                    except OSError:
                        detail = ""
                    finally:
                        error.close()
                    if status == 400 and token_attempt == 0 and re.search(r"max[_ -]?tokens", detail, re.I):
                        candidate.pop("max_tokens", None)
                        continue
                    last_error = _http_error(status)
                    if status not in (404, 405):
                        raise last_error from None
                    break
                except (TimeoutError, OSError, URLError) as error:
                    kind = "timeout" if isinstance(error, TimeoutError) or isinstance(getattr(error, "reason", None), TimeoutError) else "transport"
                    last_error = WebAiError(kind, "模型请求超时，请稍后重试。" if kind == "timeout" else "无法连接模型接口，请检查网络、接口地址与服务状态。")
                    break
        raise last_error or WebAiError("transport", "模型请求未完成。")

    @staticmethod
    def _extract_compatible(result) -> str:
        if not isinstance(result, dict):
            raise WebAiError("invalid-format", "模型回复不符合兼容聊天补全格式。", status=200)
        choices = result.get("choices")
        if (not isinstance(choices, list) or not choices or not isinstance(choices[0], dict)
                or not isinstance(choices[0].get("message"), dict)):
            raise WebAiError("invalid-format", "模型回复缺少兼容格式的 choices/message。", status=200)
        choice = choices[0]
        message = choice["message"]
        content = message.get("content")
        if choice.get("finish_reason") == "length":
            raise WebAiError("incomplete", "模型回复被输出额度截断，本次未使用不完整正文；请增加额度或调整模型。", status=200)
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            text = "".join(part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str))
            if text.strip():
                return text
        if content is not None and not isinstance(content, (str, list)):
            raise WebAiError("invalid-format", "模型正文格式无效。", status=200)
        if isinstance(message.get("reasoning_content"), str) and message["reasoning_content"].strip():
            raise WebAiError("incomplete", "模型接口已响应，但只返回了思考过程，尚未生成回答正文。", status=200)
        raise WebAiError("empty", "模型接口已响应，但没有返回回答正文。", status=200)

    def _complete_anthropic(self, ai, messages, deadline, probe):
        try:
            factory = self.anthropic_factory
            if factory is None:
                import anthropic
                factory = anthropic.Anthropic
            systems = [message["content"] for message in messages if message.get("role") == "system"]
            outgoing = [{"role": message["role"], "content": message["content"]}
                        for message in messages if message.get("role") != "system"]
            kwargs = {"model": ai["anthropicModel"], "max_tokens": 256 if probe else 4096, "messages": outgoing}
            if systems:
                kwargs["system"] = "\n\n".join(systems)
            with factory(api_key=ai["anthropicApiKey"], timeout=self._remaining(deadline), max_retries=0) as client:
                response = client.messages.create(**kwargs)
            if getattr(response, "stop_reason", None) == "max_tokens":
                raise WebAiError("incomplete", "Anthropic 回复被输出额度截断，本次未使用不完整正文。", status=200)
            answer = "".join(block.text for block in response.content if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str))
            if answer.strip():
                return answer
            incomplete = any(getattr(block, "type", None) == "thinking" for block in response.content)
            raise WebAiError("incomplete" if incomplete else "empty", "Anthropic 接口已响应，但尚未返回可用回答正文。", status=200)
        except WebAiError:
            raise
        except Exception as error:
            status = getattr(error, "status_code", None)
            if isinstance(status, int):
                raise _http_error(status) from None
            if isinstance(error, TimeoutError) or "Timeout" in type(error).__name__:
                raise WebAiError("timeout", "Anthropic 请求超时，请稍后重试。") from None
            raise WebAiError("transport", "Anthropic 请求未完成，请检查网络、模型和密钥配置。") from None

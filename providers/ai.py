"""AI yordamchi provayderlari.

Bepul: gemini, groq (karta shart emas, bepul limit bor).
Pulliga o'tish: AI_PROVIDER=openai_compat (AI_BASE_URL + AI_API_KEY + AI_MODEL) yoki anthropic.
Provayderni almashtirish uchun faqat .env o'zgartiriladi, kod emas.
Model nomlari tez-tez o'zgaradi, shuning uchun AI_MODEL ni rasmiy hujjatdan tekshiring.
"""
import logging

import requests

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a friendly language-learning assistant for a language center that teaches English, "
    "Russian and Korean. Explain clearly and briefly, adapt to the learner's level, give short "
    "examples, and answer in the language the student writes in. Help the student understand and "
    "practise; do not simply hand over full homework answers. You assist the teacher, you do not replace them."
)


class AIError(Exception):
    pass


class AIProvider:
    name = "none"
    free_tier = True

    def ask(self, prompt, system=SYSTEM_PROMPT):
        raise AIError("ai_disabled")


class NullProvider(AIProvider):
    name = "none"

    def ask(self, prompt, system=SYSTEM_PROMPT):
        raise AIError("ai_disabled")


def _post(url, headers, payload):
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=30)
    except requests.RequestException as exc:
        raise AIError("network: %s" % exc.__class__.__name__)
    if resp.status_code == 429:
        raise AIError("ai_quota")
    if resp.status_code >= 400:
        log.warning("AI xatolik %s: %s", resp.status_code, resp.text[:300])
        raise AIError("ai_failed")
    try:
        return resp.json()
    except ValueError:
        raise AIError("ai_failed")


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, key, model=None):
        self.key = key
        self.model = model or "gemini-2.0-flash"

    def ask(self, prompt, system=SYSTEM_PROMPT):
        url = "https://generativelanguage.googleapis.com/v1beta/models/%s:generateContent" % self.model
        data = _post(url, {"x-goog-api-key": self.key, "Content-Type": "application/json"}, {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        })
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("ai_failed")


class OpenAICompatProvider(AIProvider):
    """OpenAI formatidagi har qanday xizmat (Groq, OpenRouter, OpenAI, o'z serveringiz...)."""
    name = "openai_compat"

    def __init__(self, key, model, base_url):
        self.key = key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def ask(self, prompt, system=SYSTEM_PROMPT):
        data = _post(self.base_url + "/chat/completions",
                     {"Authorization": "Bearer %s" % self.key, "Content-Type": "application/json"},
                     {"model": self.model, "messages": [
                         {"role": "system", "content": system},
                         {"role": "user", "content": prompt}]})
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("ai_failed")


class GroqProvider(OpenAICompatProvider):
    name = "groq"

    def __init__(self, key, model=None):
        super().__init__(key, model or "llama-3.3-70b-versatile", "https://api.groq.com/openai/v1")


class AnthropicProvider(AIProvider):
    name = "anthropic"
    free_tier = False

    def __init__(self, key, model):
        self.key = key
        self.model = model

    def ask(self, prompt, system=SYSTEM_PROMPT):
        data = _post("https://api.anthropic.com/v1/messages",
                     {"x-api-key": self.key, "anthropic-version": "2023-06-01",
                      "content-type": "application/json"},
                     {"model": self.model, "max_tokens": 700, "system": system,
                      "messages": [{"role": "user", "content": prompt}]})
        try:
            return data["content"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            raise AIError("ai_failed")


def get_provider(config):
    name = config.get("AI_PROVIDER", "none")
    key = config.get("AI_API_KEY", "")
    model = config.get("AI_MODEL") or None
    if name == "none" or not key:
        return NullProvider()
    if name == "gemini":
        return GeminiProvider(key, model)
    if name == "groq":
        return GroqProvider(key, model)
    if name == "openai_compat":
        if not (model and config.get("AI_BASE_URL")):
            log.error("openai_compat uchun AI_MODEL va AI_BASE_URL kerak")
            return NullProvider()
        return OpenAICompatProvider(key, model, config["AI_BASE_URL"])
    if name == "anthropic":
        if not model:
            log.error("anthropic uchun AI_MODEL kerak")
            return NullProvider()
        return AnthropicProvider(key, model)
    log.error("Noma'lum AI_PROVIDER: %s", name)
    return NullProvider()

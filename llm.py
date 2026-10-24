"""Közös LLM-hívó réteg (Anthropic, OpenAI, Google, OpenAI-kompatibilis) a pipeline.py és a benchmark.py számára.

A modellek neve itt egy helyen van nyilvántartva (MODELS), az árakkal és a szolgáltató-specifikus paraméterekkel.
Telepítés: pip install anthropic openai google-genai (csak amelyik szolgáltatót használod).
"""
import os
import time

from env import load_env

load_env()

# name: egyedi név; provider: anthropic | openai | google | openai_compat
# price: (USD/M input, USD/M output) vagy None. extra: szolgáltató-specifikus paraméterek.
# Állapot: 2026. október 24., a szolgáltatók hivatalos árlistái és modelloldalai alapján. Árak: standard,
# rövid kontextus. Batch módban mindhárom cégnél kb. fele. "optional": csak --include-optional / --only esetén fut.
MODELS = [
    # --- Anthropic ---
    {"name": "haiku", "provider": "anthropic", "model": "claude-haiku-5-5", "price": (0.10, 0.50)},  # 100K tokenes prompt fölött drágább sáv
    {"name": "sonnet", "provider": "anthropic", "model": "claude-sonnet-5-5", "price": (2.00, 10.00)},
    # --- OpenAI ---
    # Haiku-osztály: a GPT-6 család "cost-sensitive, high-volume" tagja, ugyanaz az ár mint a Haikué.
    {"name": "gpt-6-luna", "provider": "openai", "model": "gpt-6-luna", "price": (0.10, 0.50),
     "extra": {"reasoning_effort": "low"}},
    # Sonnet-osztály: "near-Astra performance at lower cost", ugyanaz az ár mint a Sonnet-é.
    {"name": "gpt-6.1-sol", "provider": "openai", "model": "gpt-6.1-sol", "price": (2.00, 10.00),
     "extra": {"reasoning_effort": "low"}},
    # Plafon: a legerősebb OpenAI modell, 5x ár. Csak a minőség felső korlátjának felméréséhez.
    {"name": "gpt-6-astra", "provider": "openai", "model": "gpt-6-astra", "price": (10.00, 50.00),
     "extra": {"reasoning_effort": "low"}, "optional": True},
    # --- Google ---
    # Olcsó, gyors Flash-Lite. A gondolkodás nem kapcsolható ki, a legalacsonyabb szint a "minimal".
    {"name": "gemini-3.5-flash-lite", "provider": "google", "model": "gemini-3.5-flash-lite",
     "price": (0.30, 2.50), "extra": {"thinking_level": "minimal"}},
    # Haiku/Sonnet közötti: a "legintelligensebb Flash". Ár: $0.75/$3.75 2026.12.31-ig, utána $1.50/$7.50 (ez van itt).
    {"name": "gemini-3.8-flash", "provider": "google", "model": "gemini-3.8-flash", "price": (1.50, 7.50),
     "extra": {"thinking_level": "low"}},
    # Sonnet-osztály / zászlóshajó. Ár: $2/$10 2026.11.30-ig, utána $4/$20 (ez van itt).
    {"name": "gemini-4-argon", "provider": "google", "model": "gemini-4-argon", "price": (4.00, 20.00),
     "extra": {"thinking_level": "low"}},
    # Példa OpenAI-kompatibilis szolgáltatóra (OpenRouter, DeepSeek, Together, Groq, Mistral...):
    # {"name": "kimi", "provider": "openai_compat", "model": "moonshotai/kimi-k3.1-pro",
    #  "base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY", "price": [2.0, 10.0]},
]


def resolve(name_or_id, models=None):
    """Név (pl. "haiku") vagy modellazonosító (pl. "claude-haiku-5-5") alapján megadja a modell-leírást."""
    for m in (models or MODELS):
        if name_or_id in (m["name"], m["model"]):
            return m
    raise ValueError(f"Ismeretlen modell: {name_or_id}. Ismert nevek: {', '.join(m['name'] for m in MODELS)}")


class Result:
    def __init__(self, text, tin, tout, truncated, secs, error=None):
        self.text, self.tin, self.tout, self.truncated, self.secs, self.error = text, tin, tout, truncated, secs, error


_clients = {}


def _client(spec):
    key = (spec["provider"], spec.get("base_url"), spec.get("key_env"))
    if key in _clients:
        return _clients[key]
    prov = spec["provider"]
    if prov == "anthropic":
        import anthropic
        c = anthropic.AsyncAnthropic(max_retries=5)
    elif prov in ("openai", "openai_compat"):
        import openai
        env = spec.get("key_env")
        c = openai.AsyncOpenAI(api_key=os.environ.get(env) if env else None,
                               base_url=spec.get("base_url"), max_retries=5)
    elif prov == "google":
        from google import genai
        c = genai.Client(api_key=os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
    else:
        raise ValueError(f"ismeretlen provider: {prov}")
    _clients[key] = c
    return c


FRESH = {}  # modellnév -> [bemeneti token, kimeneti token, hívások]: csak az ebben a futásban ténylegesen kifizetett hívások


async def complete(spec, system, user, max_tokens) -> Result:
    """Egyetlen hívás, JSON-t várva. Hibát nem dob: Result.error-ban adja vissza."""
    r = await _complete(spec, system, user, max_tokens)
    f = FRESH.setdefault(spec["name"], [0, 0, 0])
    f[0] += r.tin
    f[1] += r.tout
    f[2] += 1
    return r


async def _complete(spec, system, user, max_tokens) -> Result:
    c, prov, extra = _client(spec), spec["provider"], spec.get("extra", {})
    t0 = time.time()
    try:
        if prov == "anthropic":
            r = await c.messages.create(model=spec["model"], max_tokens=max_tokens, system=system,
                                        messages=[{"role": "user", "content": user}], **extra)
            text = "".join(b.text for b in r.content if b.type == "text")
            return Result(text, r.usage.input_tokens, r.usage.output_tokens,
                          r.stop_reason == "max_tokens", time.time() - t0)
        if prov in ("openai", "openai_compat"):
            kw = dict(extra)
            kw["max_completion_tokens" if prov == "openai" else "max_tokens"] = max_tokens
            if prov == "openai":
                kw.setdefault("response_format", {"type": "json_object"})
            r = await c.chat.completions.create(
                model=spec["model"], messages=[{"role": "system", "content": system},
                                               {"role": "user", "content": user}], **kw)
            ch = r.choices[0]
            return Result(ch.message.content or "", r.usage.prompt_tokens, r.usage.completion_tokens,
                          ch.finish_reason == "length", time.time() - t0)
        if prov == "google":
            from google.genai import types
            kw = {}
            if "thinking_level" in extra:  # a Gemini 3.x/4 modelleknél a thinking_budget helyett ez él
                kw["thinking_config"] = types.ThinkingConfig(thinking_level=extra["thinking_level"].upper())
            cfg = types.GenerateContentConfig(system_instruction=system, max_output_tokens=max_tokens,
                                              response_mime_type="application/json", **kw)
            r = await c.aio.models.generate_content(model=spec["model"], contents=user, config=cfg)
            u = r.usage_metadata
            tout = (u.candidates_token_count or 0) + (getattr(u, "thoughts_token_count", 0) or 0)
            fin = str(r.candidates[0].finish_reason) if r.candidates else ""
            return Result(r.text or "", u.prompt_token_count or 0, tout, "MAX_TOKENS" in fin, time.time() - t0)
    except Exception as e:  # noqa: BLE001 - a hívó dönt arról, mi legyen a hibával
        return Result("", 0, 0, False, time.time() - t0, error=f"{type(e).__name__}: {str(e)[:200]}")
    raise ValueError(prov)


def cost(spec, tin, tout):
    p = spec.get("price")
    return None if not p else tin / 1e6 * p[0] + tout / 1e6 * p[1]

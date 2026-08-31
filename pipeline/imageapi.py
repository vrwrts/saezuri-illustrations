#!/usr/bin/env python3
# SPDX-License-Identifier: CC-BY-NC-SA-4.0
"""Saezuri - OpenAI-compatible chat/completions client for the pipeline's model calls.

Original Saezuri code (not ported from AvianVisitors).

Every provider-specific detail lives here, so choosing a different model - or
running one on your own hardware - is a config change rather than a code change.
The default endpoint is OpenRouter, which fronts most image-output models behind
one account; any other OpenAI-compatible server (LM Studio, vLLM, llama.cpp)
works by pointing GENERATE_API_URL at it.

chat/completions rather than a dedicated images endpoint because this pipeline
sends CAPTIONED references - "IMAGE 1 (positive, target species)", "IMAGE 2
(negative, do NOT copy)" - and only a messages array preserves that interleaving
of text and images. A flat prompt + reference list would drop the captions the
anti-reference prompt depends on.

Standard library only, like the rest of the pipeline: no SDK to install.
"""
from __future__ import annotations
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_API_URL = "https://openrouter.ai/api/v1"
#: Image-output default. The same model the pipeline used when it talked to
#: Google directly, so the default look does not change with this transport.
DEFAULT_IMAGE_MODEL = "google/gemini-2.5-flash-image"
#: Text-output default, for verify.py's blind species check.
DEFAULT_VISION_MODEL = "google/gemini-2.5-flash"

ENV_API_KEY = "GENERATE_API_KEY"
ENV_API_URL = "GENERATE_API_URL"
ENV_MODEL = "GENERATE_MODEL"
#: Removed in favour of ENV_API_KEY. Still read, only to fail with a message
#: that says what to do - an unset key otherwise just turns generation off.
LEGACY_ENV_API_KEY = "GEMINI_API_KEY"

RETRY_STATUS = (429, 500, 502, 503, 504)
ATTEMPTS = 4
INITIAL_BACKOFF_S = 4.0
#: Image generation is slow; a minute is not unusual for a single render.
IMAGE_TIMEOUT_S = 180


class ConfigError(Exception):
    """Bad or missing endpoint configuration. Callers print it and exit 2."""


def resolve(
    api_key: str | None,
    api_url: str | None,
    model: str | None,
    default_model: str,
) -> tuple[str, str, str]:
    """Settle (api_url, api_key, model) from flags, then environment, then the
    defaults above. Raises ConfigError when there is no key.

    A caller that always passes `model` (verify.py, whose argparse default is
    DEFAULT_VISION_MODEL) never consults ENV_MODEL - which is what keeps one
    environment variable from having to name both an image and a vision model.
    """
    key = (api_key or os.environ.get(ENV_API_KEY, "")).strip()
    if not key:
        if os.environ.get(LEGACY_ENV_API_KEY, "").strip():
            raise ConfigError(
                f"{LEGACY_ENV_API_KEY} is no longer used. Model calls now go through an "
                f"OpenAI-compatible endpoint, so set {ENV_API_KEY} instead. Note that a "
                f"Google AI key will NOT authenticate against the default endpoint "
                f"({DEFAULT_API_URL}) - get an OpenRouter key, or set {ENV_API_URL} to a "
                f"server that accepts the key you have. {ENV_MODEL} selects the model "
                f"(default {default_model})."
            )
        raise ConfigError(f"{ENV_API_KEY} required (--api-key or env)")
    url = (api_url or os.environ.get(ENV_API_URL, "") or DEFAULT_API_URL).strip().rstrip("/")
    mdl = (model or os.environ.get(ENV_MODEL, "") or default_model).strip()
    return url, key, mdl


# ---- request parts ----

def text_part(text: str) -> dict:
    return {"type": "text", "text": text}


def image_part(data: bytes, mime: str) -> dict:
    b64 = base64.b64encode(data).decode()
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}}


def mime_for(p: Path) -> str:
    ext = p.suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    # image/jpeg rather than application/octet-stream for the unknown case: an
    # unrecognised type still has to make a *valid* image data URL, and every
    # reference this pipeline handles is jpg, png, or webp anyway.
    return "image/jpeg"


# ---- the call ----

def chat(
    api_url: str,
    api_key: str,
    model: str,
    parts: list[dict],
    *,
    want_image: bool = False,
    timeout: int = IMAGE_TIMEOUT_S,
    label: str = "",
) -> dict:
    """One chat/completions call with bounded retry on 429 + transient 5xx.
    Returns the decoded response body; use first_image/first_text to read it.

    label prefixes retry chatter so a long batch says which render is stalling.
    """
    payload: dict = {"model": model, "messages": [{"role": "user", "content": parts}]}
    if want_image:
        # TEXT alongside IMAGE so a model can surface a refusal or safety note
        # without rejecting the request shape - some error on image-only.
        payload["modalities"] = ["image", "text"]
    req = urllib.request.Request(
        f"{api_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            # Bearer, not a query parameter: keeps the key out of request logs,
            # proxy logs, and shell history.
            "Authorization": f"Bearer {api_key}",
            # OpenRouter attributes usage to the app by this header; ignored
            # everywhere else.
            "X-Title": "Saezuri",
        },
        method="POST",
    )

    tag = f"{label}: " if label else ""
    backoff = INITIAL_BACKOFF_S
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            # Read the body once for a human-readable reason: the useful detail
            # (quota, model name, billing) is there, not in the status line.
            detail = error_detail(e)
            if e.code in RETRY_STATUS and attempt < ATTEMPTS - 1:
                ra = e.headers.get("Retry-After")
                try:
                    wait = float(ra) if ra else backoff
                except (TypeError, ValueError):
                    wait = backoff  # HTTP-date format, fall back
                print(f"    [retry] {tag}HTTP {e.code} {detail} - waiting {wait:.0f}s "
                      f"(attempt {attempt + 1}/{ATTEMPTS})", file=sys.stderr)
                time.sleep(wait)
                backoff *= 2
                continue
            raise RuntimeError(f"HTTP {e.code}: {detail}")
        except urllib.error.URLError as e:
            if attempt < ATTEMPTS - 1:
                print(f"    [retry] {tag}{e.reason} - waiting {backoff:.0f}s "
                      f"(attempt {attempt + 1}/{ATTEMPTS})", file=sys.stderr)
                time.sleep(backoff)
                backoff *= 2
                continue
            raise
    raise RuntimeError("retries exhausted")


def error_detail(e: urllib.error.HTTPError) -> str:
    """Best-effort concise reason from an error body - OpenAI-compatible servers
    put it in `error.message`. Falls back to a raw snippet, then the HTTP reason,
    for a server that returns HTML or a bare status. Reads the body, so call at
    most once per exception."""
    try:
        body = e.read().decode("utf-8", "replace")
    except Exception:
        return e.reason or str(e.code)
    try:
        err = json.loads(body)["error"]
    except (ValueError, KeyError, TypeError):
        return " ".join(body.split())[:200] or e.reason or str(e.code)
    if isinstance(err, str):
        return err
    msg = (err.get("message") or "").split("\n", 1)[0].strip()
    code = err.get("code")
    if code and str(code) not in msg:
        msg = f"{msg} [{code}]".strip()
    return msg or e.reason or str(e.code)


# ---- reading the response ----

def first_image(resp: dict) -> bytes:
    """Raw image bytes from a chat/completions response.

    Two shapes, because they are both real: OpenRouter returns generated images
    in `message.images`, while some OpenAI-compatible servers put them among the
    content parts instead."""
    msg = _message(resp)
    for item in msg.get("images") or []:
        data = _image_bytes(item)
        if data:
            return data
    content = msg.get("content")
    if isinstance(content, list):
        for item in content:
            data = _image_bytes(item)
            if data:
                return data
    raise RuntimeError(f"no image ({_no_output_reason(resp)})")


def first_text(resp: dict) -> str:
    """Assistant text from a chat/completions response. `content` is a plain
    string on most servers and a parts list on some, so both are joined."""
    content = _message(resp).get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        chunks = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(c for c in chunks if c).strip()
    return ""


def _message(resp: dict) -> dict:
    choices = resp.get("choices") or [{}]
    return (choices[0] or {}).get("message") or {}


def _image_bytes(item: object) -> bytes | None:
    """Decode one `{"image_url": {"url": ...}}` part. Handles a base64 data URL
    (what OpenRouter documents) and an http(s) URL, which some servers return
    instead - the endpoint is operator-configured, so fetching from it is no more
    trust than the call itself."""
    if not isinstance(item, dict):
        return None
    url = ((item.get("image_url") or {}) if isinstance(item.get("image_url"), dict) else {}).get("url")
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("data:"):
        _, _, payload = url.partition(",")
        try:
            return base64.b64decode(payload)
        except (ValueError, TypeError):
            return None
    if url.startswith("http://") or url.startswith("https://"):
        with urllib.request.urlopen(url, timeout=60) as r:
            return r.read()
    return None


def _no_output_reason(resp: dict) -> str:
    """Why a 200 came back with nothing usable. Servers report this three
    different ways, and a refusal is the common case, so surface all of them."""
    choice = (resp.get("choices") or [{}])[0] or {}
    bits = [f"finish={choice.get('finish_reason', '?')}"]
    err = resp.get("error")
    if isinstance(err, dict) and err.get("message"):
        bits.append(str(err["message"]).split("\n", 1)[0])
    elif isinstance(err, str) and err:
        bits.append(err)
    msg = choice.get("message") or {}
    if isinstance(msg.get("refusal"), str) and msg["refusal"]:
        bits.append(f"refusal: {msg['refusal']}")
    elif isinstance(msg.get("content"), str) and msg["content"].strip():
        bits.append(f"text: {msg['content'].strip()[:120]}")
    return " ".join(bits)

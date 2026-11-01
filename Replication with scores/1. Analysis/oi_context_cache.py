# -*- coding: utf-8 -*-
"""One explicit Google context cache at a time for repeated example banks.

The caller supplies the same configured client used for scoring. This module
never switches endpoints or models and never falls back to full-price prompts.
No network request is made until ``ensure`` is called for a pending prediction.

Official API references checked 2026-10-22:
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-create
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-update
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-use
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any

from oi_request_io import append_record, digest, error_code, request_progress


class ContextCacheError(RuntimeError):
    """A cache operation failed; accepted scoring replies are unaffected."""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


def _get(value: Any, name: str, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def _utc(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return fallback
    if not isinstance(value, datetime):
        return fallback
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class ContextCacheManager:
    """Lazily create, renew, and delete a cache for one example bank/fold.

    Call ``ensure`` before each pending prediction and use the returned resource
    name as GenerateContentConfig.cached_content. Send only the target suffix;
    do not repeat system_instruction, tools, or tool_config in that generation
    request. ``metadata`` provides the cache details for the scoring audit log.

    The caller must call ``close`` before closing the Google client, preferably
    in a finally block. A killed process cannot run cleanup; the finite TTL then
    bounds further storage. Cache lifecycle events are appended, never replaced.
    """

    def __init__(self, client, types, model: str, project_endpoint: str,
                 events_path: Path, ttl_seconds: int = 3600,
                 heartbeat_seconds: int = 30):
        if type(ttl_seconds) is not int or ttl_seconds < 60:
            raise ValueError("Cache TTL must be an integer of at least 60 seconds.")
        if heartbeat_seconds <= 0:
            raise ValueError("Cache heartbeat must be positive.")
        self.client = client
        self.types = types
        self.model = model
        self.endpoint = project_endpoint
        self.events_path = Path(events_path)
        self.ttl_seconds = ttl_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self._active: dict | None = None
        self._seen_fingerprints: dict[str, str] = {}
        self._renew_margin = min(600, max(15, ttl_seconds / 5))

    @property
    def metadata(self) -> dict:
        """Copy of currently owned cache metadata; no raw example text."""
        return dict(self._active or {})

    def _event(self, event: str, **details) -> None:
        record = {"timestamp_utc": datetime.now(timezone.utc).isoformat(),
                  "event": event, "model": self.model, "endpoint": self.endpoint,
                  "ttl_seconds": self.ttl_seconds, **details}
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            append_record(stream, record)

    def _details(self, response, started: datetime, previous: dict | None = None) -> dict:
        previous = previous or {}
        usage = _get(response, "usage_metadata")
        tokens = _get(usage, "total_token_count", previous.get("cache_tokens"))
        if tokens is not None:
            tokens = int(tokens)
        return {
            "cache_name": _get(response, "name") or previous.get("cache_name"),
            "cache_tokens": tokens,
            "cache_created_utc": _utc(_get(response, "create_time"),
                                      _utc(previous.get("cache_created_utc"), started)).isoformat(),
            "cache_expires_utc": _utc(_get(response, "expire_time"),
                                      started + timedelta(seconds=self.ttl_seconds)).isoformat(),
        }

    def ensure(self, cache_key: str, prefix: str, system_instruction: str) -> str:
        """Return a usable cache name, creating/renewing it only when necessary."""
        if not cache_key or not isinstance(prefix, str) or not prefix:
            raise ValueError("A nonempty cache key and text prefix are required.")
        fingerprint = digest({"prefix": prefix, "system": system_instruction,
                              "model": self.model, "endpoint": self.endpoint})
        if cache_key in self._seen_fingerprints and self._seen_fingerprints[cache_key] != fingerprint:
            raise ContextCacheError("A cache key was reused for different prompt content; scoring stopped.")
        self._seen_fingerprints[cache_key] = fingerprint
        if self._active and self._active["cache_key"] != cache_key:
            self._delete(strict=True)
        if self._active:
            now = datetime.now(timezone.utc)
            expires = _utc(self._active["cache_expires_utc"], now)
            if expires <= now:
                self.invalidate("expired_locally")
            elif (expires - now).total_seconds() <= self._renew_margin:
                self._renew()
        if self._active is None:
            self._create(cache_key, fingerprint, prefix, system_instruction)
        return self._active["cache_name"]

    def _create(self, cache_key: str, fingerprint: str, prefix: str, system: str) -> None:
        started, clock = datetime.now(timezone.utc), time.monotonic()
        context = {"cache_key": cache_key, "prefix_fingerprint": fingerprint}
        self._event("create_started", **context)
        try:
            config = self.types.CreateCachedContentConfig(
                contents=[self.types.Content(role="user", parts=[self.types.Part(text=prefix)])],
                system_instruction=system,
                display_name="oi-repeated-" + fingerprint[:24],
                ttl=f"{self.ttl_seconds}s",
            )
            with request_progress("Creating example-bank cache", self.heartbeat_seconds):
                result = self.client.caches.create(model=self.model, config=config)
        except Exception as exc:
            self._event("create_failed", **context, elapsed_seconds=time.monotonic() - clock,
                        error_type=type(exc).__name__, http_code=error_code(exc), error=str(exc))
            raise ContextCacheError(
                "Google could not create the example-bank cache. Accepted replies are retained. "
                "No uncached scoring request was sent. Check the error below for model/region "
                "support, the minimum cache size, permissions, or temporary capacity. "
                "A timed-out creation may remain on Google until its TTL expires.\n" + str(exc),
                error_code(exc),
            ) from exc
        # Own the remote name before logging/processing metadata, so a local
        # write error cannot strand a known cache without a cleanup attempt.
        name = _get(result, "name")
        if not isinstance(name, str) or not name:
            self._event("create_invalid_response", **context,
                        elapsed_seconds=time.monotonic() - clock)
            raise ContextCacheError("Google returned no cache resource name; scoring stopped.")
        self._active = {**context, "cache_name": name, "cache_tokens": None,
                        "cache_created_utc": started.isoformat(),
                        "cache_expires_utc": (started + timedelta(seconds=self.ttl_seconds)).isoformat()}
        try:
            self._active.update(self._details(result, started))
            self._event("created", **self._active, elapsed_seconds=time.monotonic() - clock)
        except BaseException:
            self.close()
            raise
        print(f"Example-bank cache ready: {self._active['cache_tokens']} cached tokens; "
              "reused for this bank's pending reviews.", flush=True)

    def _renew(self) -> None:
        started, clock = datetime.now(timezone.utc), time.monotonic()
        before = dict(self._active)
        self._event("renew_started", **before)
        try:
            with request_progress("Renewing example-bank cache", self.heartbeat_seconds):
                result = self.client.caches.update(
                    name=before["cache_name"],
                    config=self.types.UpdateCachedContentConfig(ttl=f"{self.ttl_seconds}s"),
                )
        except Exception as exc:
            self._event("renew_failed", **before, elapsed_seconds=time.monotonic() - clock,
                        error_type=type(exc).__name__, http_code=error_code(exc), error=str(exc))
            if error_code(exc) == 404:
                self.invalidate("renew_not_found")
                return
            raise ContextCacheError("Google could not renew the example-bank cache. "
                                    "Accepted replies are retained.\n" + str(exc), error_code(exc)) from exc
        self._active.update(self._details(result, started, before))
        self._event("renewed", **self._active, elapsed_seconds=time.monotonic() - clock)
        print("Example-bank cache renewed for the next requests.", flush=True)

    def invalidate(self, reason: str = "generation_not_found", cache_name: str | None = None) -> None:
        """Forget an expired/404 cache; the next ensure recreates its contents.

        Use only when Google reports the cache absent/expired, or after local
        expiry. This is not a replacement for close() on a still-live cache.
        """
        if self._active is None:
            return
        if cache_name is not None and cache_name != self._active["cache_name"]:
            return
        before = dict(self._active)
        self._event("invalidated", **before, reason=reason)
        self._active = None

    def _delete(self, strict: bool) -> None:
        if self._active is None:
            return
        before, clock = dict(self._active), time.monotonic()
        log_error = None
        try:
            self._event("delete_started", **before)
        except Exception as exc:
            # A full disk must not prevent cleanup of a known remote cache.
            log_error = exc
        try:
            with request_progress("Deleting finished example-bank cache", self.heartbeat_seconds):
                self.client.caches.delete(name=before["cache_name"])
        except Exception as exc:
            if error_code(exc) == 404:
                self._active = None
                self._event("already_absent", **before, elapsed_seconds=time.monotonic() - clock)
                return
            try:
                self._event("delete_failed", **before, elapsed_seconds=time.monotonic() - clock,
                            error_type=type(exc).__name__, http_code=error_code(exc), error=str(exc))
            except Exception:
                pass
            message = ("Google cache cleanup failed; the cache can remain billable until "
                       f"{before['cache_expires_utc']}. Accepted replies are retained. " + str(exc))
            if strict:
                raise ContextCacheError(message, error_code(exc)) from exc
            print(message, flush=True)
            return
        self._active = None
        self._event("deleted", **before, elapsed_seconds=time.monotonic() - clock)
        if log_error is not None and strict:
            raise log_error

    def close(self) -> None:
        """Best-effort final cleanup, without hiding a scoring exception."""
        try:
            self._delete(strict=False)
        except Exception as exc:
            print("Cache cleanup could not be fully recorded. Any remaining cache expires "
                  f"under its finite TTL. Details: {exc}", flush=True)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

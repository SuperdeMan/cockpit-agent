"""Request-local projections over existing owners, never a second fact store."""
from __future__ import annotations

import asyncio
import copy
import time
import weakref
from contextvars import ContextVar

from runtime import context_access as access, memory_read


class ContextChanged(RuntimeError):
    """A fixed, non-sensitive reason; callers must discard the late result."""


CURRENT_VIEW: ContextVar["RequestView | None"] = ContextVar("context_view", default=None)


def identity(ctx):
    return tuple(str(getattr(ctx, key, "") or "") for key in (
        "user_id", "session_id", "vehicle_id", "occupant_id", "request_id"))


class RequestView:
    def __init__(self, ctx, vehicle_reader=None, *, wall=None, monotonic=None):
        self.ctx = ctx
        self.wall = wall or time.time
        self.monotonic = monotonic or time.monotonic
        self.owner = identity(ctx)
        self.granted = access.grants(ctx)
        self.memory_on = (getattr(ctx, "prefs", None) or {}).get("memory_enabled", "true") != "false"
        self.vehicle_reader = vehicle_reader
        self.closed = False
        self.deadline = None
        try:
            self.task = asyncio.current_task()
        except RuntimeError:
            self.task = None

    def check(self):
        current = access.grants(self.ctx)
        memory_on = (getattr(self.ctx, "prefs", None) or {}).get("memory_enabled", "true") != "false"
        if (self.closed or identity(self.ctx) != self.owner
                or current != self.granted
                or (self.memory_on and not memory_on)
                or (self.deadline is not None and self.monotonic() >= self.deadline)):
            self.closed = True
            raise ContextChanged("context_view_invalidated")

    def invalidate(self, *, cancel=False):
        self.closed = True
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if cancel and self.task is not None and self.task is not current and not self.task.done():
            self.task.cancel()

    def remaining(self, timeout):
        self.check()
        return timeout if self.deadline is None else min(timeout, max(0.001, self.deadline - self.monotonic()))

    def vehicle(self, *, consume=False, keys=None):
        self.check()
        if not access.allowed(self.ctx, "vehicle_state"):
            return {}, {"state": memory_read.OFF, "access": "denied", "source": "vehicle_state"}
        try:
            observed = self.vehicle_reader(self.owner[2]) if self.vehicle_reader else {}
        except Exception:
            observed = {}
        if not isinstance(observed, dict) or observed.get("version") != 2 or observed.get("vehicle_id") != self.owner[2]:
            observed = {}
        # Work with the shared store's current view. Never refresh a stale value
        # from another signal, or manufacture a value from client metadata.
        signals = {key: signal for key, signal in (observed.get("signals") or {}).items()
                   if key != "location" or access.allowed(self.ctx, "location")}
        values = {key: value for key, value in (observed.get("state") or {}).items()
                  if signals.get(key, {}).get("quality") == "good"
                  and signals.get(key, {}).get("freshness") == "bounded"
                  and isinstance(signals.get(key, {}).get("expires_at_ms"), (int, float))
                  and signals[key]["expires_at_ms"] > self.wall() * 1000
                  and (keys is None or key in keys)}
        if consume and values:
            for key in values:
                expires = signals[key].get("expires_at_ms")
                if isinstance(expires, (int, float)):
                    deadline = self.monotonic() + max(0, expires / 1000 - self.wall())
                    self.deadline = deadline if self.deadline is None else min(self.deadline, deadline)
        return values, {"state": memory_read.FOUND if values else memory_read.UNAVAILABLE,
                        "access": "allowed", "source": "vehicle_state",
                        "vehicle_id": self.owner[2], "signals": copy.deepcopy(signals)}


def check_current():
    view = CURRENT_VIEW.get()
    if view is not None:
        view.check()
    return view


def check_context(ctx):
    check_current()
    view = getattr(ctx, "context_view", None)
    if not isinstance(view, RequestView):
        return None
    view.check()
    return view


class ActiveViews:
    """Only active request receipts, for the existing privacy-delete responder."""
    def __init__(self):
        self.views = weakref.WeakSet()

    def bind(self, ctx, vehicle_reader=None):
        view = RequestView(ctx, vehicle_reader)
        for old in list(self.views):
            if old.closed or old.owner[:2] != view.owner[:2]:
                continue
            if old.owner[2:4] != view.owner[2:4] or old.granted != view.granted or old.memory_on != view.memory_on:
                old.invalidate(cancel=True)
        self.views.add(view)
        ctx.context_view = view
        return view

    def invalidate_owner(self, user_id):
        for view in list(self.views):
            if view.owner[0] == user_id:
                view.invalidate(cancel=True)


def project_focus(focus, ctx):
    if focus is None:
        return None
    out = copy.deepcopy(focus)
    if not access.allowed(ctx, "profile"):
        # Opaque historical fields cannot be safely redacted by keyword.
        out = type(focus)()
        # Retain only a generic enforcement warning, never private signal text.
        alert = getattr(focus, "safety_alert", None) or {}
        if alert:
            out.safety_alert = {key: alert[key] for key in ("level", "ts") if key in alert}
    elif not access.allowed(ctx, "location"):
        for key, empty in {
            "last_poi": "", "last_destination": "", "last_city": "",
            "destination_lat": None, "destination_lng": None, "last_places": [],
            "last_choices": [], "last_choice_purpose": "", "candidate_sets": [],
            "retired_candidate_sets": [], "active_route": {}, "active_task": {},
        }.items():
            setattr(out, key, empty)
    out.by_occupant = {}
    return out


def project_working_set(working_set):
    view = working_set.context_view
    if view is None:
        return working_set  # Formatting-only legacy use; model entry binds a view.
    view.check()
    out = copy.copy(working_set)
    if not access.allowed(view.ctx, "profile"):
        out.history = []
        out.memories = []
        out.history_state = out.memory_state = memory_read.OFF
    out.focus = project_focus(working_set.focus, view.ctx)
    return out

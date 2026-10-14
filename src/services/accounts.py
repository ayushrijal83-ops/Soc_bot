"""Account service: token-free account views and the few account actions the UI offers."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.auth.base import is_token_expiring
from src.core.publisher import granted_scopes
from src.platforms.base import redact

PLATFORMS = ("instagram", "youtube", "tiktok")


@dataclass
class AccountView:
    id: int
    platform: str
    label: str
    username: str
    status: str           # ready | attention | disconnected
    status_text: str      # human text, never a secret
    expires_at: datetime | None
    group_id: int | None = None


@dataclass
class ConnectResult:
    ok: bool
    message: str                          # friendly, safe to show
    account_label: str | None = None
    details: str | None = None            # redacted technical detail (on request)
    missing_scopes: list[str] = field(default_factory=list)


@dataclass
class DeleteResult:
    ok: bool
    message: str          # friendly, safe to show
    stopped_jobs: int = 0  # not-yet-started jobs that will now never be claimed


def require_worker_thread() -> None:
    """OAuth/revoke run their own event loop (asyncio.run). They must never run on a thread whose loop is
    already running (e.g. the TUI's): call these methods from a worker thread."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("Call this from a worker thread, not from a running event loop")


@dataclass
class PlatformSummary:
    platform: str
    connected: int
    ready: int
    configured: bool      # OAuth app credentials present


class AccountService:
    def __init__(self, account_manager, auth_manager=None, engine=None):
        self.accounts = account_manager
        self.auth = auth_manager
        self.engine = engine

    def list(self, platform: str | None = None) -> list[AccountView]:
        views = []
        for account in self.accounts.list_accounts():
            if platform and account.platform != platform:
                continue
            views.append(self._view(account))
        return sorted(views, key=lambda v: (v.platform, v.label.lower()))

    def summary(self) -> list[PlatformSummary]:
        views = self.list()
        return [PlatformSummary(p, sum(v.platform == p and v.status != "disconnected" for v in views),
                                sum(v.platform == p and v.status == "ready" for v in views), self.is_configured(p))
                for p in PLATFORMS]

    def is_configured(self, platform: str) -> bool:
        try:
            return bool(self.auth and self.auth.is_configured(platform))
        except Exception:  # noqa: BLE001 - a status display must not crash
            return False

    def connect(self, platform: str, redirect_prompt: Callable[[str], str | None] | None = None,
                force_reauth: bool = False) -> ConnectResult:
        """Run the existing OAuth flow (AuthManager.connect_account, the single implementation) to the end.

        Blocking: call it from a worker thread. ``redirect_prompt`` answers the paste-mode question for
        platforms with a registered non-loopback redirect (Instagram); the CLI passes its terminal prompt.
        ``force_reauth`` (Instagram): "Add a different account" -- Instagram asks for credentials.
        """
        require_worker_thread()
        name = {"instagram": "Instagram", "youtube": "YouTube", "tiktok": "TikTok"}.get(platform, platform)
        if not self.is_configured(platform):
            return ConnectResult(False, f"{name} is not set up: add the app credentials to .env, then restart.")
        previous = self.auth.redirect_prompt
        self.auth.redirect_prompt = redirect_prompt
        try:
            result = asyncio.run(self.auth.connect_account(platform, force_reauth=force_reauth))  # own loop, in THIS (worker) thread
        except TimeoutError as e:
            return ConnectResult(False, f"{name} connection failed: no answer from the browser in time.",
                                 details=redact(str(e)))
        except Exception as e:  # noqa: BLE001 - UI boundary: friendly message + technical details on request
            return ConnectResult(False, f"{name} connection failed.", details=f"{type(e).__name__}: {redact(str(e))}")
        finally:
            self.auth.redirect_prompt = previous
        if not result.get("success"):
            return ConnectResult(False, f"{name} connection failed.", details=redact(str(result.get("error", ""))))
        account = result.get("account", {})
        from src.cli.account_menu import _missing_publish_scopes

        missing = _missing_publish_scopes(platform, account.get("scopes") or [])
        label = account.get("display_name") or account.get("username") or "account"
        return ConnectResult(True, f"{name} account connected: @{label}", label, missing_scopes=missing)

    def disconnect(self, account_id: int) -> bool:
        """Revoke where the platform supports it and mark the account disconnected (existing behavior).

        Revocation runs its own event loop: call from a worker thread."""
        require_worker_thread()
        if self.auth is not None:
            return bool(self.auth.disconnect_account(account_id))
        self.accounts.disconnect_account(account_id)
        return True

    def enable(self, account_id: int) -> None:
        self.accounts.enable_account(account_id)

    def delete(self, account_id: int) -> DeleteResult:
        """Permanently delete an account. Blocks (refuses, changes nothing) if any of its jobs is
        currently claimed (uploading/processing): that job's outcome at the platform is unknown,
        so deletion never pretends it could be cancelled. Otherwise: best-effort token revocation
        (existing AuthManager flow, same as disconnect), stops every not-yet-started job so it
        can never be claimed later, then permanently scrubs the account's credentials.

        Revocation runs its own event loop: call from a worker thread, like disconnect."""
        from src.accounts.manager import AccountAlreadyDeletedError, AccountNotFoundError

        require_worker_thread()
        try:
            self.accounts.get_account(account_id)
        except AccountNotFoundError as e:
            return DeleteResult(False, str(e))

        jobs = self.engine.store.jobs_for_account(account_id) if self.engine is not None else []
        in_flight = [j for j in jobs if j.status in ("uploading", "processing")]
        if in_flight:
            return DeleteResult(False, f"Cannot delete: {len(in_flight)} job(s) for this account "
                                "are currently in progress; their outcome at the platform is not yet "
                                "known, so this is never cancelled automatically. Wait until they "
                                "finish (check the Queue), then try again.")

        if self.auth is not None:
            try:
                self.auth.disconnect_account(account_id)  # best-effort revoke; deletion proceeds regardless
            except Exception:  # noqa: BLE001 - revocation is best-effort, same as plain disconnect
                pass

        stopped = 0
        if self.engine is not None:
            for job in jobs:
                if job.status in ("pending", "retrying") and not job.membership_removed_at:
                    if self.engine.store.remove_membership(job.id):
                        stopped += 1

        try:
            self.accounts.delete_account(account_id)
        except AccountAlreadyDeletedError as e:
            return DeleteResult(False, str(e))
        return DeleteResult(True, "Account permanently deleted. Publishing history was preserved.", stopped)

    def _view(self, account) -> AccountView:
        label = account.display_name or account.username or f"account {account.id}"
        if account.status != "active":
            status, text = "disconnected", account.status.title()
        else:
            status, text = "ready", "Ready"
            adapter = self.engine.publishers.get(account.platform) if self.engine is not None else None
            missing = adapter.missing_scopes(granted_scopes(account)) if adapter is not None else []
            expires = account.expires_at
            if expires is not None and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if missing:
                status, text = "attention", "Missing publish permission: reconnect"
            elif is_token_expiring(account.expires_at, 0) or (
                    expires is not None and (expires - datetime.now(timezone.utc)).days < 7):
                # The engine renews access tokens before publishing (refresh token / long-lived refresh).
                text = "Ready (login renewed automatically when publishing)"
        return AccountView(account.id, account.platform, label, account.username or "", status, text,
                           account.expires_at, account.group_id)

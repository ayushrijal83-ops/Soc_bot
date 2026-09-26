"""Application service layer: what any user interface (TUI, CLI) consumes.

    UI ─► services (plan / create / run batches, accounts, links, content, settings)
       ─► PublisherEngine / JobStore / AccountManager / PublishedLinks (frozen engine)

The UI never sees tokens, temporary media URLs, tunnels, Instagram containers or worker pools.
"""

from dataclasses import dataclass
from pathlib import Path

from src.services.accounts import (
    AccountService,
    AccountView,
    ConnectResult,
    PlatformSummary,
)
from src.services.library import (
    ContentService,
    LinkService,
    MediaStatus,
    SettingsService,
)
from src.services.publishing import (
    BatchEvent,
    BatchPlan,
    BatchView,
    CoverInfo,
    DestinationView,
    JobView,
    PublishingService,
    VideoInfo,
    friendly_error,
)


@dataclass
class AppServices:
    publishing: PublishingService
    accounts: AccountService
    links: LinkService
    content: ContentService
    settings: SettingsService
    account_manager: object
    auth_manager: object
    engine: object
    intake: object


def build_services(account_manager, auth_manager, engine, intake, env_file: Path) -> AppServices:
    return AppServices(
        publishing=PublishingService(engine, account_manager),
        accounts=AccountService(account_manager, auth_manager, engine),
        links=LinkService(engine.links),
        content=ContentService(intake),
        settings=SettingsService(env_file, auth_manager),
        account_manager=account_manager,
        auth_manager=auth_manager,
        engine=engine,
        intake=intake,
    )


__all__ = [
    "AccountService",
    "AccountView",
    "AppServices",
    "BatchEvent",
    "BatchPlan",
    "BatchView",
    "ConnectResult",
    "ContentService",
    "CoverInfo",
    "DestinationView",
    "JobView",
    "LinkService",
    "MediaStatus",
    "PlatformSummary",
    "PublishingService",
    "SettingsService",
    "VideoInfo",
    "build_services",
    "friendly_error",
]

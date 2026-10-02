"""Application service layer: what any user interface (TUI, CLI) consumes.

    UI ─► services (plan / create / run batches, accounts, links, content, settings)
       ─► PublisherEngine / JobStore / AccountManager / PublishedLinks (frozen engine)

The UI never sees tokens, temporary media URLs, tunnels, Instagram containers or worker pools.
"""

from dataclasses import dataclass
from pathlib import Path

from src.content.audience import AudienceStore
from src.services.accounts import (
    AccountService,
    AccountView,
    ConnectResult,
    PlatformSummary,
)
from src.services.due_scheduler import DueEvent, DueRunResult, DueScheduler
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
from src.services.scheduling import ScheduleView, SchedulingError, SchedulingService


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
    audiences: AudienceStore
    scheduling: SchedulingService
    due_scheduler: DueScheduler


def build_services(account_manager, auth_manager, engine, intake, env_file: Path) -> AppServices:
    publishing = PublishingService(engine, account_manager)
    return AppServices(
        publishing=publishing,
        accounts=AccountService(account_manager, auth_manager, engine),
        links=LinkService(engine.links),
        content=ContentService(intake),
        settings=SettingsService(env_file, auth_manager),
        account_manager=account_manager,
        auth_manager=auth_manager,
        engine=engine,
        intake=intake,
        audiences=AudienceStore(engine.store.database),
        scheduling=SchedulingService(publishing),
        due_scheduler=DueScheduler(publishing),
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
    "DueEvent",
    "DueRunResult",
    "DueScheduler",
    "JobView",
    "LinkService",
    "MediaStatus",
    "PlatformSummary",
    "PublishingService",
    "ScheduleView",
    "SchedulingError",
    "SchedulingService",
    "SettingsService",
    "VideoInfo",
    "build_services",
    "friendly_error",
]

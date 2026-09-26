"""Regenerate the README screenshots in docs/images/ with DEMO data only.

Temp DB in C:/SocBotDemo (a neutral path, so no user name shows up in the pictures), fake accounts and
fake publishers: nothing touches the network or your real database. Run: python tools/readme_screenshots.py"""
import asyncio
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from textual.widgets import Input, SelectionList, TextArea

from src.accounts.manager import AccountManager
from src.content.intake import ContentIntake
from src.core.published_links import PublishedLinks
from src.core.publisher import PublisherEngine
from src.services import build_services
from src.storage.database import Database
from src.storage.tokens import TokenEncryption, generate_key
from src.tui.app import SocBotApp
from tests.unit.test_batch import FlakyInstagram
from tests.unit.test_fanout import jpeg_bytes
from tests.unit.test_publishing_engine import FakePublisher, ok

OUT = str(ROOT / "docs" / "images")
os.environ.setdefault("CLOUDFLARED_PATH", shutil.which("cloudflared") or "cloudflared")  # "Cloudflare available" card
os.environ["MEDIA_STORAGE_PROVIDER"] = "auto"
tmp = Path("C:/SocBotDemo")
tmp.mkdir(exist_ok=True)
enc = TokenEncryption(generate_key().encode())
db = Database(f"sqlite:///{tmp / 'demo.db'}", encryption=enc)
db.init()
am = AccountManager(db, enc)
future = datetime.now(timezone.utc) + timedelta(days=55)
IG = ["travel.diaries", "foodie.nepal", "daily.motivation", "tech.bytes", "fitness.flow",
      "urban.shots", "nature.clips", "music.vibes"]
for i, name in enumerate(IG, 1):
    am.create_account(platform="instagram", platform_account_id=f"ig{i:03d}", username=name, access_token="T",
                      expires_at=future)
for name in ("Demo Travel Channel", "Demo Tech Channel"):
    am.create_account(platform="youtube", platform_account_id=name.replace(" ", ""), username=name,
                      display_name=name, access_token="T", expires_at=future)

video = tmp / "summer_trip.mp4"
video.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"v" * 48_000_000)
cover = tmp / "cover.jpg"
cover.write_bytes(jpeg_bytes())

fake_ig = FlakyInstagram(hold=0.05)
fake_ig.cover_plan = lambda path: ("upload", "") if path else ("none", "no cover")
yt = FakePublisher("youtube", ok("dQw4w9WgXcQ"), ok("aBcDeFgHiJk"))
yt.published_url = lambda media_id, state: f"https://www.youtube.com/watch?v={media_id}"
links = PublishedLinks(tmp / "links")
engine = PublisherEngine(db, am, publishers={"instagram": fake_ig, "youtube": yt}, sleep=lambda s: None,
                         probe_media=False, links=links, max_concurrent=5)
intake = ContentIntake(db, am, engine, root=tmp / "content")
services = build_services(am, None, engine, intake, tmp / ".env")
services.accounts.auth = type("A", (), {"is_configured": lambda self, p: p != "tiktok"})()
services.settings.auth = services.accounts.auth


async def main():
    app = SocBotApp(services)
    async with app.run_test(size=(120, 38)) as pilot:
        await pilot.pause(0.5)
        # a finished earlier batch for dashboard/queue/links
        ids = [a.id for a in services.accounts.list("instagram")]
        services.publishing.publish_batch(services.publishing.create_batch(
            services.publishing.plan(str(video), "Sunset in Pokhara 🌅 #travel", str(cover), ids)))
        fake_ig.fails = {"ig003": 1}  # one account fails once: shows the automatic retry round
        await pilot.press("c")
        await pilot.pause()
        s = app.screen
        s.query_one("#video-path", Input).value = str(video)
        s.query_one("#cover-path", Input).value = str(cover)
        s.load_video()
        s.load_cover()
        await pilot.pause()
        app.save_screenshot("create_media.svg", OUT)
        s.action_next()
        await pilot.pause()
        s.query_one("#caption", TextArea).text = "Weekend vibes in the mountains 🏔️\n#travel #nepal #reels"
        s.action_next()
        await pilot.pause()
        s.toggle_platform("instagram")
        await pilot.pause()
        s.query_one("#accounts", SelectionList).select_all()
        await pilot.pause()
        app.save_screenshot("create_accounts.svg", OUT)
        s.action_next()
        await pilot.pause()
        s.action_next()
        await pilot.pause()
        app.save_screenshot("create_review.svg", OUT)
        s.query_one("#include-dups").value = True
        s.confirm_publish()
        await pilot.pause()
        await pilot.press("y")
        for _ in range(100):
            await pilot.pause(0.05)
            if type(app.screen).__name__ == "PublishingScreen" and not app.screen.running:
                break
        await pilot.pause(0.3)
        app.save_screenshot("publishing_complete.svg", OUT)
        for mode, name in (("dashboard", "dashboard"), ("queue", "queue"), ("links", "links"),
                           ("accounts", "accounts")):
            app.pop_screen() if type(app.screen).__name__ == "PublishingScreen" else None
            app.navigate(mode)
            await pilot.pause(0.5)
            app.save_screenshot(f"{name}.svg", OUT)


Path(OUT).mkdir(parents=True, exist_ok=True)
try:
    asyncio.run(main())
finally:
    db.engine.dispose()
    shutil.rmtree(tmp, ignore_errors=True)
print(sorted(p.name for p in Path(OUT).iterdir()))

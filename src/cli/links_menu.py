"""CLI: Published Links (per-platform JSON link library)."""

from src.cli.display import (
    clear_screen,
    print_error,
    print_header,
    print_info,
    print_success,
    print_warning,
)
from src.cli.prompts import prompt_choice, prompt_int
from src.core.published_links import PublishedLinks, copy_to_clipboard

TITLES = {"youtube": "YouTube", "instagram": "Instagram", "tiktok": "TikTok"}


def run_published_links(links: PublishedLinks, engine=None) -> None:
    while True:
        clear_screen()
        print_header("PUBLISHED LINKS")
        print_info(f"Stored in {links.root}")
        options = ["YouTube", "Instagram", "TikTok", "Import from publish history", "Back"]
        choice = prompt_choice("Option", options, default=5)
        if choice in (None, 5):
            return
        if choice == 4:
            import_history(engine)
            input("\nPress Enter to continue...")
            continue
        run_platform_links(links, ("youtube", "instagram", "tiktok")[choice - 1])


def run_platform_links(links: PublishedLinks, platform: str) -> None:
    while True:
        clear_screen()
        print_header(f"{TITLES[platform].upper()} LINKS")
        choice = prompt_choice("Option", ["List links", "Copy link", "Back"], default=3)
        if choice in (None, 3):
            return
        records = list_links(links, platform)
        if choice == 2 and records:
            copy_link(records)
        input("\nPress Enter to continue...")


def list_links(links: PublishedLinks, platform: str) -> list[dict]:
    records = links.records(platform)
    if not records:
        print_info(f"No {TITLES[platform]} links saved.")
        if platform == "tiktok":
            print_info("TikTok's Content Posting API returns no post URL for private (SELF_ONLY) posts, "
                       "so TikTok links can't be saved yet.")
        return records
    for i, record in enumerate(records, 1):
        print(f"\n{i}. {record.get('video', '?')}")
        print(f"   Account: {record.get('account', '?')}")
        print(f"   Published: {str(record.get('published_at', ''))[:10]}")
        print(f"   {record['url']}")
    return records


def copy_link(records: list[dict]) -> bool:
    number = prompt_int("\nLink number to copy", min_val=1, max_val=len(records))
    if number is None:
        return False
    try:
        copy_to_clipboard(records[number - 1]["url"])
    except (OSError, ValueError) as e:
        print_error(f"Could not copy to the clipboard: {e}")
        return False
    print_success("✓ Link copied to clipboard.")
    return True


def import_history(engine) -> None:
    if engine is None or engine.links is None:
        print_warning("Publishing engine not available.")
        return
    print_info("Adding links for videos Soc_bot already published (duplicates are skipped)...")
    added = engine.import_published_links()
    for platform, count in added.items():
        print_info(f"  {TITLES[platform]}: {count} new")

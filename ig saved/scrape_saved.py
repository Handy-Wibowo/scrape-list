"""
Scrape all your Instagram saved posts, organized by collection.

For every saved post this downloads:
  - the image(s) / video(s)
  - caption.txt
  - meta.json   (instagram link, caption, author, upload date, save date, collection)
And rebuilds index.csv in the output folder at the end.

Usage:
    python scrape_saved.py                          # everything
    python scrape_saved.py --max 5                  # first 5 posts per collection (test run)
    python scrape_saved.py --collections "Recipes,Travel Ideas"
    python scrape_saved.py --output my_backup

Login session is cached in ig_session.json, so you only enter your
password once. Credentials are sent only to Instagram.
"""

import argparse
import csv
import getpass
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from instagrapi import Client
from instagrapi.extractors import extract_media_v1
from instagrapi.exceptions import (
    ChallengeRequired,
    LoginRequired,
    PleaseWaitFewMinutes,
    TwoFactorRequired,
)
from instagrapi.mixins.challenge import ChallengeChoice
from instagrapi.types import Collection

SESSION_FILE = Path("ig_session.json")


def challenge_code_handler(username: str, choice: ChallengeChoice) -> str:
    """Called by instagrapi when Instagram sends a security code."""
    where = "email" if choice == ChallengeChoice.EMAIL else "phone (SMS)"
    print(f"\nInstagram sent a security code to your {where}.")
    return input("Enter the security code: ").strip()


def get_sessionid(cli_value: str = "") -> str:
    """Session id from --sessionid, IG_SESSIONID env var, or ig_sessionid.txt."""
    if cli_value:
        return cli_value.strip()
    if os.environ.get("IG_SESSIONID"):
        return os.environ["IG_SESSIONID"].strip()
    f = Path("ig_sessionid.txt")
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return ""


def login(sessionid: str = "") -> Client:
    cl = Client()
    cl.delay_range = [2, 5]  # random delay between API calls
    cl.challenge_code_handler = challenge_code_handler

    if SESSION_FILE.exists():
        try:
            cl.load_settings(SESSION_FILE)
            cl.get_timeline_feed()  # validate the session is still alive
            print("Logged in using saved session.")
            return cl
        except Exception:
            print("Saved session missing/expired, logging in...")

    # Option 1: reuse the session from your real browser (no new login,
    # so no security challenge). Recommended when password login is blocked.
    if sessionid:
        try:
            cl.login_by_sessionid(sessionid)
            cl.dump_settings(SESSION_FILE)
            print("Logged in using browser session id, session saved.")
            return cl
        except Exception as e:
            sys.exit(f"Login with session id failed: {e}\n"
                     "The sessionid may be wrong or expired - copy it again "
                     "from your browser.")

    # Option 2: username + password login.
    # Save device identifiers BEFORE login so that if Instagram challenges
    # this login, retrying uses the same "device" that you approved.
    cl.dump_settings(SESSION_FILE)

    username = input("Instagram username: ").strip()
    password = getpass.getpass("Instagram password: ")
    try:
        cl.login(username, password)
    except TwoFactorRequired:
        code = input("Two-factor authentication code: ").strip()
        cl.login(username, password, verification_code=code)
    except ChallengeRequired as e:
        sys.exit(
            "\nInstagram requires a manual security check that can't be "
            "completed with a code.\n\n"
            "EASIEST FIX - reuse your browser session instead:\n"
            "  1. In your browser, log in to instagram.com\n"
            "  2. Press F12 -> Application -> Cookies -> "
            "https://www.instagram.com\n"
            "  3. Copy the value of the 'sessionid' cookie\n"
            "  4. Save it in a file named ig_sessionid.txt next to this "
            "script (or run with --sessionid VALUE)\n"
            "  5. Run the script again\n\n"
            "Alternative: approve the login in the Instagram app "
            "('This was me'), then re-run.\n"
            f"(details: {e})"
        )
    cl.dump_settings(SESSION_FILE)
    print("Login OK, session saved.")
    return cl


def get_collections(cl) -> list[Collection]:
    cols = list(cl.collections())
    # Make sure the default "All posts" collection is included
    if not any("all" in c.name.lower() for c in cols):
        try:
            pk = cl.collection_pk_by_name("All posts")
            cols.insert(0, Collection(id=pk, name="All posts",
                                      type="ALL_POSTS", media_count=0))
        except Exception:
            print("Note: 'All posts' collection not found via API; "
                  "only your custom collections will be scraped.")
    return cols


def find_saved_at(item: dict) -> str:
    """Look for a save timestamp in the raw API item (usually not exposed)."""
    for key, value in item.items():
        if key == "media" or not isinstance(value, (int, float)):
            continue
        if any(k in key.lower() for k in ("save", "added", "time")):
            try:
                return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
            except (OverflowError, OSError, ValueError):
                return ""
    return ""


def get_all_medias(cl, collection, max_items: int = 0) -> list:
    """Fetch every media in a collection, following pagination.

    Returns a list of (media, saved_at_iso) tuples. saved_at_iso is empty
    when Instagram does not expose the save timestamp (usually the case).
    """
    if "all" in collection.name.lower() or not str(collection.id).isdigit():
        endpoint = "feed/saved/posts/"
    else:
        endpoint = f"feed/collection/{collection.id}/"

    medias: dict = {}  # pk -> (media, saved_at)
    max_id = ""
    while True:
        params = {"include_igtv_preview": "false"}
        if max_id:
            params["max_id"] = max_id
        try:
            result = cl.private_request(endpoint, params=params)
        except Exception as e:
            if not medias:
                raise
            print(f"  pagination stopped early ({e}); "
                  f"keeping {len(medias)} posts")
            break
        items = result.get("items", [])
        new = 0
        for item in items:
            try:
                media = extract_media_v1(item.get("media", item))
            except Exception:
                continue
            if media.pk in medias:
                continue
            medias[media.pk] = (media, find_saved_at(item))
            new += 1
        max_id = result.get("next_max_id") or result.get("max_id") or ""
        if not new or not max_id:
            break
        if max_items and len(medias) >= max_items:
            break
        time.sleep(random.uniform(2, 4))
    result_list = list(medias.values())
    return result_list[:max_items] if max_items else result_list


def best_image_url(media) -> str:
    """Highest-resolution image URL from a Media object."""
    iv2 = getattr(media, "image_versions2", None)
    candidates = getattr(iv2, "candidates", None) or []
    if candidates:
        return str(candidates[0].url)
    return str(media.thumbnail_url) if getattr(media, "thumbnail_url", None) else ""


def download_media(cl, media, folder: Path):
    """Download using the CDN URLs from the collection listing.

    Avoids instagrapi's media_info re-fetch, which goes through the public
    web API and hits Instagram's login wall.
    """
    mt = media.media_type
    if mt == 1:                       # photo
        url = best_image_url(media)
        if not url:
            raise ValueError("no image URL available")
        cl.photo_download_by_url(url, folder=folder)
    elif mt == 2:                     # video / reel / igtv
        url = str(media.video_url) if media.video_url else ""
        if not url:
            raise ValueError("no video URL available")
        cl.video_download_by_url(url, folder=folder)
    elif mt == 8:                     # carousel / album
        urls = []
        for r in media.resources:
            if r.media_type == 2 and r.video_url:
                urls.append(str(r.video_url))
            elif r.thumbnail_url:
                urls.append(str(r.thumbnail_url))
        if not urls:
            raise ValueError("no album URLs available")
        cl.album_download_by_urls(urls, folder)
    else:
        raise ValueError(f"unknown media_type {mt}")


def safe_name(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name).strip().strip(".")
    return name[:60] or "collection"


def post_url(media) -> str:
    return f"https://www.instagram.com/p/{media.code}/" if media.code else ""


def rebuild_index(output: Path):
    """Rebuild index.csv from every meta.json in the output folder."""
    rows = []
    for meta_file in sorted(output.rglob("meta.json")):
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            continue
        meta["folder"] = str(meta_file.parent)
        rows.append(meta)
    if not rows:
        return
    for r in rows:
        # backwards compatibility with meta.json files from older runs
        r["uploaded_at"] = r.get("uploaded_at") or r.get("taken_at", "")
        r["saved_at"] = r.get("saved_at", "")
    fields = ["collection", "url", "code", "author", "uploaded_at",
              "saved_at", "media_type", "caption", "folder"]
    with open(output / "index.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["collection"], r["uploaded_at"])):
            r["caption"] = (r.get("caption") or "").replace("\r", " ").replace("\n", " ")
            w.writerow(r)
    print(f"\nindex.csv written with {len(rows)} posts: {output / 'index.csv'}")


def main():
    ap = argparse.ArgumentParser(description="Download your Instagram saved posts by collection.")
    ap.add_argument("--output", default="saved_posts", help="output folder (default: saved_posts)")
    ap.add_argument("--max", type=int, default=0, help="max posts per collection, 0 = all")
    ap.add_argument("--collections", default="", help="comma-separated collection names to scrape (default: all)")
    ap.add_argument("--sessionid", default="", help="sessionid cookie from your browser (bypasses login challenges)")
    args = ap.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    cl = login(get_sessionid(args.sessionid))
    saved_at_warned = False
    only = {n.strip().lower() for n in args.collections.split(",") if n.strip()}

    collections = get_collections(cl)
    print(f"\nFound {len(collections)} collection(s):")
    for c in collections:
        print(f"  - {c.name} ({c.media_count} posts)")

    for col in collections:
        if only and col.name.lower() not in only:
            continue
        print(f"\n=== {col.name} ===")
        try:
            medias = get_all_medias(cl, col, args.max)
        except Exception as e:
            print(f"  could not list posts: {e}")
            continue
        print(f"  {len(medias)} post(s)")

        col_dir = output / safe_name(col.name)
        any_saved_at = False
        for i, (media, saved_at) in enumerate(medias, 1):
            any_saved_at = any_saved_at or bool(saved_at)
            post_dir = col_dir / (media.code or str(media.pk))
            meta_file = post_dir / "meta.json"
            if meta_file.exists():
                print(f"  [{i}/{len(medias)}] {media.code} already downloaded, skipping")
                continue
            post_dir.mkdir(parents=True, exist_ok=True)
            try:
                download_media(cl, media, post_dir)
                caption = media.caption_text or ""
                (post_dir / "caption.txt").write_text(caption, encoding="utf-8")
                meta = {
                    "collection": col.name,
                    "url": post_url(media),
                    "code": media.code or "",
                    "author": media.user.username if media.user else "",
                    "uploaded_at": media.taken_at.isoformat() if media.taken_at else "",
                    "saved_at": saved_at,
                    "media_type": str(media.media_type),
                    "caption": caption,
                }
                meta_file.write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
                print(f"  [{i}/{len(medias)}] {meta['url']}")
            except PleaseWaitFewMinutes:
                print("  rate limited by Instagram - waiting 5 minutes...")
                time.sleep(300)
            except Exception as e:
                print(f"  [{i}/{len(medias)}] failed ({media.code}): {e}")
            time.sleep(random.uniform(2, 5))  # be nice to Instagram's rate limits

        if medias and not any_saved_at and not saved_at_warned:
            saved_at_warned = True
            print("\n  note: Instagram's API does not expose the time you saved a "
                  "post, so 'saved_at' will be empty.\n  (Save timestamps are only "
                  "available via Instagram's official data export: "
                  "Settings > Accounts Center > Download your information)")

    rebuild_index(output)
    print("\nDone!")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted - already-downloaded posts are saved, "
              "just run the script again to resume.")
    except LoginRequired:
        SESSION_FILE.unlink(missing_ok=True)
        sys.exit("Session expired. Deleted ig_session.json - run again to log in fresh.")

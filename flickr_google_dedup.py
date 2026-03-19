#!/usr/bin/env python3
"""
Flickr / Google Photos deduplication checker
---------------------------------------------
Reads Google Takeout metadata JSON files and compares against your Flickr
library to identify which photos are safely backed up on Flickr and which
are missing — so you know what's safe to delete from Google Photos.

Requirements:
    pip install flickrapi tqdm

Setup:
    1. Get a Flickr API key at https://www.flickr.com/services/apps/create/
    2. Export Google Photos via https://takeout.google.com (select Photos only)
    3. Unzip the Takeout archive and point TAKEOUT_DIR below to the folder
    4. Fill in your Flickr API key and secret below
    5. Run:  python3 flickr_google_dedup.py
"""

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict
from dotenv import load_dotenv

# Load credentials from .env file
load_dotenv()

# ─────────────────────────────────────────────
# CONFIGURATION
# All values are read from your .env file.
# See .env.example for the required keys.
# ─────────────────────────────────────────────

FLICKR_API_KEY    = os.getenv("FLICKR_API_KEY")
FLICKR_API_SECRET = os.getenv("FLICKR_API_SECRET")
TAKEOUT_DIR       = os.getenv("TAKEOUT_DIR")

# Where to save the output report
OUTPUT_REPORT = "dedup_report.txt"

# ─────────────────────────────────────────────
# STEP 1 — Load Google Photos metadata
# ─────────────────────────────────────────────

def parse_takeout_metadata(takeout_dir: str) -> dict:
    """
    Walks through the Takeout directory and reads every .json sidecar file.
    Returns a dict keyed by normalised filename -> metadata dict.
    """
    takeout_path = Path(takeout_dir)
    if not takeout_path.exists():
        raise FileNotFoundError(f"Takeout directory not found: {takeout_dir}")

    google_photos = {}
    json_files_found = 0

    print(f"\n📂  Scanning Takeout folder: {takeout_dir}")

    for json_file in takeout_path.rglob("*.json"):
        # Skip album metadata files (they don't have photoTakenTime)
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue

        if "photoTakenTime" not in data:
            continue

        json_files_found += 1

        # The title field is the original filename
        title = data.get("title", "").strip()
        if not title:
            continue

        taken_ts = int(data["photoTakenTime"].get("timestamp", 0))
        taken_dt = datetime.fromtimestamp(taken_ts, tz=timezone.utc) if taken_ts else None

        normalised = normalise_filename(title)
        google_photos[normalised] = {
            "original_filename": title,
            "taken_date":        taken_dt,
            "taken_timestamp":   taken_ts,
            "json_path":         str(json_file),
            "description":       data.get("description", ""),
        }

    print(f"   Found {json_files_found} photo metadata entries in Takeout.")
    return google_photos


# ─────────────────────────────────────────────
# STEP 2 — Load Flickr library
# ─────────────────────────────────────────────

def load_flickr_photos(api_key: str, api_secret: str) -> dict:
    """
    Fetches all photos from the authenticated Flickr account.
    Returns a dict keyed by normalised filename -> metadata dict.
    """
    try:
        import flickrapi
    except ImportError:
        raise ImportError("flickrapi not installed. Run: pip install flickrapi tqdm")

    print("\n🔑  Authenticating with Flickr…")
    flickr = flickrapi.FlickrAPI(api_key, api_secret, format="parsed-json")

    # OAuth authentication — opens browser on first run, caches token after
    if not flickr.token_valid(perms="read"):
        flickr.get_request_token(oauth_callback="oob")
        authorize_url = flickr.auth_url(perms="read")
        print(f"\nOpen this URL in your browser to authorise:\n{authorize_url}\n")
        verifier = input("Paste the verifier code here: ").strip()
        flickr.get_access_token(verifier)

    print("   Authenticated ✓")
    print("   Fetching your Flickr photo list (this may take a while for large libraries)…")

    try:
        from tqdm import tqdm
        use_tqdm = True
    except ImportError:
        use_tqdm = False

    flickr_photos = {}
    page = 1
    total_pages = 1

    while page <= total_pages:
        resp = flickr.photos.search(
            user_id="me",
            per_page=500,
            page=page,
            extras="date_upload,date_taken,original_format,url_o",
        )

        photos_data = resp.get("photos", {})
        total_pages = int(photos_data.get("pages", 1))
        total_count = int(photos_data.get("total", 0))

        if page == 1:
            print(f"   Total Flickr photos: {total_count} across {total_pages} pages")

        photos = photos_data.get("photo", [])

        iterator = tqdm(photos, desc=f"Page {page}/{total_pages}") if use_tqdm else photos

        for p in iterator:
            # Flickr stores the original filename in the title (usually)
            title = p.get("title", "").strip()
            original_format = p.get("originalformat", "jpg")
            photo_id = p.get("id", "")

            # Reconstruct likely original filename
            # Flickr often strips the extension from the title
            if title and "." not in title[-5:]:
                filename_guess = f"{title}.{original_format}"
            else:
                filename_guess = title

            normalised = normalise_filename(filename_guess)

            # Also index by Flickr photo ID for reference
            taken_raw = p.get("datetaken", "")
            taken_dt = None
            if taken_raw:
                try:
                    taken_dt = datetime.strptime(taken_raw, "%Y-%m-%d %H:%M:%S").replace(
                        tzinfo=timezone.utc
                    )
                except ValueError:
                    pass

            flickr_photos[normalised] = {
                "flickr_id":         photo_id,
                "title":             title,
                "filename_guess":    filename_guess,
                "taken_date":        taken_dt,
                "original_format":   original_format,
            }

        page += 1

    print(f"   Loaded {len(flickr_photos)} photos from Flickr ✓")
    return flickr_photos


# ─────────────────────────────────────────────
# STEP 3 — Cross-reference
# ─────────────────────────────────────────────

def cross_reference(google_photos: dict, flickr_photos: dict) -> tuple[list, list, list]:
    """
    Compares Google and Flickr libraries.

    Returns three lists:
        safe_to_delete   — in Google AND confirmed on Flickr
        missing_on_flickr — in Google but NOT found on Flickr
        flickr_only      — on Flickr but not in Google Takeout export
    """
    google_keys  = set(google_photos.keys())
    flickr_keys  = set(flickr_photos.keys())

    safe_keys    = google_keys & flickr_keys
    missing_keys = google_keys - flickr_keys
    extra_keys   = flickr_keys - google_keys

    safe_to_delete = [
        {**google_photos[k], "flickr_id": flickr_photos[k]["flickr_id"]}
        for k in sorted(safe_keys)
    ]
    missing_on_flickr = [google_photos[k] for k in sorted(missing_keys)]
    flickr_only       = [flickr_photos[k] for k in sorted(extra_keys)]

    return safe_to_delete, missing_on_flickr, flickr_only


# ─────────────────────────────────────────────
# STEP 4 — Write report
# ─────────────────────────────────────────────

def write_report(safe: list, missing: list, flickr_only: list, output_path: str):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    lines = []
    lines.append("=" * 70)
    lines.append("  FLICKR / GOOGLE PHOTOS DEDUPLICATION REPORT")
    lines.append(f"  Generated: {now}")
    lines.append("=" * 70)
    lines.append("")
    lines.append(f"  ✅  Safe to delete from Google Photos : {len(safe):,}")
    lines.append(f"  ⚠️   Missing on Flickr (do NOT delete) : {len(missing):,}")
    lines.append(f"  📷  On Flickr only (not in Takeout)   : {len(flickr_only):,}")
    lines.append("")

    # ── Safe to delete ──────────────────────────────────────────────────
    lines.append("─" * 70)
    lines.append("✅  SAFE TO DELETE FROM GOOGLE PHOTOS")
    lines.append("    These photos are confirmed on Flickr.")
    lines.append("─" * 70)
    if safe:
        for item in safe:
            date_str = item["taken_date"].strftime("%Y-%m-%d") if item["taken_date"] else "unknown date"
            lines.append(f"  {item['original_filename']:<50}  {date_str}  [Flickr ID: {item['flickr_id']}]")
    else:
        lines.append("  (none found)")
    lines.append("")

    # ── Missing on Flickr ───────────────────────────────────────────────
    lines.append("─" * 70)
    lines.append("⚠️   DO NOT DELETE — MISSING ON FLICKR")
    lines.append("    These are in Google Photos but were NOT found on Flickr.")
    lines.append("    Upload them to Flickr before deleting from Google.")
    lines.append("─" * 70)
    if missing:
        for item in missing:
            date_str = item["taken_date"].strftime("%Y-%m-%d") if item["taken_date"] else "unknown date"
            lines.append(f"  {item['original_filename']:<50}  {date_str}")
    else:
        lines.append("  (none — your Flickr library appears complete! 🎉)")
    lines.append("")

    # ── Flickr only ─────────────────────────────────────────────────────
    lines.append("─" * 70)
    lines.append("📷  ON FLICKR ONLY (not in this Takeout export)")
    lines.append("    These exist on Flickr but weren't in your Takeout export.")
    lines.append("    Likely camera uploads or photos already deleted from Google.")
    lines.append("─" * 70)
    if flickr_only:
        for item in flickr_only[:100]:   # cap at 100 to keep report readable
            date_str = item["taken_date"].strftime("%Y-%m-%d") if item["taken_date"] else "unknown date"
            lines.append(f"  {item['filename_guess']:<50}  {date_str}  [Flickr ID: {item['flickr_id']}]")
        if len(flickr_only) > 100:
            lines.append(f"  … and {len(flickr_only) - 100:,} more.")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("=" * 70)
    lines.append("  END OF REPORT")
    lines.append("=" * 70)

    report_text = "\n".join(lines)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    print(report_text)
    print(f"\n📄  Full report saved to: {output_path}")


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def normalise_filename(filename: str) -> str:
    """
    Normalises a filename for comparison:
    - lowercase
    - strip whitespace
    - remove extension (we compare by stem only to handle format differences
      e.g. IMG_1234.jpg vs IMG_1234.jpeg)
    - strip Google's duplicate suffix pattern like (1), (2)
    """
    stem = Path(filename).stem.lower().strip()
    # Remove duplicate suffixes added by Google e.g. "IMG_1234 (1)"
    stem = re.sub(r"\s*\(\d+\)$", "", stem)
    return stem


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────

def main():
    print("\n🔍  Flickr / Google Photos Deduplication Checker")
    print("   ─────────────────────────────────────────────")

    # Validate config
    if not FLICKR_API_KEY or not FLICKR_API_SECRET:
        print("\n❌  FLICKR_API_KEY or FLICKR_API_SECRET missing.")
        print("    Add them to your .env file. Get keys at: https://www.flickr.com/services/apps/create/\n")
        return

    if not TAKEOUT_DIR:
        print("\n❌  TAKEOUT_DIR missing from your .env file.\n")
        return

    # Run
    google_photos              = parse_takeout_metadata(TAKEOUT_DIR)
    flickr_photos              = load_flickr_photos(FLICKR_API_KEY, FLICKR_API_SECRET)
    safe, missing, flickr_only = cross_reference(google_photos, flickr_photos)
    write_report(safe, missing, flickr_only, OUTPUT_REPORT)

    print(f"\n✅  Done!")
    print(f"    {len(safe):,} photos safe to delete from Google Photos")
    print(f"    {len(missing):,} photos still need to be uploaded to Flickr first\n")


if __name__ == "__main__":
    main()

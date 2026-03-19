# flickr-google-dedup

A Python script to cross-reference your Google Photos and Flickr libraries,
so you know exactly which photos are safe to delete from Google Photos without
losing anything that hasn't made it to Flickr yet.

## Why

Flickr's Auto-Uploadr doesn't always sync reliably in the background on Android.
This script removes the guesswork — it tells you definitively what's on Flickr
and what isn't before you delete anything from Google.

## How it works

1. Reads photo metadata from a **Google Takeout** export (JSON sidecars only — not the photos themselves)
2. Fetches your full **Flickr library** via the Flickr API
3. Cross-references by filename and outputs a report with three sections:
   - ✅ Safe to delete from Google Photos (confirmed on Flickr)
   - ⚠️ Do NOT delete — missing on Flickr (upload these first)
   - 📷 On Flickr only (not in Takeout export)

## Setup

### 1. Install dependencies

```bash
pip3 install flickrapi tqdm python-dotenv
```

### 2. Get a Flickr API key

Go to https://www.flickr.com/services/apps/create/ and create a non-commercial app.
You'll get an API Key and Secret.

### 3. Export Google Photos metadata

Go to https://takeout.google.com and export **Google Photos only**.
You don't need the actual image files — just the metadata JSONs.
Unzip the archive when it's ready.

### 4. Configure your .env file

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```
FLICKR_API_KEY=your_key_here
FLICKR_API_SECRET=your_secret_here
TAKEOUT_DIR=/Users/yourname/Downloads/Takeout/Google Photos
```

### 5. Run

```bash
python3 flickr_google_dedup.py
```

On first run, the script will open a browser window to authorize Flickr access.
The token is cached locally so you won't need to do this again.

## Output

The script prints a report to the terminal and saves it as `dedup_report.txt`.

## Notes

- The script only reads metadata — it never modifies or deletes any photos
- Matching is done by filename stem (without extension) to handle format differences e.g. `.jpg` vs `.jpeg`
- Google duplicate suffixes like `IMG_1234 (1).jpg` are normalized automatically

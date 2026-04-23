<<<<<<< HEAD
# Instagram Post Exporter (Playwright + Excel)

This tool opens an Instagram profile, collects discovered post links, visits each post, and exports metrics into Excel grouped by month.

## What It Collects
- Post URL
- Post date
- Post type (Reel, Video, Carousel, Photo, Unknown)
- Comments
- Likes
- Shares (when exposed by Instagram)

If a value is not exposed on the page, the output writes `Cannot detect` instead of skipping the post.

## Requirements
- Python 3.10+
- Playwright
- openpyxl
- Chromium browser for Playwright

## Install
```bash
pip install playwright openpyxl
python -m playwright install chromium
```

## Run
```bash
python instagram_to_excel.py
```

On first run, a browser window opens. Log in manually if prompted, then press Enter in terminal.

## Reliability Behavior
- The collector scrolls profile posts in rounds with a hard upper bound to avoid infinite loops.
- Each post load uses retry logic (`POST_LOAD_RETRIES`) to handle temporary delays/timeouts.
- Posts are still written to Excel even when some metrics/date cannot be parsed.
- Missing values are marked as `Cannot detect`.

## Main Config (in script)
- `PROFILE_URL`: Target profile
- `OUTPUT_FILE`: Output Excel filename
- `MAX_POSTS`: Optional cap (`None` means no cap)
- `MAX_SCROLL_ROUNDS`: Max profile scroll rounds
- `MAX_STAGNANT_ROUNDS`: Stop when no new links for this many rounds
- `POST_LOAD_RETRIES`: Retries per post URL

## Notes
- Instagram markup changes over time, so selectors/parsers may need maintenance.
- Some share counts are not publicly exposed; those cells will show `Cannot detect`.
=======
# IG_tool
>>>>>>> 06c132b1cd795add43a02c491979519ea9e82887

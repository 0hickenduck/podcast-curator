# podcast-curator

Turn checked episode recommendations in GitHub Issues into an RSS feed.

Feed URL: https://0hickenduck.github.io/podcast-curator/curated.xml

## Issue Format
Issue label: `recommendation`
Checked line format: `- [x] `3fa2c19b0d` Show Name — Episode Title (42 min)`

## Local Usage
Install requirements:
```bash
pip install pyyaml
```

1. Fetch candidate episodes from sources:
```bash
python scripts/fetch_candidates.py
```

2. Sync checked issue recommendations to `curated.xml`:
```bash
python scripts/sync_issue_to_rss.py --body-file path/to/issue_body.md
```

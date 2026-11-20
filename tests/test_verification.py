import json
import os
import pathlib
import subprocess
import xml.etree.ElementTree as ET
import yaml

# 1. Clean temp files and checkout curated.xml
for f in ["temp_real_sources.yaml", "temp_real_candidates.json", "candidates.json", "tests/test_sources.yaml"]:
    if os.path.exists(f):
        os.remove(f)
subprocess.run(["git", "checkout", "curated.xml"], check=True)

# 2. Setup fixture source
fixture_uri = pathlib.Path("tests/fixtures/sample_feed.xml").resolve().as_uri()
with open("tests/test_sources.yaml", "w", encoding="utf-8") as f:
    yaml.dump({"feeds": [{"title": "Sample Podcast Show", "url": fixture_uri}]}, f)

# 3. Run fetch_candidates.py
print("--- Running fetch_candidates.py on fixture ---")
subprocess.run(
    ["python", "scripts/fetch_candidates.py", "--sources", "tests/test_sources.yaml", "--out", "candidates.json"],
    check=True,
)

# 4. Run sync_issue_to_rss.py first time
print("--- Running sync_issue_to_rss.py (first run) ---")
p1 = subprocess.run(
    ["python", "scripts/sync_issue_to_rss.py", "--body-file", "tests/sample_issue_body.md"],
    capture_output=True,
    text=True,
)
print("STDOUT:", p1.stdout.strip())
print("STDERR:", p1.stderr.strip())
assert p1.returncode == 0, "First sync run failed"

# 5. Verify curated.xml parses and contains item once
tree = ET.parse("curated.xml")
items = tree.findall(".//item")
assert len(items) == 1, f"Expected 1 item, got {len(items)}"
guid = items[0].find("guid").text
assert guid == "fixture-guid-ep1", f"Unexpected guid {guid}"
title = items[0].find("title").text
assert title == "Sample Podcast Show — Recent Episode One", f"Unexpected title {title}"
dur = items[0].find("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration").text
assert dur == "42:15", f"Unexpected duration {dur}"
print("curated.xml parsed and validated successfully!")

# 6. Run sync_issue_to_rss.py second time (idempotent check)
print("--- Running sync_issue_to_rss.py (second run - idempotency) ---")
p2 = subprocess.run(
    ["python", "scripts/sync_issue_to_rss.py", "--body-file", "tests/sample_issue_body.md"],
    capture_output=True,
    text=True,
)
print("STDOUT:", p2.stdout.strip())
print("STDERR:", p2.stderr.strip())
assert p2.returncode == 0, "Second sync run failed"
assert "No new episodes to add." in p2.stdout

tree2 = ET.parse("curated.xml")
items2 = tree2.findall(".//item")
assert len(items2) == 1, f"Expected still 1 item, got {len(items2)}"
print("Idempotency check passed: no duplicates added!")

# 7. Test real public feed
print("--- Running fetch_candidates.py on real public feed ---")
with open("temp_real_sources.yaml", "w", encoding="utf-8") as f:
    yaml.dump({"feeds": [{"url": "https://feeds.npr.org/510289/podcast.xml"}]}, f)
subprocess.run(
    [
        "python",
        "scripts/fetch_candidates.py",
        "--sources",
        "temp_real_sources.yaml",
        "--out",
        "temp_real_candidates.json",
        "--days",
        "30",
        "--per-feed",
        "3",
    ],
    check=True,
)
with open("temp_real_candidates.json", "r", encoding="utf-8") as f:
    real_data = json.load(f)
episodes = real_data.get("episodes", [])
assert len(episodes) > 0, "Real feed produced 0 episodes"
print(f"Real feed successfully fetched {len(episodes)} episodes!")

# 8. Clean up and checkout curated.xml
for f in ["temp_real_sources.yaml", "temp_real_candidates.json", "candidates.json", "tests/test_sources.yaml"]:
    if os.path.exists(f):
        os.remove(f)
subprocess.run(["git", "checkout", "curated.xml"], check=True)
print("Cleaned up temp files and restored curated.xml to clean channel state.")

#!/usr/bin/env python3
"""Fetch candidate episodes from RSS feeds in sources.yaml."""

import argparse
import datetime
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import yaml


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    # Strip script/style blocks and comments
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:1500]


def extract_notes(item: ET.Element) -> str:
    # 1. content:encoded
    encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if encoded is not None and encoded.text and encoded.text.strip():
        return strip_html(encoded.text)
    for child in item:
        if child.tag.endswith("encoded") and child.text and child.text.strip():
            return strip_html(child.text)

    # 2. description
    desc = item.find("description")
    if desc is not None and desc.text and desc.text.strip():
        return strip_html(desc.text)

    # 3. itunes:summary
    summary = item.find("{http://www.itunes.com/dtds/podcast-1.0.dtd}summary")
    if summary is not None and summary.text and summary.text.strip():
        return strip_html(summary.text)
    for child in item:
        if child.tag.endswith("summary") and child.text and child.text.strip():
            return strip_html(child.text)

    return ""


def extract_duration(item: ET.Element) -> str:
    dur = item.findtext("{http://www.itunes.com/dtds/podcast-1.0.dtd}duration")
    if dur is not None and dur.strip():
        return dur.strip()
    for child in item:
        if child.tag.endswith("duration") and child.text and child.text.strip():
            return child.text.strip()
    return ""


def parse_feed_episodes(feed_url: str, show_override: str, content: bytes, days: int, per_feed: int, now: datetime.datetime):
    root = ET.fromstring(content)
    channel = root.find("channel")
    if channel is None:
        channel = root if root.tag.endswith("channel") else root.find(".//channel")

    show = show_override
    if not show and channel is not None:
        show = (channel.findtext("title") or "").strip()
    if not show:
        show = ""

    items = channel.findall("item") if channel is not None else root.findall(".//item")
    cutoff = now - datetime.timedelta(days=days)
    episodes = []

    for item in items:
        enclosure = item.find("enclosure")
        if enclosure is None:
            continue
        enclosure_url = (enclosure.get("url") or "").strip()
        if not enclosure_url:
            continue
        enclosure_type = (enclosure.get("type") or "").strip()
        enclosure_length = (enclosure.get("length") or "").strip()

        pub_date_raw = (item.findtext("pubDate") or "").strip()
        if not pub_date_raw:
            continue
        try:
            dt = email.utils.parsedate_to_datetime(pub_date_raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            else:
                dt = dt.astimezone(datetime.timezone.utc)
        except Exception:
            continue

        if dt < cutoff:
            continue
        published = dt.isoformat()

        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()

        guid_elem = item.find("guid")
        guid = (guid_elem.text or "").strip() if (guid_elem is not None and guid_elem.text) else enclosure_url

        ep_id = hashlib.sha1(f"{guid}|{feed_url}".encode("utf-8")).hexdigest()[:10]
        duration = extract_duration(item)
        notes = extract_notes(item)

        episodes.append({
            "id": ep_id,
            "show": show,
            "feed_url": feed_url,
            "title": title,
            "pub_date": pub_date_raw,
            "published": published,
            "duration": duration,
            "notes": notes,
            "link": link,
            "guid": guid,
            "enclosure_url": enclosure_url,
            "enclosure_type": enclosure_type,
            "enclosure_length": enclosure_length,
        })

    episodes.sort(key=lambda x: x["published"], reverse=True)
    return show, episodes[:per_feed]


def main():
    parser = argparse.ArgumentParser(description="Fetch candidate podcast episodes.")
    parser.add_argument("--sources", default="sources.yaml", help="Path to sources YAML file")
    parser.add_argument("--out", default="candidates.json", help="Path to output JSON file")
    parser.add_argument("--days", type=int, default=30, help="Max age in days for fetched episodes")
    parser.add_argument("--per-feed", type=int, default=10, help="Max episodes per feed")
    args = parser.parse_args()

    feeds = []
    if os.path.exists(args.sources):
        with open(args.sources, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            feeds = data.get("feeds") or []

    now = datetime.datetime.now(datetime.timezone.utc)
    all_new_episodes = []

    for feed_entry in feeds:
        if isinstance(feed_entry, dict):
            feed_url = feed_entry.get("url", "").strip()
            show_title = feed_entry.get("title", "").strip()
        elif isinstance(feed_entry, str):
            feed_url = feed_entry.strip()
            show_title = ""
        else:
            continue

        if not feed_url:
            continue

        try:
            req = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "podcast-curator/1.0"}
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read()
            show, episodes = parse_feed_episodes(
                feed_url, show_title, content, args.days, args.per_feed, now
            )
            print(f"{show or feed_url}: {len(episodes)} episode(s)")
            all_new_episodes.extend(episodes)
        except Exception as e:
            print(f"Warning: Failed to fetch feed '{feed_url}': {e}", file=sys.stderr)
            continue

    print(f"Total candidate episodes fetched: {len(all_new_episodes)}")

    # Merge with existing candidates.json
    existing_episodes = []
    if os.path.exists(args.out):
        try:
            with open(args.out, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_episodes = existing_data.get("episodes", [])
        except Exception as e:
            print(f"Warning: Failed to read existing candidates file '{args.out}': {e}", file=sys.stderr)

    new_ids = {ep["id"] for ep in all_new_episodes}
    cutoff_120 = now - datetime.timedelta(days=120)

    kept_old = []
    for ep in existing_episodes:
        if ep.get("id") in new_ids:
            continue
        pub_iso = ep.get("published", "")
        if pub_iso:
            try:
                pub_dt = datetime.datetime.fromisoformat(pub_iso)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=datetime.timezone.utc)
                if pub_dt < cutoff_120:
                    continue
            except Exception:
                pass
        kept_old.append(ep)

    merged = all_new_episodes + kept_old
    merged.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Check if candidate episode IDs have changed compared to existing candidates.json
    existing_ids = [ep.get("id") for ep in existing_episodes]
    merged_ids = [ep.get("id") for ep in merged]
    if os.path.exists(args.out) and existing_ids == merged_ids:
        print("No changes in candidate episodes; candidates.json remains unchanged.")
        return

    out_dir = os.path.dirname(args.out)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    tmp_path = args.out + ".tmp"
    output_data = {
        "generated": now.isoformat(),
        "episodes": merged,
    }
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp_path, args.out)


if __name__ == "__main__":
    main()

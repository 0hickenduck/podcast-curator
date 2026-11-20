#!/usr/bin/env python3
"""Sync checked episodes from GitHub issue bodies into curated.xml RSS feed."""

import argparse
import datetime
import email.utils
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"


def get_issue_bodies(body_file: str | None) -> list[str]:
    if body_file:
        with open(body_file, "r", encoding="utf-8") as f:
            return [f.read()]

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if event_path and os.path.exists(event_path):
        with open(event_path, "r", encoding="utf-8") as f:
            event = json.load(f)

        if "issue" in event and event["issue"]:
            body = event["issue"].get("body") or ""
            return [body]

        # workflow_dispatch: fetch open issues with label recommendation
        token = os.environ.get("GITHUB_TOKEN")
        repo = os.environ.get("GITHUB_REPOSITORY")
        if not token or not repo:
            print("Error: GITHUB_TOKEN and GITHUB_REPOSITORY required for API fetch", file=sys.stderr)
            sys.exit(1)

        api_url = f"https://api.github.com/repos/{repo}/issues?state=open&labels=recommendation&per_page=100"
        req = urllib.request.Request(
            api_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "podcast-curator/1.0",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            issues = json.load(resp)
        return [iss["body"] for iss in issues if iss.get("body")]

    print("Error: No --body-file provided and GITHUB_EVENT_PATH is not set.", file=sys.stderr)
    sys.exit(1)


def parse_checked_ids(bodies: list[str]) -> list[str]:
    pattern = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+.*?\b([0-9a-f]{10})\b")
    checked_ids = []
    seen = set()
    for body in bodies:
        for line in body.splitlines():
            m = pattern.match(line)
            if m:
                ep_id = m.group(1).lower()
                if ep_id not in seen:
                    seen.add(ep_id)
                    checked_ids.append(ep_id)
    return checked_ids


def create_item_element(ep: dict) -> ET.Element:
    item = ET.Element("item")

    show = ep.get("show", "").strip()
    title = ep.get("title", "").strip()
    full_title = f"{show} — {title}" if show and title else (title or show)

    title_elem = ET.SubElement(item, "title")
    title_elem.text = full_title

    guid_elem = ET.SubElement(item, "guid", {"isPermaLink": "false"})
    guid_elem.text = ep.get("guid", "")

    enclosure_attrs = {
        "url": ep.get("enclosure_url", ""),
        "type": ep.get("enclosure_type", "audio/mpeg"),
        "length": str(ep.get("enclosure_length", "0")),
    }
    ET.SubElement(item, "enclosure", enclosure_attrs)

    pub_date_elem = ET.SubElement(item, "pubDate")
    pub_date_elem.text = ep.get("pub_date", "")

    link_elem = ET.SubElement(item, "link")
    link_elem.text = ep.get("link", "")

    desc_elem = ET.SubElement(item, "description")
    desc_elem.text = ep.get("notes", "")

    duration = str(ep.get("duration", "")).strip()
    if duration:
        dur_elem = ET.SubElement(item, f"{{{ITUNES_NS}}}duration")
        dur_elem.text = duration

    return item


def main():
    parser = argparse.ArgumentParser(description="Sync checked issue recommendations to curated.xml")
    parser.add_argument("--body-file", help="Path to markdown issue body file (for testing)")
    parser.add_argument("--candidates", default="candidates.json", help="Path to candidates.json")
    parser.add_argument("--rss", default="curated.xml", help="Path to curated.xml RSS feed")
    args = parser.parse_args()

    bodies = get_issue_bodies(args.body_file)
    checked_ids = parse_checked_ids(bodies)
    if not checked_ids:
        print("No checked episode IDs found.")
        sys.exit(0)

    if not os.path.exists(args.candidates):
        print(f"Error: Candidates file '{args.candidates}' not found.", file=sys.stderr)
        sys.exit(1)

    with open(args.candidates, "r", encoding="utf-8") as f:
        candidates_data = json.load(f)
    candidates_by_id = {ep["id"]: ep for ep in candidates_data.get("episodes", []) if "id" in ep}

    ET.register_namespace("itunes", ITUNES_NS)
    tree = ET.parse(args.rss)
    root = tree.getroot()
    channel = root.find("channel")
    if channel is None:
        print(f"Error: No <channel> found in '{args.rss}'", file=sys.stderr)
        sys.exit(1)

    existing_guids = set()
    for item in channel.findall("item"):
        guid_elem = item.find("guid")
        if guid_elem is not None and guid_elem.text:
            existing_guids.add(guid_elem.text.strip())

    episodes_to_add = []
    for ep_id in checked_ids:
        if ep_id not in candidates_by_id:
            print(f"Warning: Episode ID '{ep_id}' not found in {args.candidates}", file=sys.stderr)
            continue
        ep = candidates_by_id[ep_id]
        guid = ep.get("guid", "").strip()
        if not guid or guid in existing_guids:
            continue
        episodes_to_add.append(ep)
        existing_guids.add(guid)

    if not episodes_to_add:
        print("No new episodes to add.")
        sys.exit(0)

    # Sort newly added episodes newest first
    episodes_to_add.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Update or add channel <lastBuildDate>
    now = datetime.datetime.now(datetime.timezone.utc)
    rfc822_date = email.utils.format_datetime(now)
    lbd = channel.find("lastBuildDate")
    if lbd is not None:
        lbd.text = rfc822_date
    else:
        lbd = ET.Element("lastBuildDate")
        lbd.text = rfc822_date
        first_item_idx = None
        for idx, child in enumerate(list(channel)):
            if child.tag == "item":
                first_item_idx = idx
                break
        if first_item_idx is not None:
            channel.insert(first_item_idx, lbd)
        else:
            channel.append(lbd)

    # Insert items at the top of the item list (newest-added first)
    first_item_idx = None
    for idx, child in enumerate(list(channel)):
        if child.tag == "item":
            first_item_idx = idx
            break

    for offset, ep in enumerate(episodes_to_add):
        item_elem = create_item_element(ep)
        if first_item_idx is None:
            channel.append(item_elem)
        else:
            channel.insert(first_item_idx + offset, item_elem)
        show = ep.get("show", "").strip()
        title = ep.get("title", "").strip()
        print(f"Added: {show} — {title} ({ep['id']})")

    ET.indent(tree, space="  ")
    with open(args.rss, "wb") as f:
        tree.write(f, encoding="utf-8", xml_declaration=True)
        f.write(b"\n")

    print(f"Total added: {len(episodes_to_add)} episode(s).")


if __name__ == "__main__":
    main()

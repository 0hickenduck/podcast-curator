#!/usr/bin/env python3
"""Fetch candidate podcast episodes from RSS feeds in sources.yaml, including curated classics."""

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


# Curated high-value EconTalk archive targets (Title substring -> Category & rationale)
ECONTALK_CURATED_TARGETS = {
    "David Wolpe on The Death of Ivan Ilyich": "普世母题 · 托尔斯泰死之省思与生命虚掷",
    "A Mind-Blowing Way of Looking at Math": "经典长青 · 2025年度最高分/思考的直觉与心智",
    "What Is Capitalism": "经典长青 · 2025年度第1名/经济学最底层的制度与交换",
    "The Perfect Tuba": "生活意志 · 2025年度第4名/从极度沉沦到乐队与共同体秩序",
    "Shampoo, Property Rights, and Civilization": "机制核心 · 2025年度第3名/产权分母与制度演化",
    "What Modern Medicine Gets Wrong": "风险判断 · 2024年度第1名/过度医疗与真正的健康分母",
    "The Problems of Boys and Men in Today's America": "人生母题 · 2024年度第3名/男性在当代社会的发展脱嵌与自处",
    "Letter from Birmingham Jail": "长青思想 · 2024年度Top10/狱中来信与非暴力的深层代价",
    "Glenn Loury Tells All": "真切Stake · 2024年度Top10/经济学家的自白与不掩饰的瑕疵真实",
    "A User's Guide to Our Emotional Thermostat": "情绪心智 · 幸福感的恒温器基准线与适应机制",
    "Incentives Are for Losers": "机制核心 · 悬赏机制的失效与内在自驱力",
    "Do Less, Heal More: The Case for Medical Conservatism": "风险判断 · 医疗保守主义/不干预的智慧与分母思考",
    "The Self, the Crowd, and Social Contagion": "比较与欲望 · 勒内·吉拉尔欲望模仿理论与群体传染",
    "Peter Attia on Lifespan, Healthspan, and Outlive": "健康分母 · 2023年度Top5/长寿与生命质量的底层干预",
    "Vinay Prasad on Cancer Screening": "风险判断 · 2023年度Top5/癌症筛查的统计幻觉与生存率真相",
    "Lydia Dugdale on the Lost Art of Dying": "普世母题 · 2023年度Top10/告别的艺术与死亡照护",
    "Dwayne Betts on Beauty, Prison, and Redaction": "普世母题 · 2023年度Top10/高墙之内的美与人格重塑",
    "Agnes Callard on Meaning": "人生母题 · 人生探索的终极目的与大学教育",
    "Agnes Callard on Anger": "人生母题 · 愤怒的本质与地位排名的心理冲动",
    "Bryan Caplan on the Case Against Education": "机制核心 · 教育的信号传递假说与真实产出",
    "Nassim Nicholas Taleb on Rationality, Risk, and Skin in the Game": "经典长青 · 切身利害(Stake)与真实世界的风险不对称",
    "Sam Quinones on Heroin, the Opioid Epidemic, and Dreamland": "成瘾与社会 · 芬太尼危机与阿片类成瘾的社会解体",
}

# Known user exclusions from Obsidian Vault (already listened / in active queue)
KNOWN_EXCLUSIONS = [
    "Addiction, Faith, and the Limits of Willpower",
    "Wild Problems",
    "In Defense of Intuition",
    "L.A. Paul on Vampires, Life Choices, and Transformation",
    "Kieran Setiya on Midlife",
    "Agnes Callard on Aspiration",
    "How Adam Smith Can Change Your Life",
    "Gary Greenberg on Depression, Addiction, and the Brain",
]


def strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    cleaned = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html_text, flags=re.DOTALL | re.IGNORECASE)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = html.unescape(cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:1500]


def extract_notes(item: ET.Element) -> str:
    encoded = item.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    if encoded is not None and encoded.text and encoded.text.strip():
        return strip_html(encoded.text)
    for child in item:
        if child.tag.endswith("encoded") and child.text and child.text.strip():
            return strip_html(child.text)

    desc = item.find("description")
    if desc is not None and desc.text and desc.text.strip():
        return strip_html(desc.text)

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


def build_episode_dict(item: ET.Element, feed_url: str, show: str) -> dict | None:
    enclosure = item.find("enclosure")
    if enclosure is None:
        return None
    enclosure_url = (enclosure.get("url") or "").strip()
    if not enclosure_url:
        return None
    enclosure_type = (enclosure.get("type") or "").strip()
    enclosure_length = (enclosure.get("length") or "").strip()

    pub_date_raw = (item.findtext("pubDate") or "").strip()
    if not pub_date_raw:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(pub_date_raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            dt = dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None

    published = dt.isoformat()
    title = (item.findtext("title") or "").strip()
    link = (item.findtext("link") or "").strip()

    guid_elem = item.find("guid")
    guid = (guid_elem.text or "").strip() if (guid_elem is not None and guid_elem.text) else enclosure_url

    ep_id = hashlib.sha1(f"{guid}|{feed_url}".encode("utf-8")).hexdigest()[:10]
    duration = extract_duration(item)
    notes = extract_notes(item)

    return {
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
    }


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
    recent_episodes = []
    classic_episodes = []
    seen_guids = set()

    is_econtalk = "econtalk" in show.lower() or "simplecast.com/wgl4xegl" in feed_url.lower()

    for item in items:
        title = (item.findtext("title") or "").strip()
        # Check if excluded
        if any(exc.lower() in title.lower() for exc in KNOWN_EXCLUSIONS):
            continue

        ep_data = build_episode_dict(item, feed_url, show)
        if not ep_data:
            continue

        guid = ep_data["guid"]
        if guid in seen_guids:
            continue

        dt = email.utils.parsedate_to_datetime(ep_data["pub_date"])
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        else:
            dt = dt.astimezone(datetime.timezone.utc)

        # 1. Check if matches recent cutoff
        if dt >= cutoff:
            recent_episodes.append(ep_data)
            seen_guids.add(guid)
            continue

        # 2. For EconTalk, check if matches curated classic archive targets
        if is_econtalk:
            matched_theme = None
            for target_title, theme_tag in ECONTALK_CURATED_TARGETS.items():
                if target_title.lower() in title.lower():
                    matched_theme = theme_tag
                    break
            if matched_theme:
                ep_data["theme_tag"] = matched_theme
                classic_episodes.append(ep_data)
                seen_guids.add(guid)

    recent_episodes.sort(key=lambda x: x["published"], reverse=True)
    selected_recent = recent_episodes[:per_feed]

    # Combine selected recent with curated classics
    combined = selected_recent + classic_episodes
    combined.sort(key=lambda x: x["published"], reverse=True)
    return show, combined


def main():
    parser = argparse.ArgumentParser(description="Fetch candidate podcast episodes.")
    parser.add_argument("--sources", default="sources.yaml", help="Path to sources YAML file")
    parser.add_argument("--out", default="candidates.json", help="Path to output JSON file")
    parser.add_argument("--days", type=int, default=60, help="Max age in days for fetched recent episodes (default 60)")
    parser.add_argument("--per-feed", type=int, default=20, help="Max recent episodes per feed (default 20)")
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
    cutoff_180 = now - datetime.timedelta(days=180)

    kept_old = []
    for ep in existing_episodes:
        if ep.get("id") in new_ids:
            continue
        # Preserve curated classic episodes even if older
        if ep.get("theme_tag"):
            kept_old.append(ep)
            continue
        pub_iso = ep.get("published", "")
        if pub_iso:
            try:
                pub_dt = datetime.datetime.fromisoformat(pub_iso)
                if pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=datetime.timezone.utc)
                if pub_dt < cutoff_180:
                    continue
            except Exception:
                pass
        kept_old.append(ep)

    merged = all_new_episodes + kept_old
    merged.sort(key=lambda x: x.get("published", ""), reverse=True)

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
    print(f"Successfully saved {len(merged)} episodes to {args.out}")


if __name__ == "__main__":
    main()

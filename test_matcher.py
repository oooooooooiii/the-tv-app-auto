import os
import sys
import json
import re
import time
from datetime import datetime, timezone, timedelta
import urllib.parse
import requests

FIREBASE_URL = os.getenv("FIREBASE_URL")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET")
SOURCE_API_URL = os.getenv("SOURCE_API_URL")

FIXED_DEMO_STREAM = "https://file.genoads.com/ch1.m3u8"
BACKUP_LOGO = "https://i.ibb.co/1Yh8PdLH/1000459066.jpg"

CATEGORY_SYNONYMS = {
    "soccer": ["soccer", "football", "efl", "cup", "premier", "league", "laliga", "serie", "bundesliga", "ligue", "ucl", "uel", "mls", "fifa", "uefa"],
    "american football": ["american football", "nfl", "cfb", "ncaaf", "ncaa football", "college football"],
    "basketball": ["basketball", "nba", "wnba", "euroleague", "ncaab"],
    "hockey": ["hockey", "nhl"],
    "baseball": ["baseball", "mlb"],
    "racing": ["racing", "f1", "formula", "nascar", "cup series"],
    "fighting": ["fighting", "boxing", "mma", "ufc", "wwe", "wrestling"]
}

def clean_text(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'\b(f\.?c\.?|c\.?f\.?|a\.?f\.?c\.?|s\.?c\.?)\b', ' ', text)
    text = re.sub(r'[\(\)\[\]\{\}\-–—_\.:;,/\\&]', ' ', text)
    tokens = text.split()
    stopwords = {"vs", "at", "the", "fc", "cf", "afc", "sc", "club", "team", "utd", "united", "city", "town", "athletic", "ath", "real", "de", "ac", "inter"}
    meaningful = [w for w in tokens if w not in stopwords]
    return " ".join(meaningful)

def are_categories_matching(cat1, cat2):
    c1 = clean_text(cat1)
    c2 = clean_text(cat2)
    if not c1 or not c2:
        return True
    if c1 == c2 or c1 in c2 or c2 in c1:
        return True
    for main_cat, syns in CATEGORY_SYNONYMS.items():
        c1_matched = any(s in c1 for s in syns)
        c2_matched = any(s in c2 for s in syns)
        if c1_matched and c2_matched:
            return True
    return False

def is_team_similar(team_a, team_b):
    t1 = clean_text(team_a)
    t2 = clean_text(team_b)
    if not t1 or not t2:
        return False
    if t1 == t2 or t1 in t2 or t2 in t1:
        return True
    w1 = set(t1.split())
    w2 = set(t2.split())
    overlap = w1.intersection(w2)
    if len(overlap) >= 1:
        for word in overlap:
            if len(word) >= 3:
                return True
    return False

def split_teams(event_name):
    if not event_name:
        return "", ""
    parts = re.split(r'\s+(?:vs|at|-)\s+', event_name, maxsplit=1, flags=re.I)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return event_name.strip(), event_name.strip()

def extract_raw_stream_url(link_str):
    if not link_str:
        return ""
    url_match = re.search(r'url=([^"\'\s&]+)', link_str)
    if url_match:
        return urllib.parse.unquote(url_match.group(1))
    direct_match = re.search(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', link_str)
    if direct_match:
        return direct_match.group(1)
    clean = re.sub(r'<[^>]+>', '', link_str).strip()
    return clean

def is_main_stream(link_url):
    match = re.search(r'playlist/(\d+)/', link_url)
    if match:
        stream_id = match.group(1)
        return len(stream_id) <= 6, stream_id
    return False, ""

def build_iframe_link(raw_stream_url):
    return f'<iframe src="https://ivan-player.vercel.app/?play=https://stream-proxy.goalzen.site/proxy/php?url={raw_stream_url}" style="width: 100%; aspect-ratio: 16/9; border: none;" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowfullscreen></iframe>'

def create_channel_entry(raw_stream_url):
    return {
        "link": build_iframe_link(raw_stream_url),
        "source": "tta",
        "title": "GOALZEN LIVE",
        "tokenApi": ""
    }

def run_sync():
    if not FIREBASE_URL or not FIREBASE_SECRET or not SOURCE_API_URL:
        print("[ERROR] Missing required secrets.")
        sys.exit(1)

    clean_fb_url = FIREBASE_URL.rstrip('/')
    db_endpoint = f"{clean_fb_url}/sports_live/events.json?auth={FIREBASE_SECRET}"

    print("[*] Fetching Database Events...")
    try:
        db_res = requests.get(db_endpoint, timeout=15)
        if db_res.status_code != 200:
            print(f"[!] Database request failed: {db_res.status_code}")
            sys.exit(1)
        raw_db = db_res.json()
        db_events = []
        max_index = -1
        if isinstance(raw_db, list):
            for idx, ev in enumerate(raw_db):
                if ev is not None:
                    db_events.append((idx, ev))
                    max_index = max(max_index, idx)
        elif isinstance(raw_db, dict):
            for k, ev in raw_db.items():
                if ev is not None:
                    parsed_k = int(k) if str(k).isdigit() else max_index + 1
                    db_events.append((parsed_k, ev))
                    max_index = max(max_index, parsed_k)
        print(f"[+] Found {len(db_events)} events in database.")
    except Exception as e:
        print(f"[ERROR] Failed to read database: {e}")
        sys.exit(1)

    print(f"[*] Fetching API from: {SOURCE_API_URL}...")
    try:
        api_res = requests.get(SOURCE_API_URL, timeout=15)
        if api_res.status_code != 200:
            print(f"[!] API request failed: {api_res.status_code}")
            sys.exit(1)
        api_json = api_res.json()
        api_matches = api_json.get("Live_Data", []) if isinstance(api_json, dict) else api_json
        print(f"[+] Loaded {len(api_matches)} matches from API.\n")
    except Exception as e:
        print(f"[ERROR] Failed to fetch API: {e}")
        sys.exit(1)

    updated_count = 0
    created_count = 0
    next_index = max_index + 1

    for api_match in api_matches:
        api_cat = api_match.get("Category") or api_match.get("League_Name") or "Sports"
        api_title = api_match.get("Event_Name") or api_match.get("Rivels") or ""
        api_team_a = api_match.get("Team_A") or api_match.get("Team A") or ""
        api_team_b = api_match.get("Team_B") or api_match.get("Team B") or ""

        if not api_team_a or not api_team_b:
            parsed_a, parsed_b = split_teams(api_title)
            api_team_a = api_team_a or parsed_a
            api_team_b = api_team_b or parsed_b

        api_raw_stream = extract_raw_stream_url(api_match.get("Link", ""))
        api_is_main, api_stream_id = is_main_stream(api_raw_stream)

        effective_stream = api_raw_stream if api_is_main else FIXED_DEMO_STREAM

        matched_db_event = None
        matched_db_idx = None

        for db_idx, db_event in db_events:
            event_info = db_event.get("eventInfo", {})
            db_cat = db_event.get("cat") or db_event.get("title") or ""
            db_team_a = event_info.get("teamA") or ""
            db_team_b = event_info.get("teamB") or ""
            db_event_name = event_info.get("eventName") or db_event.get("title") or ""

            if not are_categories_matching(api_cat, db_cat):
                continue

            direct_teams_match = (
                is_team_similar(api_team_a, db_team_a) and is_team_similar(api_team_b, db_team_b)
            ) or (
                is_team_similar(api_team_a, db_team_b) and is_team_similar(api_team_b, db_team_a)
            )

            fallback_title_match = is_team_similar(api_title, db_event_name)

            if direct_teams_match or fallback_title_match:
                matched_db_event = db_event
                matched_db_idx = db_idx
                break

        if matched_db_event is not None:
            channels = matched_db_event.get("channels_data")
            if not isinstance(channels, list):
                channels = matched_db_event.get("channels") or []
                if not isinstance(channels, list):
                    channels = []

            tta_index = -1
            placeholder_index = -1

            for ch_idx, ch in enumerate(channels):
                ch_link = ch.get("link", "")
                if "no.link" in ch_link:
                    placeholder_index = ch_idx
                if ch.get("source") == "tta" or "GOALZEN LIVE" in ch.get("title", ""):
                    tta_index = ch_idx

            should_update_db = False

            if placeholder_index != -1:
                channels[placeholder_index] = create_channel_entry(effective_stream)
                should_update_db = True
                print(f"[REPLACE] Event #{matched_db_idx} '{api_title}' -> Replaced 'no.link'")

            elif tta_index != -1:
                current_tta_link = channels[tta_index].get("link", "")
                cur_is_main, _ = is_main_stream(current_tta_link)

                if api_is_main and not cur_is_main:
                    channels[tta_index] = create_channel_entry(api_raw_stream)
                    should_update_db = True
                    print(f"[UPGRADE] Event #{matched_db_idx} '{api_title}' -> Upgraded Demo to Main ({api_stream_id})")

            else:
                if len(channels) == 0:
                    channels.append(create_channel_entry(effective_stream))
                    should_update_db = True
                    print(f"[SET] Event #{matched_db_idx} '{api_title}' -> Set stream")
                elif api_is_main:
                    channels.append(create_channel_entry(api_raw_stream))
                    should_update_db = True
                    print(f"[APPEND] Event #{matched_db_idx} '{api_title}' -> Appended Main Stream")

            if should_update_db:
                patch_url = f"{clean_fb_url}/sports_live/events/{matched_db_idx}/channels_data.json?auth={FIREBASE_SECRET}"
                resp = requests.put(patch_url, json=channels, timeout=10)
                if resp.status_code == 200:
                    matched_db_event["channels_data"] = channels
                    updated_count += 1
                else:
                    print(f"[!] Failed to update #{matched_db_idx}: {resp.status_code}")
        else:
            now_utc = datetime.now(timezone.utc)
            start_utc = api_match.get("UTC_Start") or now_utc.strftime("%Y/%m/%d %H:%M:%S +0000")
            end_utc = api_match.get("UTC_End") or (now_utc + timedelta(hours=3)).strftime("%Y/%m/%d %H:%M:%S +0000")

            team_a_final = api_team_a or api_match.get("League_Name") or "Team A"
            team_b_final = api_team_b or api_match.get("League_Name") or "Team B"
            team_a_logo = api_match.get("Team_A_Logo") or api_match.get("Team_Logo") or BACKUP_LOGO
            team_b_logo = api_match.get("Team_B_Logo") or api_match.get("Team_Logo") or BACKUP_LOGO
            league_logo = api_match.get("League_Logo") or team_a_logo or BACKUP_LOGO

            new_event_obj = {
                "cat": api_cat,
                "title": api_match.get("League_Name") or api_cat,
                "league_logo": league_logo,
                "id": int(time.time() * 1000) % 1000000000,
                "channels_data": [create_channel_entry(effective_stream)],
                "eventInfo": {
                    "Status": "Upcoming",
                    "eventName": api_title or f"{team_a_final} vs {team_b_final}",
                    "isHot": "0",
                    "startTime": start_utc,
                    "endTime": end_utc,
                    "teamA": team_a_final,
                    "teamAFlag": team_a_logo,
                    "teamB": team_b_final,
                    "teamBFlag": team_b_logo
                }
            }

            put_url = f"{clean_fb_url}/sports_live/events/{next_index}.json?auth={FIREBASE_SECRET}"
            put_resp = requests.put(put_url, json=new_event_obj, timeout=10)
            if put_resp.status_code == 200:
                print(f"[CREATED] Event #{next_index} '{api_title}' added as new match.")
                db_events.append((next_index, new_event_obj))
                next_index += 1
                created_count += 1
            else:
                print(f"[!] Failed to create #{next_index}: {put_resp.status_code}")

    print("\n" + "=" * 50)
    print("                SYNC REPORT SUMMARY                ")
    print("=" * 50)
    print(f"Total API Matches Processed : {len(api_matches)}")
    print(f"Total Existing Events Updated: {updated_count}")
    print(f"Total New Events Created     : {created_count}")
    print("=" * 50)

if __name__ == "__main__":
    run_sync()

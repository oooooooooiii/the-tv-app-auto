import os
import sys
import json
import re
import requests

FIREBASE_URL = os.getenv("FIREBASE_URL")
FIREBASE_SECRET = os.getenv("FIREBASE_SECRET")
SOURCE_API_URL = os.getenv("SOURCE_API_URL")

CATEGORY_SYNONYMS = {
    "soccer": ["soccer", "football", "efl cup", "premier league", "laliga", "serie a", "ucl"],
    "american football": ["american football", "nfl", "cfb", "ncaaf", "ncaa football"],
    "basketball": ["basketball", "nba", "wnba", "euroleague"],
    "hockey": ["hockey", "nhl"],
    "baseball": ["baseball", "mlb"],
    "racing": ["racing", "f1", "formula 1", "nascar", "nascar cup series"],
    "fighting": ["fighting", "boxing", "mma", "ufc", "wwe"]
}

def clean_text(text):
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r'[\(\)\[\]\{\}\-–—_\.:;,]', ' ', text)
    tokens = text.split()
    stopwords = {"vs", "at", "the", "fc", "club", "team"}
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
            if len(word) >= 4:
                return True
    return False

def classify_stream_link(raw_link):
    if not raw_link:
        return "NO_LINK", "None"
    match = re.search(r'playlist/(\d+)/', raw_link)
    if match:
        stream_id = match.group(1)
        if len(stream_id) <= 6:
            return "MAIN_5_DIGIT", stream_id
        else:
            return "DEMO_LONG_ID", stream_id
    return "STANDARD_URL", "N/A"

def run_test():
    if not FIREBASE_URL or not FIREBASE_SECRET or not SOURCE_API_URL:
        print("[ERROR] Missing GitHub Secrets: FIREBASE_URL, FIREBASE_SECRET, or SOURCE_API_URL.")
        sys.exit(1)

    print("=" * 60)
    print("           GOALZEN LIVE SYNC: TEST MATCHER REPORT           ")
    print("=" * 60)

    clean_fb_url = FIREBASE_URL.rstrip('/')
    db_endpoint = f"{clean_fb_url}/sports_live/events.json?auth={FIREBASE_SECRET}"

    try:
        print(f"[*] Fetching Database Events from Firebase...")
        db_res = requests.get(db_endpoint, timeout=15)
        if db_res.status_code != 200:
            print(f"[!] Database request failed with status: {db_res.status_code}")
            sys.exit(1)
        raw_db = db_res.json() or []
        db_events = []
        if isinstance(raw_db, list):
            for idx, ev in enumerate(raw_db):
                if ev:
                    db_events.append((idx, ev))
        elif isinstance(raw_db, dict):
            for k, ev in raw_db.items():
                if ev:
                    db_events.append((int(k) if str(k).isdigit() else k, ev))
        print(f"[+] Loaded {len(db_events)} events from Database.\n")
    except Exception as e:
        print(f"[ERROR] Could not load database: {e}")
        sys.exit(1)

    try:
        print(f"[*] Fetching Live Matches from API: {SOURCE_API_URL}...")
        api_res = requests.get(SOURCE_API_URL, timeout=15)
        if api_res.status_code != 200:
            print(f"[!] API request failed with status: {api_res.status_code}")
            sys.exit(1)
        api_data = api_res.json()
        api_matches = api_data.get("Live_Data", []) if isinstance(api_data, dict) else api_data
        print(f"[+] Loaded {len(api_matches)} matches from API.\n")
    except Exception as e:
        print(f"[ERROR] Could not load API: {e}")
        sys.exit(1)

    matched_count = 0
    unmatched_count = 0

    print("=" * 60)
    print("                    MATCH ANALYSIS                          ")
    print("=" * 60)

    for api_match in api_matches:
        api_cat = api_match.get("Category") or api_match.get("League_Name") or ""
        api_title = api_match.get("Event_Name") or api_match.get("Rivels") or ""
        api_team_a = api_match.get("Team_A") or api_match.get("Team A") or ""
        api_team_b = api_match.get("Team_B") or api_match.get("Team B") or ""
        api_link = api_match.get("Link") or ""
        api_link_type, api_stream_id = classify_stream_link(api_link)

        found_db_match = None
        found_db_index = None

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
                found_db_match = db_event
                found_db_index = db_idx
                break

        if found_db_match:
            matched_count += 1
            channels = found_db_match.get("channels_data") or found_db_match.get("channels") or []
            db_link_status = "EMPTY (No channels)"
            has_no_link = False
            existing_demo_found = False

            if channels:
                links_text = " ".join([ch.get("link", "") for ch in channels])
                if "#no.link" in links_text:
                    has_no_link = True
                    db_link_status = "CONTAINS 'no.link' (Needs Replace)"
                else:
                    demo_check = [ch for ch in channels if "playlist/" in ch.get("link", "") and len(re.search(r'playlist/(\d+)/', ch.get("link", "")).group(1)) > 6] if any("playlist/" in ch.get("link", "") for ch in channels) else []
                    if demo_check:
                        existing_demo_found = True
                        db_link_status = "CONTAINS DEMO LINK (Can be updated to Main)"
                    else:
                        db_link_status = f"HAS {len(channels)} VALID STREAM(S)"

            action_note = "APPEND NEW SERVER LINK"
            if has_no_link:
                action_note = "REPLACE '#no.link' WITH THIS API LINK"
            elif existing_demo_found and api_link_type == "MAIN_5_DIGIT":
                action_note = "UPDATE EXISTING DEMO LINK -> MAIN 5-DIGIT STREAM"

            print(f"\n[MATCHED] Database Event Index #{found_db_index}")
            print(f"  • DB Event       : {found_db_match.get('eventInfo', {}).get('eventName', 'N/A')}")
            print(f"  • DB Category    : {found_db_match.get('cat', 'N/A')}")
            print(f"  • API Match      : {api_title}")
            print(f"  • API Category   : {api_cat}")
            print(f"  • API Stream Type: {api_link_type} (Stream ID: {api_stream_id})")
            print(f"  • Current DB State: {db_link_status}")
            print(f"  • Action Planned : >> {action_note} <<")
            print("-" * 60)
        else:
            unmatched_count += 1
            print(f"\n[UNMATCHED - NEW EVENT]")
            print(f"  • API Match      : {api_title}")
            print(f"  • API Category   : {api_cat}")
            print(f"  • Teams          : {api_team_a} vs {api_team_b}")
            print(f"  • API Stream Type: {api_link_type} (Stream ID: {api_stream_id})")
            print(f"  • Action Planned : >> CREATE AS NEW EVENT IN DATABASE <<")
            print("-" * 60)

    print("\n" + "=" * 60)
    print("                    TEST SUMMARY                            ")
    print("=" * 60)
    print(f"Total API Matches Ingested : {len(api_matches)}")
    print(f"Total Database Events Scanned: {len(db_events)}")
    print(f"Total Matched Events         : {matched_count}")
    print(f"Total New (Unmatched) Events : {unmatched_count}")
    print("=" * 60)

if __name__ == "__main__":
    run_test()

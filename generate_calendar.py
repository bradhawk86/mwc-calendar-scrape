from datetime import datetime, timedelta, timezone
from rapidfuzz import fuzz
import hashlib
import json
import pytz
import requests

BASE_URL = "https://www.mountainwestcouncil.org"
API_URL = f"{BASE_URL}/cfroot/campsCMSWrapper.cfc"

TZ = pytz.timezone("America/Boise")
session = requests.Session()

NOW = datetime.now()

# Rolling windows
START_DATE = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
END_DATE = (NOW + timedelta(days=365)).strftime("%Y-%m-%d")

PAST_LIMIT = NOW - timedelta(days=365 * 5)
FUTURE_LIMIT = NOW + timedelta(days=365)

def now_utc():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

def event_uid(e):
    base = f"{e['title']}_{e['start_dt']}"
    return hashlib.md5(base.encode()).hexdigest()

def parse_cf_date(dt_str):
    try:
        return datetime.strptime(dt_str, "%a, %d %b %Y %H:%M:%S")
    except Exception:
        print("⚠️ Failed to parse:", dt_str)
        return None

def is_not_a_moved_event(old_event, new_events):
    for uid, new_event in new_events.items():
        old_title = old_event.get("title", "")
        new_title = new_event.get("title", "")
        if old_title and new_title:
            score = fuzz.ratio(old_title, new_title)
            if (score > 90.0):
                old_date = old_event.get("date", "")
                new_date = new_event.get("start_dt", "")
                window = timedelta(days=90)
                if old_date and new_date and (abs(new_date - old_date) <= window):
                    print(f"Detected moved event\nOld Title {old_title} New Title {new_title}\nOld DT {old_date} New DT {new_date}") 
                    return False
    return True
                

def fetch_events():
    params = {
        "method": "GetRemoteCMSEvents",
        "returnformat": "json",
        "CalendarStartDate": START_DATE,
        "CalendarEndDate": END_DATE,
        "StartDate": START_DATE,
        "SiteID": "127",
        "CampID": "123",
        "DistrictIDi": "0",
        "EventCategoryID": "0"
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
        "Referer": "https://www.mountainwestcouncil.org/calendar",
        "Accept": "application/json, text/javascript, */*; q=0.01"
    }

    session.get(f"{BASE_URL}/calendar", headers=headers)
    resp = session.get(API_URL, params=params, headers=headers, timeout=20)
    resp.raise_for_status()

    return resp.json()

# =============================
# FETCH EVENTS
# =============================

data = fetch_events()
events = []

for item in data:
    try:
        title = item.get("NAME", "").strip()

        start_str = item.get("STARTDATE")
        end_str = item.get("ENDDATE")

        if not title or not start_str:
            continue

        start_dt = parse_cf_date(start_str)
        end_dt = parse_cf_date(end_str) if end_str else None

        if not start_dt:
            continue

        if not end_dt or end_dt <= start_dt:
            end_dt = start_dt + timedelta(hours=1)

        location = item.get("LOCATION", "")
        desc = item.get("DESCRIPTION", "")
        event_url = item.get("SHORTURL", "")

        if event_url:
            event_url = f"{BASE_URL}{event_url}"
        else:
            event_url = f"{BASE_URL}/calendar"

        events.append({
            "title": title,
            "start_dt": start_dt,
            "end_dt": end_dt,
            "location": location,
            "description": desc,
            "url": event_url
        })

    except Exception as e:
        print("Skipped event:", e)

print(f"✅ Pulled {len(events)} events from API")

# =============================
# LOAD EXISTING
# =============================

def load_existing(filename="calendar.ics"):
    existing = {}
    try:
        with open(filename, "r") as f:
            content = f.read().split("BEGIN:VEVENT")[1:]

        for block in content:
            uid = None
            date = None
            title = None

            for line in block.split("\n"):
                if line.startswith("UID:"):
                    uid = line.replace("UID:", "").strip()
                if line.startswith("SUMMARY:"):
                    title = line.replace("SUMMARY:", "").strip()
                if "DTSTART" in line:
                    date = datetime.strptime(line.split(":")[1][:15], "%Y%m%dT%H%M%S")

            if uid:
                existing[uid] = {
                    "raw": block.strip(),
                    "date": date,
                    "title": title,
                }
    except:
        pass

    return existing

existing = load_existing()

# =============================
# MERGE + FILTER
# =============================

merged = {}

# Add new events
for e in events:
    uid = event_uid(e)
    merged[uid] = e

# Keep old ones
for uid, old in existing.items():
    if uid not in merged and is_not_a_moved_event(old, merged):
        merged[uid] = old

filtered = []

for uid, e in merged.items():
    if "raw" in e:
        dt = e['date']
    else:
        dt = e['start_dt']

    if PAST_LIMIT <= dt <= FUTURE_LIMIT:
        filtered.append((uid, e))

filtered.sort(key=lambda x: x[1]['date'] if "raw" in x[1] else x[1]['start_dt'])

# =============================
# WRITE ICS
# =============================

with open("calendar.ics", "w") as f:
    f.write("BEGIN:VCALENDAR\n")
    f.write("VERSION:2.0\n")
    f.write("PRODID:-//Mountain West Council//Events//EN\n")

    for uid, e in filtered:
        f.write("BEGIN:VEVENT\n")

        if "raw" in e:
            f.write(e['raw'] + "\n")
            continue

        f.write(f"UID:{uid}\n")
        f.write(f"DTSTAMP:{now_utc()}\n")
        
        start_local = TZ.localize(e['start_dt'])
        end_local = TZ.localize(e['end_dt'])

        # Convert to UTC
        start_dt = start_local.astimezone(timezone.utc)
        end_dt = end_local.astimezone(timezone.utc)

        f.write(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%SZ')}\n")
        f.write(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%SZ')}\n")

        f.write(f"SUMMARY:{e['title']}\n")

        if e.get("location"):
            f.write(f"LOCATION:{e['location']}\n")

        desc_parts = []

        if e.get("url"):
            desc_parts.append(e['url'].strip())

        if e.get("description"):
            desc_parts.append(e['description'].strip())

        if desc_parts:
            full_desc = "\\n\\n".join(desc_parts)
            f.write(f"DESCRIPTION:{full_desc}\n")

        f.write("END:VEVENT\n")

    f.write("END:VCALENDAR\n")

print(f"✅ Final ICS contains {len(filtered)} events")


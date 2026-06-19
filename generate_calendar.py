import requests
from datetime import datetime, timedelta
import hashlib
import pytz
import json

BASE_URL = "https://www.mountainwestcouncil.org"
API_URL = f"{BASE_URL}/cfroot/campsCMSWrapper.cfc"

TZ = pytz.timezone("America/Boise")
session = requests.Session()

NOW = datetime.now()

# Rolling windows
START_DATE = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")
END_DATE = (NOW + timedelta(days=365)).strftime("%Y-%m-%d")

START_DATE_2 = (NOW - timedelta(days=60)).strftime("%Y-%m-%d")

PAST_LIMIT = NOW - timedelta(days=365 * 5)
FUTURE_LIMIT = NOW + timedelta(days=365)

def now_utc():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

def event_uid(e):
    base = f"{e['title']}_{e['date']}"
    return hashlib.md5(base.encode()).hexdigest()

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

    resp = session.get(API_URL, params=params, headers=headers, timeout=20)

    if resp.status_code == 403:
        print("❌ Still blocked (403). Response:", resp.text[:300])

    resp.raise_for_status()

    print("RAW Resp")
    print(resp.text[:300])
    print("JSON")
    print(resp.json())
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

        if not start_str:
            continue

        start_dt = datetime.fromisoformat(start_str)
        end_dt = datetime.fromisoformat(end_str) if end_str else None

        location = item.get("LOCATION", "")
        desc = item.get("DESCRIPTION", "")

        events.append({
            "date": start_dt,
            "title": title,
            "url": BASE_URL,
            "details": {
                "start": start_dt.time(),
                "end": end_dt.time() if end_dt else None,
                "location": location,
                "desc": desc
            }
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
                    date = datetime.strptime(line.split(":")[1][:8], "%Y%m%d")

            if uid:
                existing[uid] = {
                    "raw": block.strip(),
                    "date": date,
                    "title": title
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
    if uid not in merged:
        merged[uid] = old

filtered = []

for uid, e in merged.items():
    dt = e["date"]

    if PAST_LIMIT <= dt <= FUTURE_LIMIT:
        filtered.append((uid, e))

filtered.sort(key=lambda x: x[1]["date"])

# =============================
# WRITE ICS
# =============================

with open("calendar.ics", "w") as f:
    f.write("BEGIN:VCALENDAR\n")
    f.write("VERSION:2.0\n")
    f.write("PRODID:-//Mountain West Council//Events//EN\n")

    for uid, e in filtered:
        f.write("BEGIN:VEVENT\n")
        f.write(f"UID:{uid}\n")
        f.write(f"DTSTAMP:{now_utc()}\n")

        if "raw" in e:
            f.write(e["raw"] + "\n")
            f.write("END:VEVENT\n")
            continue

        start = e["details"]["start"]
        end = e["details"]["end"]

        start_dt = TZ.localize(datetime.combine(e["date"], start))
        end_dt = TZ.localize(datetime.combine(e["date"], end or start))

        f.write(f"DTSTART:{start_dt.strftime('%Y%m%dT%H%M%S')}\n")
        f.write(f"DTEND:{end_dt.strftime('%Y%m%dT%H%M%S')}\n")

        f.write(f"SUMMARY:{e['title']}\n")

        if e["details"]["location"]:
            f.write(f"LOCATION:{e['details']['location']}\n")

        if e["details"]["desc"]:
            f.write(f"DESCRIPTION:{e['details']['desc']}\n")

        f.write("END:VEVENT\n")

    f.write("END:VCALENDAR\n")

print(f"✅ Final ICS contains {len(filtered)} events")


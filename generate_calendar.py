import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import re
import uuid
import hashlib
import pytz

BASE_URL = "https://www.mountainwestcouncil.org"
CAL_URL = f"{BASE_URL}/calendar"
TZ = pytz.timezone("America/Boise")

session = requests.Session()

NOW = datetime.now()
PAST_LIMIT = NOW - timedelta(days=365 * 5)
FUTURE_LIMIT = NOW + timedelta(days=365)


def now_utc():
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def event_uid(e):
    base = f"{e['title']}_{e['date']}"
    return hashlib.md5(base.encode()).hexdigest()


def event_hash(e):
    content = f"{e['title']}_{e['date']}_{e.get('details', {})}"
    return hashlib.md5(content.encode()).hexdigest()


def parse_time_range(text):
    match = re.search(
        r"(\\d{1,2}:?\\d{0,2}\\s*[AaPp][Mm])\\s*-\\s*(\\d{1,2}:?\\d{0,2}\\s*[AaPp][Mm])",
        text
    )
    if not match:
        return None, None

    def norm(t):
        return datetime.strptime(
            t.strip().upper(),
            "%I:%M %p" if ":" in t else "%I %p"
        ).time()

    try:
        return norm(match.group(1)), norm(match.group(2))
    except:
        return None, None


def parse_date(text):
    match = re.match(r"(\\d{1,2})\\s+([A-Z]{3})", text)
    if not match:
        return None

    d, m = match.groups()

    for year in (NOW.year, NOW.year + 1):
        try:
            return datetime.strptime(f"{d} {m} {year}", "%d %b %Y")
        except:
            continue
    return None


def extract_details(url):
    try:
        r = session.get(url, timeout=10)
        s = BeautifulSoup(r.text, "html.parser")
        text = s.get_text(" ", strip=True)

        start, end = parse_time_range(text)

        loc_match = re.search(r"Where:\\s*(.*?)(When:|$)", text)

        return {
            "start": start,
            "end": end,
            "location": loc_match.group(1).strip() if loc_match else "",
            "desc": text[:800]
        }

    except:
        return {}


def load_existing(filename="calendar.ics"):
    existing = {}

    try:
        with open(filename, "r") as f:
            blocks = f.read().split("BEGIN:VEVENT")[1:]

        for b in blocks:
            uid = re.search(r"UID:(.*)", b)
            dt = re.search(r"DTSTART(?:;VALUE=DATE)?:(\\d{8})", b)
            summ = re.search(r"SUMMARY:(.*)", b)
            mod = re.search(r"LAST-MODIFIED:(.*)", b)

            if uid and dt and summ:
                existing[uid.group(1).strip()] = {
                    "date": datetime.strptime(dt.group(1), "%Y%m%d"),
                    "title": summ.group(1).strip(),
                    "modified": mod.group(1).strip() if mod else None,
                    "raw": b.strip()
                }

    except:
        pass

    return existing


# =============================
# SCRAPE EVENTS
# =============================

resp = session.get(CAL_URL, timeout=15)
soup = BeautifulSoup(resp.text, "html.parser")

events = []

for a in soup.find_all("a", href=True):
    text = a.get_text(strip=True)

    m = re.match(r"(\\d{1,2}\\s+[A-Z]{3})", text)
    if not m:
        continue

    dt = parse_date(m.group(1))
    if not dt:
        continue

    title = text[len(m.group(1)):].strip()
    if not title:
        continue

    url = a["href"]
    if not url.startswith("http"):
        url = BASE_URL + url

    details = extract_details(url)

    events.append({
        "date": dt,
        "title": title,
        "url": url,
        "details": details
    })


# Deduplicate
seen = set()
clean = []
for e in events:
    key = (e["date"], e["title"])
    if key not in seen:
        clean.append(e)
        seen.add(key)

events = clean


# =============================
# MERGE WITH EXISTING
# =============================

existing = load_existing()

merged = {}

for e in events:
    uid = event_uid(e)
    merged[uid] = e

for uid, old in existing.items():
    if uid not in merged:
        merged[uid] = old


# =============================
# FILTER WINDOW
# =============================

filtered = []

for uid, e in merged.items():
    dt = e["date"] if isinstance(e, dict) else e["date"]

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
        now = now_utc()

        if "raw" in e:
            f.write("BEGIN:VEVENT\n")
            f.write(e["raw"] + "\n")
            continue

        existing_event = existing.get(uid)

        if existing_event:
            last_mod = existing_event["modified"]
        else:
            last_mod = now

        f.write("BEGIN:VEVENT\n")
        f.write(f"UID:{uid}\n")
        f.write(f"DTSTAMP:{now}\n")
        f.write(f"CREATED:{last_mod}\n")
        f.write(f"LAST-MODIFIED:{last_mod}\n")

        start = e["details"].get("start")
        end = e["details"].get("end")

        if start:
            sdt = TZ.localize(datetime.combine(e["date"], start))
            edt = TZ.localize(datetime.combine(e["date"], end or start))

            f.write(f"DTSTART:{sdt.strftime('%Y%m%dT%H%M%S')}\n")
            f.write(f"DTEND:{edt.strftime('%Y%m%dT%H%M%S')}\n")
        else:
            d1 = e["date"].strftime("%Y%m%d")
            d2 = (e["date"] + timedelta(days=1)).strftime("%Y%m%d")

            f.write(f"DTSTART;VALUE=DATE:{d1}\n")
            f.write(f"DTEND;VALUE=DATE:{d2}\n")

        f.write(f"SUMMARY:{e['title']}\n")

        if e["details"].get("location"):
            f.write(f"LOCATION:{e['details']['location']}\n")

        f.write(f"DESCRIPTION:{e['details'].get('desc','')}\\n{e['url']}\n")

        f.write("END:VEVENT\n")

    f.write("END:VCALENDAR\n")

print(f"✅ Calendar generated: {len(filtered)} events")

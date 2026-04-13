import os
import time
import json
import re
import threading
import requests
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException, TimeoutException

load_dotenv()

USERNAME = os.getenv("COLLEGE_USER")
PASSWORD = os.getenv("COLLEGE_PASS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT  = os.getenv("TG_CHAT")

if not USERNAME or not PASSWORD:
    raise EnvironmentError("COLLEGE_USER and COLLEGE_PASS must be set as environment variables.")

LOGIN_URL  = "https://arms.sse.saveetha.com/Login.aspx"
ENROLL_URL = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"

DATA_FILE  = "agent_memory.json"
STATS_FILE = "agent_stats.json"

EXCLUDED_SLOTS = {"E", "5"}

# ── Shared state ──────────────────────────────────────────
dashboard_state = {
    "last_updated": "Never",
    "courses": {},
    "log": []
}
state_lock = threading.Lock()

stats = {
    "last_check":     "Never",
    "session_alive":  False,
    "login_failures": 0,
    "alerts_today":   0,
    "alerts_date":    datetime.now().strftime("%Y-%m-%d"),
}


# ── Stats helpers ─────────────────────────────────────────

def load_stats():
    global stats
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE) as f:
                saved = json.load(f)
            today = datetime.now().strftime("%Y-%m-%d")
            if saved.get("alerts_date") != today:
                saved["alerts_today"] = 0
                saved["alerts_date"]  = today
            stats.update(saved)
        except Exception:
            pass

def save_stats():
    with open(STATS_FILE, "w") as f:
        json.dump({
            "last_check":     stats["last_check"],
            "login_failures": stats["login_failures"],
            "alerts_today":   stats["alerts_today"],
            "alerts_date":    stats["alerts_date"],
        }, f)

def increment_alerts():
    today = datetime.now().strftime("%Y-%m-%d")
    if stats["alerts_date"] != today:
        stats["alerts_today"] = 0
        stats["alerts_date"]  = today
    stats["alerts_today"] += 1
    save_stats()


# ── Smart cell parser ─────────────────────────────────────
# ARMS table columns (0-indexed):
#   0 = S.No  1 = Course Code  2 = Course Title  3 = Faculty  4 = Available Seats  5 = ...
# But sometimes cols shift. We detect robustly by scanning all cells.

def parse_row(cells):
    """
    Returns (code, title, faculty, seats) from a table row's cells.
    Uses regex to identify which cell is code and which is seats,
    then infers title and faculty from surrounding cells.
    """
    ct = [c.text.strip() for c in cells]
    if not any(ct):
        return None, None, None, 0

    code_idx  = None
    seats_idx = None

    for i, val in enumerate(ct):
        # Course code: letters followed by digits, 6+ chars e.g. ECA1401, SPIC5A07
        if re.match(r'^[A-Z]{2,}\d{2,}', val.upper()) and len(val) >= 5:
            if code_idx is None:
                code_idx = i
        # Seats: pure small number (0-999)
        if re.fullmatch(r'\d{1,3}', val):
            seats_idx = i

    if code_idx is None:
        return None, None, None, 0

    code  = ct[code_idx].split()[0].upper()   # take only first token in case of extra text
    seats = int(ct[seats_idx]) if seats_idx is not None else 0

    # Title is the cell right after code_idx (if it exists and isn't a number)
    title  = ""
    faculty = ""

    if code_idx + 1 < len(ct) and not re.fullmatch(r'\d+', ct[code_idx + 1]):
        title = ct[code_idx + 1]
    if code_idx + 2 < len(ct) and not re.fullmatch(r'\d+', ct[code_idx + 2]):
        faculty = ct[code_idx + 2]

    return code, title, faculty, seats


# ── Dashboard HTML ────────────────────────────────────────

def render_dashboard():
    with state_lock:
        courses      = dashboard_state["courses"]
        last_updated = dashboard_state["last_updated"]
        log          = dashboard_state["log"][-20:]

    rows = ""
    for code, info in sorted(courses.items()):
        seats   = info.get("seats", 0)
        title   = info.get("title", "-")
        faculty = info.get("faculty", "-")
        slot    = info.get("slot", "-")
        t       = info.get("time", "-")
        is_new  = info.get("is_new", False)

        badge   = (f'<span class="badge open">{seats} seats</span>'
                   if seats > 0 else '<span class="badge full">Full</span>')
        new_tag = '<span class="new-tag">NEW</span>' if is_new else ""

        rows += f"""
        <tr>
          <td>{slot}</td>
          <td><strong>{code}</strong> {new_tag}</td>
          <td>{title}</td>
          <td>{faculty}</td>
          <td>{badge}</td>
          <td>{t}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" class="empty">No courses scanned yet.</td></tr>'

    log_lines  = "".join(f"<div class='log-line'>{l}</div>" for l in reversed(log))
    alive_dot  = "🟢" if stats["session_alive"] else "🔴"
    stat_block = f"""
      <div class="stat-grid">
        <div class="stat">{alive_dot} Session alive: {"Yes" if stats["session_alive"] else "No"}</div>
        <div class="stat">🕒 Last check: {stats["last_check"]}</div>
        <div class="stat">⚠️ Login failures: {stats["login_failures"]}</div>
        <div class="stat">📬 Alerts today: {stats["alerts_today"]}</div>
        <div class="stat">🖥️ Server time: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div>
      </div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="60">
  <title>SSE Enrollment Monitor</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;padding:2rem}}
    h1{{font-size:1.8rem;font-weight:700;color:#38bdf8;margin-bottom:.3rem}}
    .subtitle{{color:#94a3b8;font-size:.9rem;margin-bottom:1.5rem}}
    .stat-grid{{display:flex;flex-wrap:wrap;gap:.75rem;margin-bottom:1.5rem}}
    .stat{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.5rem 1rem;font-size:.82rem;color:#cbd5e1}}
    .last-updated{{background:#1e293b;border:1px solid #334155;border-radius:8px;padding:.5rem 1rem;display:inline-block;font-size:.82rem;color:#94a3b8;margin-bottom:1.2rem}}
    .table-wrap{{overflow-x:auto}}
    table{{width:100%;border-collapse:collapse;background:#1e293b;border-radius:12px;overflow:hidden;box-shadow:0 4px 20px rgba(0,0,0,.4)}}
    thead{{background:#0f172a}}
    th{{padding:.9rem 1rem;text-align:left;font-size:.75rem;text-transform:uppercase;letter-spacing:.05em;color:#64748b}}
    td{{padding:.8rem 1rem;border-top:1px solid #334155;font-size:.88rem}}
    tr:hover td{{background:#263348}}
    .badge{{padding:.3rem .75rem;border-radius:999px;font-size:.78rem;font-weight:600}}
    .badge.open{{background:#064e3b;color:#34d399;border:1px solid #34d399}}
    .badge.full{{background:#3b0764;color:#c084fc;border:1px solid #c084fc}}
    .new-tag{{background:#1e3a5f;color:#60a5fa;border:1px solid #3b82f6;border-radius:4px;font-size:.7rem;padding:.1rem .4rem;margin-left:.4rem;font-weight:600}}
    .empty{{text-align:center;color:#475569;padding:2rem}}
    .log-section{{margin-top:2rem}}
    .log-section h2{{font-size:.85rem;color:#64748b;margin-bottom:.8rem;text-transform:uppercase;letter-spacing:.05em}}
    .log-box{{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:1rem 1.2rem;font-family:monospace;font-size:.78rem;color:#94a3b8;max-height:220px;overflow-y:auto}}
    .log-line{{padding:2px 0;border-bottom:1px solid #263348}}
    .footer{{margin-top:2rem;font-size:.75rem;color:#334155}}
  </style>
</head>
<body>
  <h1>📋 SSE Enrollment Monitor</h1>
  <p class="subtitle">Auto-refreshes every 60s · Scans every 5 min · Slots E &amp; 5 excluded</p>
  {stat_block}
  <div class="last-updated">🕒 Last scanned: {last_updated}</div>
  <div class="table-wrap">
    <table>
      <thead><tr><th>Slot</th><th>Code</th><th>Title</th><th>Faculty</th><th>Availability</th><th>Detected At</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="log-section">
    <h2>Agent Log</h2>
    <div class="log-box">{log_lines or "<div class='log-line'>No logs yet.</div>"}</div>
  </div>
  <p class="footer">Saveetha School of Engineering · ARMS Portal Monitor</p>
</body>
</html>"""


# ── Web server ────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        html = render_dashboard().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args):
        pass

def start_dashboard_server():
    port = int(os.getenv("PORT", 8080))
    HTTPServer(("0.0.0.0", port), DashboardHandler).serve_forever()


# ── Logging ───────────────────────────────────────────────

def add_log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    with state_lock:
        dashboard_state["log"].append(line)


# ── Telegram helpers ──────────────────────────────────────

def tg_send(text, chat_id=None):
    if not TG_TOKEN:
        add_log(f"(Telegram not configured) {text}")
        return
    cid = chat_id or TG_CHAT
    if not cid:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            data={"chat_id": cid, "text": text},
            timeout=10
        )
    except Exception as e:
        add_log(f"Telegram error: {e}")

def tg_get_updates(offset):
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 30},
            timeout=35
        )
        return r.json().get("result", [])
    except Exception:
        return []


# ── Driver builder ────────────────────────────────────────

def build_driver():
    opts = Options()
    opts.binary_location = "/usr/bin/google-chrome"
    opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1920,1080")
    return webdriver.Chrome(options=opts)


def login_driver(driver):
    """Login and return True on success, False on failure."""
    driver.get(LOGIN_URL)
    try:
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "txtusername")))
    except TimeoutException:
        return False
    driver.find_element(By.ID, "txtusername").send_keys(USERNAME)
    driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
    driver.find_element(By.ID, "btnlogin").click()
    try:
        WebDriverWait(driver, 15).until(EC.url_contains("Landing"))
    except TimeoutException:
        return False
    return True


def scan_all_slots(driver, query=None):
    """
    Scan all non-excluded slots.
    If query is set (str), only collect rows matching that query (case-insensitive).
    Returns dict: { code: {seats, title, faculty, slot, time} }
    """
    driver.get(ENROLL_URL)
    WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "cphbody_ddlslot")))

    sel   = Select(driver.find_element(By.ID, "cphbody_ddlslot"))
    slots = [o.get_attribute("value") for o in sel.options if o.get_attribute("value")]

    data = {}

    for slot in slots:
        if slot.strip().upper() in EXCLUDED_SLOTS:
            continue

        sel.select_by_value(slot)

        # Wait for table to refresh — wait for at least one <tr> with <td>s
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "tr td"))
            )
        except TimeoutException:
            time.sleep(1)

        slot_name = sel.first_selected_option.text.strip()

        for row in driver.find_elements(By.TAG_NAME, "tr"):
            cells = row.find_elements(By.TAG_NAME, "td")
            if len(cells) < 3:
                continue

            text = row.text.strip()
            if not text:
                continue

            # If doing a targeted search, skip rows that don't match
            if query and query.upper() not in text.upper():
                continue

            code, title, faculty, seats = parse_row(cells)
            if not code:
                continue

            entry = {
                "seats":   seats,
                "title":   title,
                "faculty": faculty,
                "slot":    slot_name,
                "time":    time.strftime("%H:%M:%S"),
                "is_new":  False,
            }

            # For auto-scan: keep highest seat count per code
            # For /check: keep all slot variants
            if query:
                key = f"{code}_{slot_name}"
                data[key] = {**entry, "code": code}
            else:
                if code not in data or seats > data[code]["seats"]:
                    data[code] = entry

    return data


# ── /check command ────────────────────────────────────────

def search_course(query_code, chat_id):
    query = query_code.strip().upper()
    add_log(f"/check: {query}")
    tg_send(f"🔍 Searching for: {query}\nPlease wait...", chat_id)

    driver = None
    try:
        driver = build_driver()
        if not login_driver(driver):
            tg_send("❌ Login failed during search.", chat_id)
            return

        results = scan_all_slots(driver, query=query)

    except Exception as e:
        tg_send(f"❌ Search error: {e}", chat_id)
        return
    finally:
        if driver:
            driver.quit()

    if not results:
        tg_send(
            f"🔍 Search: {query}\n{'─'*30}\n"
            f"❌ No courses found matching '{query}'.",
            chat_id
        )
        return

    items   = list(results.values())
    header  = (
        f"🔍 Search: {query}\n"
        f"{'─'*30}\n"
        f"📊 Found in {len(items)} slot(s)\n\n"
    )

    lines = []
    for r in items:
        avail = f"{r['seats']} seats" if r["seats"] > 0 else "Full 🔴"
        lines.append(
            f"📍 Slot: {r['slot']}\n"
            f"📚 Code: {r['code']}\n"
            f"📖 Title: {r['title'] or 'N/A'}\n"
            f"👩‍🏫 Faculty: {r['faculty'] or 'N/A'}\n"
            f"🪑 Vacancies: {avail}"
        )

    full_msg = header + "\n\n".join(lines)
    if len(full_msg) <= 4000:
        tg_send(full_msg, chat_id)
    else:
        tg_send(header + f"Sending {len(items)} results...", chat_id)
        for chunk in lines:
            tg_send(chunk, chat_id)
            time.sleep(0.3)

    add_log(f"/check {query} → {len(items)} result(s).")


# ── Telegram command polling ──────────────────────────────

def handle_status_command(chat_id):
    alive_str = "Yes ✅" if stats["session_alive"] else "No ❌"
    tg_send(
        f"Bot Status 🟢\n"
        f"{'─'*30}\n"
        f"🕒 Last check: {stats['last_check']}\n"
        f"🔑 Session alive: {alive_str}\n"
        f"⚠️ Login failures: {stats['login_failures']}\n"
        f"📬 Alerts today: {stats['alerts_today']}\n"
        f"🖥️ Server time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        chat_id
    )

def poll_telegram_commands():
    if not TG_TOKEN:
        return
    offset = 0
    add_log("Telegram polling started.")
    while True:
        try:
            updates = tg_get_updates(offset)
            for update in updates:
                offset  = update["update_id"] + 1
                msg     = update.get("message", {})
                text    = msg.get("text", "").strip()
                chat_id = msg.get("chat", {}).get("id")
                if not text or not chat_id:
                    continue
                lower = text.lower()

                if lower.startswith("/status"):
                    handle_status_command(chat_id)

                elif lower.startswith("/check"):
                    parts = text.split(maxsplit=1)
                    if len(parts) < 2 or not parts[1].strip():
                        tg_send("⚠️ Usage: /check <course_code>\nExample: /check CSA0712", chat_id)
                    else:
                        threading.Thread(
                            target=search_course,
                            args=(parts[1].strip(), chat_id),
                            daemon=True
                        ).start()

                elif lower.startswith("/help"):
                    tg_send(
                        "📖 Available Commands\n"
                        "─────────────────────\n"
                        "/status  — Bot health & stats\n"
                        "/check <code> — Search all slots for a course\n"
                        "   Example: /check CSA0712\n"
                        "   Example: /check ECA17\n\n"
                        "Auto-alerts fire only for newly released courses.",
                        chat_id
                    )
        except Exception as e:
            add_log(f"Poll error: {e}")
        time.sleep(2)


# ── Self-ping ─────────────────────────────────────────────

def self_ping():
    app_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not app_url:
        add_log("RENDER_EXTERNAL_URL not set, self-ping disabled.")
        return
    add_log(f"Self-ping started → {app_url}")
    while True:
        try:
            requests.get(app_url, timeout=10)
        except Exception:
            pass
        time.sleep(45)


# ── Enrollment Agent ──────────────────────────────────────

class EnrollmentAgent:

    def __init__(self):
        self.memory     = self.load_memory()
        self.first_run  = len(self.memory.get("seen_courses", {})) == 0

    def load_memory(self):
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE) as f:
                    return json.load(f)
            except Exception:
                pass
        return {"seen_courses": {}}

    def save_memory(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.memory, f, indent=2)

    def observe(self):
        add_log("Launching browser...")
        driver = build_driver()
        try:
            if not login_driver(driver):
                stats["login_failures"] += 1
                stats["session_alive"]   = False
                save_stats()
                raise Exception("Login failed.")

            stats["session_alive"] = True
            add_log(f"Logged in. URL: {driver.current_url}")

            data = scan_all_slots(driver)
            add_log(f"Scan complete. {len(data)} courses found.")
            return data
        finally:
            driver.quit()

    def check_and_notify(self, current_data):
        seen = self.memory.get("seen_courses", {})

        # ── First run: silently populate memory, no alerts ──
        if self.first_run:
            add_log(f"First run: seeding memory with {len(current_data)} courses. No alerts sent.")
            self.memory["seen_courses"] = current_data
            self.save_memory()
            self.first_run = False
            with state_lock:
                dashboard_state["courses"]      = current_data
                dashboard_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            stats["last_check"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
            save_stats()
            return

        new_courses    = []
        opened_courses = []

        for code, info in current_data.items():
            if code not in seen:
                info["is_new"] = True
                new_courses.append((code, info))
            else:
                info["is_new"] = False
                if seen[code].get("seats", 0) == 0 and info["seats"] > 0:
                    opened_courses.append((code, info))

        # ── New course alerts ──
        slot_summary = {}
        for code, info in new_courses:
            slot = info.get("slot", "Unknown")
            tg_send(
                f"🆕 NEW COURSE DETECTED\n"
                f"{'─'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title','N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty','N/A')}\n"
                f"🪑 Vacancies: {info.get('seats',0)}\n"
                f"🕐 Time: {info.get('time','N/A')}"
            )
            add_log(f"NEW: {code} | {slot} | {info.get('title','')}")
            increment_alerts()
            slot_summary[slot] = slot_summary.get(slot, 0) + 1

        if slot_summary:
            lines = "\n".join(f"{s}: {c}" for s, c in slot_summary.items())
            tg_send(f"📊 Cycle summary: {sum(slot_summary.values())} new course(s)\n{lines}")

        # ── Seats opened alerts ──
        for code, info in opened_courses:
            slot = info.get("slot", "Unknown")
            tg_send(
                f"🟢 SEATS NOW AVAILABLE\n"
                f"{'─'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title','N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty','N/A')}\n"
                f"🪑 Vacancies: {info.get('seats',0)}\n"
                f"🕐 Time: {info.get('time','N/A')}"
            )
            add_log(f"Seats opened: {code} | {slot}")
            increment_alerts()

        if not new_courses and not opened_courses:
            add_log("No new courses or changes.")

        # Merge — never forget seen courses across restarts
        self.memory["seen_courses"] = {**seen, **current_data}
        self.save_memory()

        stats["last_check"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        save_stats()

        with state_lock:
            dashboard_state["courses"]      = current_data
            dashboard_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    def run_loop(self):
        add_log("Agent started. Scanning every 5 minutes.")
        while True:
            try:
                add_log("Starting scan...")
                self.check_and_notify(self.observe())
            except Exception as e:
                add_log(f"Error: {e}")
            time.sleep(300)


# ── Entry point ───────────────────────────────────────────

if __name__ == "__main__":
    load_stats()
    threading.Thread(target=start_dashboard_server, daemon=True).start()
    threading.Thread(target=poll_telegram_commands, daemon=True).start()
    threading.Thread(target=self_ping,              daemon=True).start()
    EnrollmentAgent().run_loop()

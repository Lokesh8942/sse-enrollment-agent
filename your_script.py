import os
import time
import json
import re
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import NoSuchElementException

load_dotenv()

USERNAME = os.getenv("COLLEGE_USER")
PASSWORD = os.getenv("COLLEGE_PASS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT  = os.getenv("TG_CHAT")

if not USERNAME or not PASSWORD:
    raise EnvironmentError("COLLEGE_USER and COLLEGE_PASS must be set as environment variables.")

LOGIN_URL  = "https://arms.sse.saveetha.com/Login.aspx"
ENROLL_URL = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"

DATA_FILE = "agent_memory.json"

EXCLUDED_SLOTS = {"E", "5"}

# Shared state for dashboard
dashboard_state = {
    "last_updated": "Never",
    "courses": {},
    "log": []
}
state_lock = threading.Lock()


# ---------------- DASHBOARD HTML ----------------

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

        if seats > 0:
            badge = f'<span class="badge open">{seats} seats</span>'
        else:
            badge = '<span class="badge full">Full</span>'

        rows += f"""
        <tr>
          <td>{slot}</td>
          <td><strong>{code}</strong></td>
          <td>{title}</td>
          <td>{faculty}</td>
          <td>{badge}</td>
          <td>{t}</td>
        </tr>"""

    if not rows:
        rows = '<tr><td colspan="6" class="empty">No courses scanned yet. Check back in a moment.</td></tr>'

    log_lines = "".join(f"<div class='log-line'>{line}</div>" for line in reversed(log))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta http-equiv="refresh" content="60">
  <title>SSE Enrollment Monitor</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'Segoe UI', sans-serif;
      background: #0f172a;
      color: #e2e8f0;
      min-height: 100vh;
      padding: 2rem;
    }}
    h1 {{ font-size: 1.8rem; font-weight: 700; color: #38bdf8; margin-bottom: 0.3rem; }}
    .subtitle {{ color: #94a3b8; font-size: 0.9rem; margin-bottom: 2rem; }}
    .last-updated {{
      background: #1e293b; border: 1px solid #334155; border-radius: 8px;
      padding: 0.6rem 1rem; display: inline-block;
      font-size: 0.85rem; color: #94a3b8; margin-bottom: 1.5rem;
    }}
    .table-wrap {{ overflow-x: auto; }}
    table {{
      width: 100%; border-collapse: collapse;
      background: #1e293b; border-radius: 12px;
      overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.4);
    }}
    thead {{ background: #0f172a; }}
    th {{
      padding: 0.9rem 1rem; text-align: left;
      font-size: 0.75rem; text-transform: uppercase;
      letter-spacing: 0.05em; color: #64748b;
    }}
    td {{ padding: 0.8rem 1rem; border-top: 1px solid #334155; font-size: 0.88rem; }}
    tr:hover td {{ background: #263348; }}
    .badge {{
      padding: 0.3rem 0.75rem; border-radius: 999px;
      font-size: 0.78rem; font-weight: 600;
    }}
    .badge.open {{ background: #064e3b; color: #34d399; border: 1px solid #34d399; }}
    .badge.full {{ background: #3b0764; color: #c084fc; border: 1px solid #c084fc; }}
    .empty {{ text-align: center; color: #475569; padding: 2rem; }}
    .log-section {{ margin-top: 2rem; }}
    .log-section h2 {{
      font-size: 0.85rem; color: #64748b; margin-bottom: 0.8rem;
      text-transform: uppercase; letter-spacing: 0.05em;
    }}
    .log-box {{
      background: #1e293b; border: 1px solid #334155; border-radius: 12px;
      padding: 1rem 1.2rem; font-family: monospace; font-size: 0.78rem;
      color: #94a3b8; max-height: 220px; overflow-y: auto;
    }}
    .log-line {{ padding: 2px 0; border-bottom: 1px solid #263348; }}
    .footer {{ margin-top: 2rem; font-size: 0.75rem; color: #334155; }}
  </style>
</head>
<body>
  <h1>📋 SSE Enrollment Monitor</h1>
  <p class="subtitle">Auto-refreshes every 60s &nbsp;·&nbsp; Scans every 5 min &nbsp;·&nbsp; Slots E & 5 excluded</p>
  <div class="last-updated">🕒 Last scanned: {last_updated}</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Slot</th><th>Code</th><th>Title</th>
          <th>Faculty</th><th>Availability</th><th>Detected At</th>
        </tr>
      </thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
  <div class="log-section">
    <h2>Agent Log</h2>
    <div class="log-box">{log_lines if log_lines else "<div class='log-line'>No logs yet.</div>"}</div>
  </div>
  <p class="footer">Saveetha School of Engineering &nbsp;·&nbsp; ARMS Portal Monitor</p>
</body>
</html>"""


# ---------------- HEALTH SERVER ----------------

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
    server = HTTPServer(("0.0.0.0", port), DashboardHandler)
    print(f"Dashboard running on port {port}")
    server.serve_forever()


def add_log(msg):
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    with state_lock:
        dashboard_state["log"].append(line)


# ---------------- AGENT ----------------

class EnrollmentAgent:

    def __init__(self):
        self.memory = self.load_memory()

    # ---------------- MEMORY ----------------

    def load_memory(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {"seen_courses": {}}

    def save_memory(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.memory, f, indent=4)

    # ---------------- DRIVER ----------------

    def build_driver(self):
        options = Options()
        options.binary_location = "/usr/bin/google-chrome"
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        return webdriver.Chrome(options=options)

    # ---------------- TELEGRAM ----------------

    def send_alert(self, message):
        if not TG_TOKEN or not TG_CHAT:
            add_log(f"ALERT (Telegram not configured): {message}")
            return
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        try:
            requests.post(url, data={"chat_id": TG_CHAT, "text": message}, timeout=10)
            add_log("Telegram alert sent.")
        except Exception as e:
            add_log(f"Telegram error: {e}")

    # ---------------- OBSERVE ----------------

    def observe(self):
        add_log("Launching browser...")
        driver = self.build_driver()
        data = {}

        try:
            driver.get(LOGIN_URL)
            time.sleep(5)

            try:
                username_field = driver.find_element(By.ID, "txtusername")
            except NoSuchElementException:
                raise Exception("Login page structure changed or blocked.")

            username_field.send_keys(USERNAME)
            driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
            driver.find_element(By.ID, "btnlogin").click()
            time.sleep(5)

            add_log(f"Logged in. URL: {driver.current_url}")

            driver.get(ENROLL_URL)
            time.sleep(5)

            dropdown    = driver.find_element(By.ID, "cphbody_ddlslot")
            slot_select = Select(dropdown)

            slots = [
                option.get_attribute("value")
                for option in slot_select.options
                if option.get_attribute("value")
            ]

            add_log(f"Found {len(slots)} slots.")

            for slot in slots:
                if slot.strip().upper() in EXCLUDED_SLOTS:
                    add_log(f"Skipping Slot {slot}.")
                    continue

                slot_select.select_by_value(slot)
                time.sleep(3)

                slot_name = slot_select.first_selected_option.text.strip()

                rows = driver.find_elements(By.TAG_NAME, "tr")

                for row in rows:
                    cells = row.find_elements(By.TAG_NAME, "td")
                    if len(cells) < 3:
                        continue

                    text = row.text.strip()
                    if not text:
                        continue

                    # Extract course code
                    code = None
                    for part in text.split():
                        if re.match(r"^[A-Z]{2,}\d{3,}", part):
                            code = part
                            break

                    if not code:
                        continue

                    cell_texts = [c.text.strip() for c in cells]
                    title      = cell_texts[2] if len(cell_texts) > 2 else ""
                    faculty    = cell_texts[3] if len(cell_texts) > 3 else ""

                    numbers = re.findall(r"\d+", text)
                    seats   = int(numbers[-1]) if numbers else 0

                    entry = {
                        "seats":   seats,
                        "title":   title,
                        "faculty": faculty,
                        "slot":    slot_name,
                        "time":    time.strftime("%H:%M:%S"),
                    }

                    if code not in data or seats > data[code]["seats"]:
                        data[code] = entry

            add_log(f"Scan complete. {len(data)} courses found.")
            return data

        finally:
            driver.quit()

    # ---------------- COMPARE & NOTIFY ----------------

    def check_and_notify(self, current_data):
        seen = self.memory.get("seen_courses", {})

        new_courses    = []
        opened_courses = []

        for code, info in current_data.items():
            if code not in seen:
                new_courses.append((code, info))
            elif seen[code].get("seats", 0) == 0 and info["seats"] > 0:
                opened_courses.append((code, info))

        # --- New course alerts ---
        slot_summary = {}
        for code, info in new_courses:
            slot = info.get("slot", "Unknown")
            msg = (
                f"🆕 NEW COURSE DETECTED\n"
                f"{'—'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title', 'N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty', 'N/A')}\n"
                f"🪑 Vacancies: {info.get('seats', 0)}\n"
                f"🕐 Time: {info.get('time', 'N/A')}"
            )
            add_log(f"NEW course: {code} | {slot} | {info.get('title', '')}")
            self.send_alert(msg)
            slot_summary[slot] = slot_summary.get(slot, 0) + 1

        # Cycle summary per slot
        if slot_summary:
            summary_lines = "\n".join(f"{s}: {c}" for s, c in slot_summary.items())
            total = sum(slot_summary.values())
            summary_msg = f"📊 Cycle summary: {total} new course(s)\n{summary_lines}"
            self.send_alert(summary_msg)

        # --- Seats opened alerts ---
        for code, info in opened_courses:
            slot = info.get("slot", "Unknown")
            msg = (
                f"🟢 SEATS NOW AVAILABLE\n"
                f"{'—'*30}\n"
                f"📍 Slot: {slot}\n"
                f"📚 Code: {code}\n"
                f"📖 Title: {info.get('title', 'N/A')}\n"
                f"👩‍🏫 Faculty: {info.get('faculty', 'N/A')}\n"
                f"🪑 Vacancies: {info.get('seats', 0)}\n"
                f"🕐 Time: {info.get('time', 'N/A')}"
            )
            add_log(f"Seats opened: {code} | {slot}")
            self.send_alert(msg)

        if not new_courses and not opened_courses:
            add_log("No new courses or seat changes.")

        # Update memory and dashboard
        self.memory["seen_courses"] = current_data
        self.save_memory()

        with state_lock:
            dashboard_state["courses"]      = current_data
            dashboard_state["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")

    # ---------------- RUN LOOP ----------------

    def run_loop(self):
        add_log("Agent started. Scanning every 5 minutes.")
        while True:
            try:
                add_log("Starting scan...")
                current_data = self.observe()
                self.check_and_notify(current_data)
            except Exception as e:
                add_log(f"Error: {e}")

            time.sleep(300)


if __name__ == "__main__":
    threading.Thread(target=start_dashboard_server, daemon=True).start()

    agent = EnrollmentAgent()
    agent.run_loop()

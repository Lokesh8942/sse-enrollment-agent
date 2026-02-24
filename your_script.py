import os
import time
import json
import re
import requests
from datetime import datetime
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException

load_dotenv()

USERNAME = os.getenv("COLLEGE_USER")
PASSWORD = os.getenv("COLLEGE_PASS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT = os.getenv("TG_CHAT")

LOGIN_URL = "https://arms.sse.saveetha.com/Login.aspx"
ENROLL_URL = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"

DATA_FILE = "agent_memory.json"


class EnrollmentAgent:

    def __init__(self):
        self.prefix = "ECA47"
        self.memory = self.load_memory()

    # ---------------- MEMORY ----------------

    def load_memory(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {"known_courses": {}}

    def save_memory(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.memory, f, indent=4)

    # ---------------- DRIVER ----------------

    def build_driver(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--remote-debugging-port=9222")

        return webdriver.Chrome(options=options)

    # ---------------- OBSERVE ----------------

    def observe(self):
        print("Launching browser...")
        driver = self.build_driver()
        data = {}

        try:
            print("Opening login page...")
            driver.get(LOGIN_URL)

            time.sleep(5)

            print("Current URL:", driver.current_url)
            print("Page title:", driver.title)

            # Wait for login field
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "txtusername"))
            )

            print("Entering credentials...")
            driver.find_element(By.ID, "txtusername").send_keys(USERNAME)
            driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
            driver.find_element(By.ID, "btnlogin").click()

            WebDriverWait(driver, 20).until(
                EC.url_contains("StudentPortal")
            )

            print("Login successful.")

            driver.get(ENROLL_URL)

            dropdown_element = WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.ID, "cphbody_ddlslot"))
            )

            slot_select = Select(dropdown_element)

            slots = [
                option.get_attribute("value")
                for option in slot_select.options
                if option.get_attribute("value")
            ]

            print("Slots found:", slots)

            for slot in slots:
                print(f"Scanning Slot {slot}")
                slot_select.select_by_value(slot)
                time.sleep(3)

                rows = driver.find_elements(By.TAG_NAME, "tr")

                for row in rows:
                    text = row.text.strip()

                    if self.prefix in text:
                        parts = text.split()

                        code = None
                        for part in parts:
                            if part.startswith(self.prefix):
                                code = part
                                break

                        numbers = re.findall(r"\d+", text)

                        if code and numbers:
                            seats = int(numbers[-1])
                            data[code] = seats

            print("Observed data:", data)
            return data

        except TimeoutException:
            print("Timeout occurred while waiting for element.")
            raise

        finally:
            driver.quit()
            print("Browser closed.")

    # ---------------- REASON ----------------

    def reason(self, current_data):
        known = self.memory["known_courses"]

        new_courses = []
        seat_changes = []

        for code, seats in current_data.items():
            if code not in known:
                new_courses.append(code)
            else:
                if seats != known[code]:
                    seat_changes.append((code, seats))

        return new_courses, seat_changes

    # ---------------- ACT ----------------

    def act(self, new_courses, seat_changes):
        messages = []

        if new_courses:
            messages.append(
                "🚨 NEW COURSE RELEASED:\n" +
                ", ".join(new_courses)
            )

        for code, seats in seat_changes:
            messages.append(f"🔄 Seat Update: {code} → {seats}")

        if messages:
            message_text = "\n\n".join(messages)
            print("Sending Telegram alert...")
            self.send_telegram(message_text)
        else:
            print("No changes detected.")

    # ---------------- TELEGRAM ----------------

    def send_telegram(self, message):
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        response = requests.post(url, data={
            "chat_id": TG_CHAT,
            "text": message
        })
        print("Telegram response:", response.text)

    # ---------------- RUN ONCE ----------------

    def run_once(self):
        print("Agent execution started.")
        current_data = self.observe()

        new_courses, seat_changes = self.reason(current_data)

        self.memory["known_courses"] = current_data
        self.save_memory()

        self.act(new_courses, seat_changes)

        print("Agent execution finished.")


if __name__ == "__main__":
    agent = EnrollmentAgent()
    agent.run_once()

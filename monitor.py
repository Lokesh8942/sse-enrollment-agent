import os
import time
import json
import re
import random
import statistics
import requests
from datetime import datetime
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, NoSuchElementException

load_dotenv()

USERNAME = os.getenv("COLLEGE_USER")
PASSWORD = os.getenv("COLLEGE_PASS")
TG_TOKEN = os.getenv("TG_TOKEN")
TG_CHAT = os.getenv("TG_CHAT")

LOGIN_URL = "https://arms.sse.saveetha.com/Login.aspx"
ENROLL_URL = "https://arms.sse.saveetha.com/StudentPortal/Enrollment.aspx"

DATA_FILE = "agent_memory.json"


class IntelligentEnrollmentAgent:

    def __init__(self):
        self.prefix = "CSA07"
        self.base_interval = 120
        self.failure_count = 0
        self.memory = self.load_memory()

    # ================= MEMORY =================

    def load_memory(self):
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        return {
            "known_courses": {},
            "release_hours": [],
            "failures": []
        }

    def save_memory(self):
        with open(DATA_FILE, "w") as f:
            json.dump(self.memory, f, indent=4)

    # ================= DRIVER =================

    def build_driver(self):
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        return webdriver.Chrome(options=options)

    # ================= OBSERVE =================

    def observe(self):
        print("Launching browser...")
        driver = self.build_driver()
        data = {}

        try:
            print("Opening login page...")
            driver.get(LOGIN_URL)

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "txtusername"))
            )

            print("Entering credentials...")
            driver.find_element(By.ID, "txtusername").send_keys(USERNAME)
            driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
            driver.find_element(By.ID, "btnlogin").click()

            WebDriverWait(driver, 10).until(
                EC.url_contains("StudentPortal")
            )

            print("Login successful. Opening enrollment page...")
            driver.get(ENROLL_URL)

            dropdown_element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.ID, "cphbody_ddlslot"))
            )

            slot_select = Select(dropdown_element)

            slots = [
                option.get_attribute("value")
                for option in slot_select.options
                if option.get_attribute("value")
            ]

            print("Detected slots:", slots)

            for slot in slots:
                print(f"Scanning Slot {slot}...")
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

        finally:
            driver.quit()
            print("Browser closed.")

    # ================= REASON =================

    def reason(self, current_data):
        print("Reasoning phase...")
        known = self.memory["known_courses"]

        new_courses = []
        seat_updates = []

        for code, seats in current_data.items():
            if code not in known:
                print("New course detected:", code)
                new_courses.append(code)
                self.memory["release_hours"].append(datetime.now().hour)
            else:
                if seats != known[code]:
                    print(f"Seat change detected for {code}: {known[code]} -> {seats}")
                    seat_updates.append((code, seats))

        decision = {
            "new_courses": new_courses,
            "seat_updates": seat_updates
        }

        print("Decision:", decision)
        return decision

    # ================= ACT =================

    def act(self, decision):
        messages = []

        if decision["new_courses"]:
            messages.append(
                "🚨 NEW COURSE RELEASED:\n" +
                ", ".join(decision["new_courses"])
            )

        for code, seats in decision["seat_updates"]:
            messages.append(f"🔄 Seat Update: {code} → {seats}")

        if messages:
            message_text = "\n\n".join(messages)
            print("Sending Telegram alert...")
            self.send_telegram(message_text)

    # ================= ADAPT =================

    def adapt(self, success=True):
        if success:
            self.failure_count = 0
        else:
            self.failure_count += 1

        # Predictive acceleration
        if len(self.memory["release_hours"]) >= 3:
            try:
                common_hour = statistics.mode(self.memory["release_hours"])
                current_hour = datetime.now().hour

                if abs(current_hour - common_hour) <= 1:
                    print("Release window detected. Increasing frequency.")
                    return 30
            except:
                pass

        # Failure slowdown
        if self.failure_count >= 3:
            print("Repeated failures. Slowing down checks.")
            return min(self.base_interval + 60, 600)

        return self.base_interval + random.randint(0, 20)

    # ================= TELEGRAM =================

    def send_telegram(self, message):
        url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
        response = requests.post(url, data={
            "chat_id": TG_CHAT,
            "text": message
        })
        print("Telegram response:", response.text)

    # ================= RUN =================

    def run(self):
        while True:
            try:
                print("\n==============================")
                print("AGENT OBSERVING...")
                print("==============================")

                current_data = self.observe()

                decision = self.reason(current_data)

                self.memory["known_courses"] = current_data
                self.save_memory()

                self.act(decision)

                wait = self.adapt(success=True)

            except Exception as e:
                print("Failure occurred:", e)
                self.memory["failures"].append(str(e))
                self.save_memory()
                wait = self.adapt(success=False)

            print(f"Next check in {wait} seconds.")
            time.sleep(wait)


if __name__ == "__main__":
    agent = IntelligentEnrollmentAgent()
    agent.run()
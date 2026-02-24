import os
import time
import json
import re
import requests
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

            print("\n===== PAGE SOURCE (FIRST 1500 CHARS) =====")
            print(driver.page_source[:1500])
            print("==========================================\n")

            # Check manually if element exists
            try:
                username_field = driver.find_element(By.ID, "txtusername")
                print("Username field FOUND.")
            except NoSuchElementException:
                print("Username field NOT FOUND.")
                raise Exception("Login page structure changed or blocked.")

            print("Entering credentials...")
            username_field.send_keys(USERNAME)
            driver.find_element(By.ID, "txtpassword").send_keys(PASSWORD)
            driver.find_element(By.ID, "btnlogin").click()

            time.sleep(5)

            print("After login URL:", driver.current_url)
            print("After login title:", driver.title)

            driver.get(ENROLL_URL)
            time.sleep(5)

            dropdown = driver.find_element(By.ID, "cphbody_ddlslot")
            slot_select = Select(dropdown)

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

        finally:
            driver.quit()
            print("Browser closed.")

    # ---------------- RUN ONCE ----------------

    def run_once(self):
        print("Agent execution started.")
        current_data = self.observe()

        print("Execution completed.")
        print("Data collected:", current_data)


if __name__ == "__main__":
    agent = EnrollmentAgent()
    agent.run_once()

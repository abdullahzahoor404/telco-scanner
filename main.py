import time
import json
import os
import re
import pandas as pd
import gspread
import traceback
from google.oauth2.service_account import Credentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Telecom_Offers_Bot"  
JSON_KEYFILE = "service_account.json"

# --- AUTHENTICATION ---
def get_sheet_data():
    print("🔑 Authenticating with Google...")
    if not os.path.exists(JSON_KEYFILE):
        print("❌ CRITICAL: service_account.json file not found!")
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(JSON_KEYFILE, scopes=scopes)
        client = gspread.authorize(creds)
        print(f"📂 Opening Sheet: '{SHEET_NAME}'...")
        sheet = client.open(SHEET_NAME).sheet1
        print("✅ Connection Successful!")
        return sheet
    except Exception as e:
        print("❌ Connection Failed.")
        traceback.print_exc()
        return None

# --- BROWSER SETUP ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# --- SCROLL HELPER ---
def scroll_to_bottom(driver):
    print("   Scrolling page to load lazy elements...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(5):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height: break
        last_height = new_height

# --- SMART PARSER ---
def parse_offer_text(text_block):
    lines = [line.strip() for line in text_block.split('\n') if line.strip()]
    
    name = "Unknown Bundle"
    price = "N/A"
    details = []
    validity = "N/A"

    # Regex patterns
    price_pattern = re.compile(r'(Rs\.|PKR|Consumer Price|Incl\. Tax)', re.IGNORECASE)
    data_pattern = re.compile(r'(\d+\s*(GB|MB))', re.IGNORECASE)
    mins_pattern = re.compile(r'(\d+\s*Mins)', re.IGNORECASE)
    sms_pattern = re.compile(r'(\d+\s*SMS)', re.IGNORECASE)
    
    # 1. Extract Price
    for i, line in enumerate(lines):
        if price_pattern.search(line):
            price_match = re.search(r'[\d,]+', line)
            if price_match:
                price = price_match.group(0)
            else:
                price = line
            lines.pop(i) 
            break
            
    # 2. Extract Details & Validity
    filtered_lines = []
    for line in lines:
        is_detail = False
        if data_pattern.search(line):
            details.append(line)
            is_detail = True
        if mins_pattern.search(line):
            details.append(line)
            is_detail = True
        if sms_pattern.search(line):
            details.append(line)
            is_detail = True
        
        # Validity Guessing
        lower_line = line.lower()
        if "weekly" in lower_line: validity = "Weekly"
        elif "monthly" in lower_line: validity = "Monthly"
        elif "daily" in lower_line: validity = "Daily"
        elif "3 day" in lower_line: validity = "3 Days"

        if not is_detail:
            filtered_lines.append(line)

    # 3. Name Extraction (First valid line remaining)
    for line in filtered_lines:
        # Ignore common button texts
        if len(line) > 3 and "subscribe" not in line.lower() and "consumer price" not in line.lower():
            name = line
            break
            
    full_details = ", ".join(details) if details else "Check Site"
    return name, validity, full_details, price

# --- SCRAPERS ---

def scrape_zong(driver):
    print("🔹 Scraping Zong...")
    driver.get("https://www.zong.com.pk/prepaid")
    time.sleep(5)
    scroll_to_bottom(driver) # Added scrolling for Zong!
    
    offers = []
    try:
        # Simplified Zong Selector
        cards = driver.find_elements(By.XPATH, "//*[contains(text(), 'Consumer Price')]/../../..")
        
        for card in cards:
            try:
                name, validity, details, price = parse_offer_text(card.text)
                if name != "Unknown Bundle":
                    offers.append(["Zong", name, validity, details, price])
            except: continue
    except Exception as e:
        print(f"   Zong Error: {e}")
        
    print(f"   ✅ Found {len(offers)} Zong offers.")
    return offers

def scrape_jazz(driver):
    print("🔹 Scraping Jazz...")
    driver.get("https://jazz.com.pk/prepaid/all-in-one-offers")
    time.sleep(5)
    scroll_to_bottom(driver)
    
    offers = []
    try:
        # FIXED JAZZ SELECTOR: Uses '.' to check current node and children text
        # Checks for "SUBSCRIBE" or "MORE DETAILS"
        xpath = "//button[contains(., 'SUBSCRIBE') or contains(., 'Subscribe') or contains(., 'MORE DETAILS') or contains(., 'More Details')]/../../.."
        cards = driver.find_elements(By.XPATH, xpath)
        
        # Fallback if buttons aren't found, look for Prices
        if len(cards) == 0:
            print("   Using fallback selector for Jazz...")
            cards = driver.find_elements(By.XPATH, "//*[contains(text(), 'Rs.')]/../../..")

        for card in cards:
            try:
                name, validity, details, price = parse_offer_text(card.text)
                
                # Validity Fallback
                if validity == "N/A":
                    lower_name = name.lower()
                    if "weekly" in lower_name: validity = "Weekly"
                    elif "monthly" in lower_name: validity = "Monthly"
                    elif "daily" in lower_name: validity = "Daily"

                offers.append(["Jazz", name, validity, details, price])
            except: continue
    except Exception as e:
        print(f"   Jazz Error: {e}")
        
    print(f"   ✅ Found {len(offers)} Jazz offers.")
    return offers

# --- PROCESSING ---
def process_data(new_data, sheet):
    if not sheet: return []
    
    existing_records = sheet.get_all_records()
    df_old = pd.DataFrame(existing_records)
    
    processed_rows = []
    today = datetime.now().strftime("%Y-%m-%d")

    for row in new_data:
        operator, name, validity, details, price = row
        remark = "New Offer"
        
        if not df_old.empty and 'Offer Name' in df_old.columns:
            match = df_old[(df_old['Operator'] == operator) & (df_old['Offer Name'] == name)]
            
            if not match.empty:
                last_entry = match.iloc[-1]
                last_price = str(last_entry['Price']).strip()
                last_details = str(last_entry['Details']).strip()
                
                price_changed = last_price != str(price).strip()
                details_changed = last_details != str(details).strip()
                
                if not price_changed:
                    remark = "Same as yesterday"
                else:
                    changes = []
                    if price_changed: changes.append(f"Price: {last_price}->{price}")
                    if details_changed: changes.append("Details Updated")
                    remark = "Changed: " + ", ".join(changes)
        
        processed_rows.append([today, operator, name, validity, details, price, remark])
    
    return processed_rows

# --- MAIN ---
def main():
    print("--- STARTING BOT ---")
    sheet = get_sheet_data()
    if not sheet: return

    driver = get_driver()
    all_offers = []
    
    try: all_offers.extend(scrape_zong(driver))
    except Exception as e: print(f"Global Zong Fail: {e}")

    try: all_offers.extend(scrape_jazz(driver))
    except Exception as e: print(f"Global Jazz Fail: {e}")
    
    driver.quit()
    
    if all_offers:
        print(f"📝 Writing {len(all_offers)} rows to Google Sheets...")
        try:
            final_rows = process_data(all_offers, sheet)
            sheet.append_rows(final_rows)
            print("🎉 SUCCESS: Data written to sheet.")
        except Exception as e:
            print(f"❌ Error writing to sheet: {e}")
    else:
        print("⚠️ Scrapers finished but found 0 offers.")

if __name__ == "__main__":
    main()

import time
import json
import os
import pandas as pd
import gspread
import traceback
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
    
    # Check if JSON file exists
    if not os.path.exists(JSON_KEYFILE):
        print("❌ CRITICAL: service_account.json file not found!")
        return None
        
    try:
        # The 'service_account' method handles scopes and tokens automatically
        gc = gspread.service_account(filename=JSON_KEYFILE)
        
        # Open the sheet
        print(f"📂 Opening Sheet: '{SHEET_NAME}'...")
        sheet = gc.open(SHEET_NAME).sheet1
        print("✅ Connection Successful!")
        return sheet
    except gspread.exceptions.SpreadsheetNotFound:
        print(f"❌ ERROR: Spreadsheet '{SHEET_NAME}' not found.")
        print("   -> Make sure you shared the sheet with the 'client_email' inside your JSON file.")
        print("   -> Make sure the Sheet Name in this code matches your actual Google Sheet Name exactly.")
        return None
    except Exception as e:
        print("❌ Connection Failed.")
        print("-" * 30)
        traceback.print_exc()  # This prints the REAL error details
        print("-" * 30)
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
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- SCROLL HELPER ---
def scroll_to_bottom(driver):
    print("   Scrolling page...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(5):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height:
            break
        last_height = new_height

# --- SCRAPERS ---
def scrape_zong(driver):
    print("🔹 Scraping Zong...")
    driver.get("https://www.zong.com.pk/prepaid")
    time.sleep(5)
    
    offers = []
    try:
        # Strategy: Look for specific price format "PKR. 500.00 Consumer Price"
        # We find elements with "Consumer Price" and work backwards
        price_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Consumer Price')]")
        
        for price_el in price_elements:
            try:
                raw_price = price_el.text.replace("Consumer Price", "").strip()
                # Parent of Parent usually holds the whole card
                card = price_el.find_element(By.XPATH, "./../../..")
                text_lines = card.text.split('\n')
                
                name = text_lines[0]
                validity = "Weekly" if "Weekly" in name else ("Monthly" if "Monthly" in name else "Daily")
                
                offers.append(["Zong", name, validity, "Check Site", raw_price])
            except:
                continue
    except Exception as e:
        print(f"   Zong Error: {e}")
        
    print(f"   ✅ Found {len(offers)} Zong offers.")
    return offers

def scrape_jazz(driver):
    print("🔹 Scraping Jazz...")
    driver.get("https://jazz.com.pk/prepaid/prepaid-bundles")
    time.sleep(5)
    scroll_to_bottom(driver)
    
    offers = []
    try:
        # Strategy: Look for 'MORE DETAILS' buttons which are distinct to Jazz cards
        buttons = driver.find_elements(By.XPATH, "//*[contains(text(), 'MORE DETAILS') or contains(text(), 'SUBSCRIBE')]")
        
        for btn in buttons:
            try:
                card = btn.find_element(By.XPATH, "./../../..") 
                card_text = card.text
                
                lines = card_text.split('\n')
                name = lines[0]
                
                price = "N/A"
                for line in lines:
                    if "Rs." in line or "Incl. Tax" in line:
                        price = line.strip()
                        break
                
                if price != "N/A":
                    offers.append(["Jazz", name, "N/A", "Check Site", price])
            except:
                continue
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
                last_price = str(match.iloc[-1]['Price']).strip()
                if last_price == str(price).strip():
                    remark = "Same"
                else:
                    remark = f"Changed: {last_price} -> {price}"
        
        processed_rows.append([today, operator, name, validity, details, price, remark])
    
    return processed_rows

# --- MAIN ---
def main():
    print("--- STARTING BOT ---")
    
    # 1. Connect first
    sheet = get_sheet_data()
    if not sheet:
        return # Stop if auth fails

    driver = get_driver()
    all_offers = []
    
    # 2. Run Scrapers
    try: all_offers.extend(scrape_zong(driver))
    except Exception as e: print(f"Global Zong Fail: {e}")

    try: all_offers.extend(scrape_jazz(driver))
    except Exception as e: print(f"Global Jazz Fail: {e}")
    
    driver.quit()
    
    # 3. Save
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

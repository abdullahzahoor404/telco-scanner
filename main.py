import time
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Telecom_Offers_Bot"  
JSON_KEYFILE = "service_account.json"

# --- GOOGLE SHEETS AUTH ---
def get_sheet_data():
    try:
        scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
        creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        return sheet
    except Exception as e:
        print(f"❌ Connection Error: {e}")
        return None

# --- BROWSER SETUP ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/90.0.4430.212 Safari/537.36")
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- SCROLL HELPER ---
def scroll_to_bottom(driver):
    print("   Scrolling page to load lazy elements...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(3):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
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
    # Zong currently uses structure: "PKR. 2100.00 Consumer Price"
    # We look for the container that holds this text
    try:
        # Find all elements containing "Consumer Price"
        price_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Consumer Price')]")
        
        for price_el in price_elements:
            try:
                # The text usually looks like: "PKR. 550.00 Consumer Price"
                raw_price = price_el.text.replace("Consumer Price", "").strip()
                
                # Go up 3-4 parents to find the whole card
                card = price_el.find_element(By.XPATH, "./../../..")
                card_text = card.text.split('\n')
                
                # Usually the first line of the card is the name
                name = card_text[0]
                if len(name) < 3: name = card_text[1] # Safety check
                
                validity = "Weekly" if "Weekly" in name else ("Monthly" if "Monthly" in name else "Daily")
                
                offers.append(["Zong", name, validity, "Check Site", raw_price])
            except:
                continue
    except Exception as e:
        print(f"   Zong Error: {e}")
        
    print(f"   Found {len(offers)} Zong offers.")
    return offers

def scrape_jazz(driver):
    print("🔹 Scraping Jazz...")
    driver.get("https://jazz.com.pk/prepaid/prepaid-bundles")
    time.sleep(5)
    scroll_to_bottom(driver)
    
    offers = []
    try:
        # Jazz prices usually contain "Incl. Tax"
        # We look for ANY element containing "Incl. Tax"
        price_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Incl. Tax')]")
        
        for price_el in price_elements:
            try:
                raw_price = price_el.text.strip()
                
                # Go up parents to find the wrapper
                card = price_el.find_element(By.XPATH, "./../../..") 
                
                # Jazz card text structure varies, but name is usually at top
                text_lines = card.text.split('\n')
                name = text_lines[0]
                
                offers.append(["Jazz", name, "N/A", "Check Site", raw_price])
            except:
                continue
    except Exception as e:
        print(f"   Jazz Error: {e}")
        
    print(f"   Found {len(offers)} Jazz offers.")
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
        
        # Check against old data if it exists
        if not df_old.empty and 'Offer Name' in df_old.columns:
            match = df_old[(df_old['Operator'] == operator) & (df_old['Offer Name'] == name)]
            if not match.empty:
                last_price = match.iloc[-1]['Price']
                if str(last_price) == str(price):
                    remark = "Same"
                else:
                    remark = f"Changed: {last_price} -> {price}"
        
        processed_rows.append([today, operator, name, validity, details, price, remark])
    
    return processed_rows

# --- MAIN ---
def main():
    print("--- STARTING BOT ---")
    driver = get_driver()
    sheet = get_sheet_data()
    
    all_offers = []
    
    # Run scrapers safely
    try: all_offers.extend(scrape_zong(driver))
    except Exception as e: print(f"Global Zong Fail: {e}")

    try: all_offers.extend(scrape_jazz(driver))
    except Exception as e: print(f"Global Jazz Fail: {e}")
    
    driver.quit()
    
    if all_offers and sheet:
        print(f"📝 Writing {len(all_offers)} rows to Google Sheets...")
        final_rows = process_data(all_offers, sheet)
        sheet.append_rows(final_rows)
        print("✅ SUCCESS: Data written.")
    else:
        print("⚠️ No data found or Sheet missing.")

if __name__ == "__main__":
    main()

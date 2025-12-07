import time
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Telecom_Offers_Bot"  # Name of your Google Sheet
JSON_KEYFILE = "service_account.json"

# --- GOOGLE SHEETS AUTH ---
def get_sheet_data():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
    client = gspread.authorize(creds)
    sheet = client.open(SHEET_NAME).sheet1
    return sheet

# --- BROWSER SETUP ---
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options

def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    
    # This automatically handles the driver installation
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- SCRAPING FUNCTIONS ---
# Note: Selectors (class names) change frequently. You must inspect element on each site to update these.

def scrape_zong(driver):
    print("Scraping Zong...")
    driver.get("https://www.zong.com.pk/prepaid")
    time.sleep(5) # Wait for JS to load
    soup = BeautifulSoup(driver.page_source, 'html.parser')
    offers = []
    
    # Example logic - You must update specific class names based on inspection
    cards = soup.find_all('div', class_='offer-card') # Placeholder class
    for card in cards:
        try:
            name = card.find('h4').text.strip()
            price = card.find('span', class_='price').text.strip()
            details = card.find('p', class_='details').text.strip()
            validity = "Weekly" if "Weekly" in name else "Monthly" # Simple deduction
            offers.append(["Zong", name, validity, details, price])
        except:
            continue
    return offers

def scrape_jazz(driver):
    print("Scraping Jazz...")
    driver.get("https://jazz.com.pk/prepaid/prepaid-bundles")
    time.sleep(5)
    # Similar logic to Zong...
    return [] 

# Add similar functions for Telenor, Ufone, Onic, Rox...
# Note: Rox and Onic are Single Page Apps, ensure time.sleep() is sufficient or use WebDriverWait

# --- COMPARISON LOGIC ---
def process_data(new_data, sheet):
    existing_records = sheet.get_all_records()
    df_old = pd.DataFrame(existing_records)
    
    processed_rows = []
    today = datetime.now().strftime("%Y-%m-%d")

    for row in new_data:
        operator, name, validity, details, price = row
        
        # Check if offer existed yesterday
        remark = "New Offer"
        if not df_old.empty:
            match = df_old[(df_old['Operator'] == operator) & (df_old['Offer Name'] == name)]
            if not match.empty:
                last_price = match.iloc[-1]['Price']
                if str(last_price) == str(price):
                    remark = "Same as before"
                else:
                    remark = f"Price changed: {last_price} -> {price}"
        
        processed_rows.append([today, operator, name, validity, details, price, remark])
    
    return processed_rows

# --- MAIN EXECUTION ---
def main():
    driver = get_driver()
    sheet = get_sheet_data()
    
    all_offers = []
    
    # Run scrapers (Wrap in try-except to prevent one failure stopping all)
    try: all_offers.extend(scrape_zong(driver))
    except Exception as e: print(f"Zong failed: {e}")
    
    # ... Call other scrape functions here ...

    driver.quit()
    
    if all_offers:
        final_rows = process_data(all_offers, sheet)
        # Append to Sheet
        sheet.append_rows(final_rows)
        print("Sheet updated successfully.")
    else:
        print("No offers found.")

if __name__ == "__main__":
    main()

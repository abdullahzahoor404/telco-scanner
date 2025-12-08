import time
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from datetime import datetime

# --- CONFIGURATION ---
SHEET_NAME = "Telecom_Offers_Bot"  
JSON_KEYFILE = "service_account.json"

# --- GOOGLE SHEETS AUTH ---
def get_sheet_data():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name(JSON_KEYFILE, scope)
    client = gspread.authorize(creds)
    try:
        sheet = client.open(SHEET_NAME).sheet1
        return sheet
    except Exception as e:
        print(f"Error opening sheet: {e}")
        return None

# --- BROWSER SETUP ---
def get_driver():
    chrome_options = Options()
    chrome_options.add_argument("--headless") 
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080") # Important for some sites to load layout correctly
    
    service = Service(ChromeDriverManager().install())
    driver = webdriver.Chrome(service=service, options=chrome_options)
    return driver

# --- HELPER: SCROLL TO BOTTOM ---
# Many sites (like Jazz/Onic) only load offers when you scroll down
def scroll_page(driver):
    lenOfPage = driver.execute_script("window.scrollTo(0, document.body.scrollHeight);var lenOfPage=document.body.scrollHeight;return lenOfPage;")
    match=False
    while(match==False):
        lastCount = lenOfPage
        time.sleep(2)
        lenOfPage = driver.execute_script("window.scrollTo(0, document.body.scrollHeight);var lenOfPage=document.body.scrollHeight;return lenOfPage;")
        if lastCount==lenOfPage:
            match=True

# --- SCRAPING FUNCTIONS ---

def scrape_zong(driver):
    print("Scraping Zong...")
    driver.get("https://www.zong.com.pk/prepaid")
    time.sleep(5)
    
    offers = []
    # Zong Strategy: Find all elements that look like cards
    # We look for containers that have "Rs" (Price) and specific text
    try:
        # This XPath looks for any div containing 'Rs.' 
        cards = driver.find_elements(By.XPATH, "//div[contains(text(), 'Rs.') or contains(., 'Rs.')]/..")
        
        for card in cards:
            text = card.text.split('\n')
            # Heuristic: If text block is too short, it's not an offer.
            if len(text) > 3: 
                name = text[0] # Usually the first line is the name
                price = "N/A"
                for line in text:
                    if "Rs." in line or "PKR" in line:
                        price = line
                
                validity = "N/A"
                if "Weekly" in name: validity = "Weekly"
                elif "Monthly" in name: validity = "Monthly"
                elif "Daily" in name: validity = "Daily"
                
                offers.append(["Zong", name, validity, "See Website", price])
    except Exception as e:
        print(f"Error scraping Zong: {e}")
        
    return offers

def scrape_jazz(driver):
    print("Scraping Jazz...")
    driver.get("https://jazz.com.pk/prepaid/prepaid-bundles")
    time.sleep(3)
    scroll_page(driver) # Jazz loads on scroll
    
    offers = []
    try:
        # Jazz Strategy: Look for "Subscribe" buttons and get parent container
        buttons = driver.find_elements(By.XPATH, "//button[contains(text(), 'Subscribe') or contains(text(), 'Rs')]")
        
        for btn in buttons:
            try:
                # Go up 2-3 levels to get the full card
                card = btn.find_element(By.XPATH, "./../../..") 
                text = card.text
                
                lines = text.split('\n')
                name = lines[0] if lines else "Unknown Bundle"
                price = "N/A"
                for line in lines:
                    if "Rs" in line:
                        price = line
                        break
                
                offers.append(["Jazz", name, "Check Site", text[:50] + "...", price])
            except:
                continue
    except Exception as e:
        print(f"Error scraping Jazz: {e}")
        
    return offers

def scrape_telenor(driver):
    print("Scraping Telenor...")
    driver.get("https://www.telenor.com.pk/personal/telenor/offers/")
    time.sleep(5)
    offers = []
    try:
        # Telenor often uses standard cards. We look for 'Rs' text blocks.
        cards = driver.find_elements(By.CLASS_NAME, "offer-box") # Common class, might need update
        if not cards:
             cards = driver.find_elements(By.XPATH, "//div[contains(@class, 'package') or contains(@class, 'offer')]")
        
        for card in cards:
            text = card.text.split('\n')
            if len(text) > 2:
                offers.append(["Telenor", text[0], "N/A", "N/A", text[-1]])
    except Exception as e:
        print(f"Error scraping Telenor: {e}")
    return offers

# --- COMPARISON & SAVE ---
def process_data(new_data, sheet):
    if not sheet: return
    
    existing_records = sheet.get_all_records()
    df_old = pd.DataFrame(existing_records)
    
    processed_rows = []
    today = datetime.now().strftime("%Y-%m-%d")

    for row in new_data:
        operator, name, validity, details, price = row
        
        # Clean price string
        price = str(price).replace('\n', '').strip()
        
        remark = "New Offer"
        if not df_old.empty and 'Offer Name' in df_old.columns:
            match = df_old[(df_old['Operator'] == operator) & (df_old['Offer Name'] == name)]
            if not match.empty:
                last_price = match.iloc[-1]['Price']
                if str(last_price) == str(price):
                    remark = "Same as before"
                else:
                    remark = f"Price changed: {last_price} -> {price}"
        
        processed_rows.append([today, operator, name, validity, details, price, remark])
    
    return processed_rows

# --- MAIN ---
def main():
    driver = get_driver()
    sheet = get_sheet_data()
    
    all_offers = []
    
    # We use try/except blocks so if one site fails, the others still run
    try: all_offers.extend(scrape_zong(driver))
    except: print("Zong Failed")
    
    try: all_offers.extend(scrape_jazz(driver))
    except: print("Jazz Failed")

    try: all_offers.extend(scrape_telenor(driver))
    except: print("Telenor Failed")
    
    # Add other scrapers (Ufone, Onic, Rox) similarly...

    driver.quit()
    
    if all_offers and sheet:
        # Convert processed_rows to list of lists for appending
        final_rows = process_data(all_offers, sheet)
        sheet.append_rows(final_rows)
        print(f"Successfully added {len(final_rows)} rows.")
    else:
        print("No offers found or Sheet not accessible.")

if __name__ == "__main__":
    main()

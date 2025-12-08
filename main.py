import time
import json
import os
import pandas as pd
import gspread
import traceback
import google.generativeai as genai
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

# --- SETUP GEMINI AI (SMART SELECTOR) ---
def setup_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ CRITICAL: GEMINI_API_KEY not found in secrets!")
        return None
    
    print("🔑 Configuring Gemini API...")
    genai.configure(api_key=api_key)
    
    # Dynamic Model Selection: Ask Google what is available
    target_model_name = None
    try:
        print("🔎 Listing available models for your Key...")
        for m in genai.list_models():
            # We only care about models that can 'generateContent'
            if 'generateContent' in m.supported_generation_methods:
                print(f"   - Available: {m.name}")
                # Prefer Flash (faster/free), then Pro
                if 'gemini-1.5-flash' in m.name:
                    target_model_name = m.name
                elif 'gemini-1.5-pro' in m.name and not target_model_name:
                    target_model_name = m.name
        
        if target_model_name:
            print(f"👉 Automatically selected: {target_model_name}")
            return genai.GenerativeModel(target_model_name)
        else:
            # Fallback if list fails but key works
            print("⚠️ Could not auto-detect, forcing 'gemini-1.5-flash'...")
            return genai.GenerativeModel('gemini-1.5-flash')
            
    except Exception as e:
        print(f"❌ Error listing models: {e}")
        return None

# --- AUTHENTICATION ---
def get_sheet_data():
    print("🔑 Authenticating with Google Sheets...")
    if not os.path.exists(JSON_KEYFILE):
        print("❌ CRITICAL: service_account.json file not found!")
        return None
    try:
        scopes = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        creds = Credentials.from_service_account_file(JSON_KEYFILE, scopes=scopes)
        client = gspread.authorize(creds)
        sheet = client.open(SHEET_NAME).sheet1
        print("✅ Sheet Connection Successful!")
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

# --- HELPER: Scroll & Extract Text ---
def get_page_content(driver, url):
    print(f"   Navigating to {url}...")
    driver.get(url)
    time.sleep(5) 
    
    print("   Scrolling to load all offers...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(5):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height: break
        last_height = new_height
        
    body_text = driver.find_element(By.TAG_NAME, "body").text
    return body_text

# --- AI PARSER ---
def parse_with_gemini(model, operator_name, raw_text):
    print(f"🤖 Asking Gemini to extract {operator_name} offers...")
    
    prompt = f"""
    You are a data extraction bot. I will give you the raw text from the {operator_name} website.
    Your job is to find all the Telecom Bundles/Offers in the text.
    
    Rules:
    1. Extract: Offer Name, Price (include Currency), Details (Data, Mins, SMS), and Validity.
    2. Validity: If not explicitly stated, infer it from the name (e.g., "Weekly" = "Weekly").
    3. Output strictly as a JSON list of objects.
    4. Format: [{{"name": "...", "price": "...", "validity": "...", "details": "..."}}, ...]
    5. Do not add markdown formatting (like ```json). Just the raw JSON string.
    
    Here is the raw text:
    {raw_text[:30000]} 
    """
    
    try:
        response = model.generate_content(prompt)
        cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
        data = json.loads(cleaned_text)
        print(f"   ✅ Gemini found {len(data)} offers.")
        return data
    except Exception as e:
        print(f"   ❌ Gemini Error: {e}")
        return []

# --- MAIN EXECUTION ---
def main():
    print("--- STARTING AI BOT ---")
    
    # 1. Setup
    model = setup_gemini()
    sheet = get_sheet_data()
    
    # If model failed (key error) or sheet failed, stop.
    if not model: 
        print("❌ STOPPING: Gemini Model could not be loaded.")
        return
    if not sheet: 
        print("❌ STOPPING: Google Sheet could not be loaded.")
        return

    driver = get_driver()
    all_rows = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    # 2. Define Sites to Scrape
    sites = [
        {"name": "Zong", "url": "[https://www.zong.com.pk/prepaid](https://www.zong.com.pk/prepaid)"},
        {"name": "Jazz", "url": "[https://jazz.com.pk/prepaid/all-in-one-offers](https://jazz.com.pk/prepaid/all-in-one-offers)"},
    ]

    # 3. Loop through sites
    for site in sites:
        try:
            print(f"🔹 Processing {site['name']}...")
            raw_text = get_page_content(driver, site['url'])
            offers = parse_with_gemini(model, site['name'], raw_text)
            
            for offer in offers:
                all_rows.append([
                    today,
                    site['name'],
                    offer.get('name', 'N/A'),
                    offer.get('validity', 'N/A'),
                    offer.get('details', 'N/A'),
                    offer.get('price', 'N/A'),
                    "AI Extracted" 
                ])
                
        except Exception as e:
            print(f"   ❌ Failed to process {site['name']}: {e}")

    driver.quit()
    
    # 4. Save
    if all_rows:
        print(f"📝 Writing {len(all_rows)} rows to Google Sheets...")
        try:
            sheet.append_rows(all_rows)
            print("🎉 SUCCESS: Data written.")
        except Exception as e:
            print(f"❌ Error writing to sheet: {e}")
    else:
        print("⚠️ No data found.")

if __name__ == "__main__":
    main()

import time
import json
import os
import re
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
# We use the specific model visible in your dashboard
MODEL_NAME = 'gemini-2.5-flash' 

# --- SETUP GEMINI AI ---
def setup_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ CRITICAL: GEMINI_API_KEY not found in secrets!")
        return None
    
    print("🔑 Configuring Gemini API...")
    genai.configure(api_key=api_key)
    print(f"👉 Force Selecting Model: {MODEL_NAME}")
    return genai.GenerativeModel(MODEL_NAME)

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
    chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=chrome_options)

# --- HELPER: Scroll & Extract Text ---
def get_page_content(driver, url):
    # SAFETY: Clean URL
    clean_url = url.replace("[", "").replace("]", "").split("(")[0].strip()
    if clean_url.startswith("http") and ")" in url:
        clean_url = url.split("(")[-1].replace(")", "")
    if not clean_url.startswith("http"):
        clean_url = url.strip()

    print(f"   Navigating to: {clean_url}")
    driver.get(clean_url)
    time.sleep(5) 
    
    print("   Scrolling to load all offers...")
    last_height = driver.execute_script("return document.body.scrollHeight")
    for i in range(5):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(2)
        new_height = driver.execute_script("return document.body.scrollHeight")
        if new_height == last_height: break
        last_height = new_height
        
    body_text = driver.find_element(By.TAG_NAME, "body").text
    return body_text

# --- AI PARSER ---
def parse_with_gemini(model, operator_name, raw_text):
    print(f"🤖 Asking Gemini to extract {operator_name} offers...")
    
    if len(raw_text) < 500:
        print("   ⚠️ Text too short! Scraper might have been blocked.")
        return []

    prompt = f"""
    I am giving you the raw text content of the {operator_name} website. 
    Your task is to extracting a list of Telecom Bundles/Offers.

    RAW TEXT STARTS HERE:
    {raw_text[:40000]}
    RAW TEXT ENDS HERE.

    INSTRUCTIONS:
    1. Look for patterns like "Monthly", "Weekly", "GB", "Mins", "Rs.", "PKR".
    2. Extract: Offer Name, Price, Details (Data/Mins), Validity.
    3. Return ONLY a JSON list. No markdown. No explanations.
    4. If no offers are found, return exactly: []
    
    JSON FORMAT EXAMPLE:
    [
        {{"name": "Super Weekly", "price": "Rs. 250", "validity": "Weekly", "details": "10GB Data"}},
        {{"name": "Monthly Max", "price": "PKR 1000", "validity": "Monthly", "details": "20GB, 500 Mins"}}
    ]
    """
    
    # SAFETY SETTINGS: Disable filters to prevent "Blocked" errors
    safety_config = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt, safety_settings=safety_config)
            
            try:
                result_text = response.text
            except Exception:
                print(f"   ⚠️ API returned no text (Finish Reason: {response.candidates[0].finish_reason if response.candidates else 'Unknown'}).")
                return []

            cleaned_text = result_text.replace("```json", "").replace("```", "").strip()
            
            if not cleaned_text.startswith("["):
                start = cleaned_text.find("[")
                end = cleaned_text.rfind("]")
                if start != -1 and end != -1:
                    cleaned_text = cleaned_text[start:end+1]
                else:
                    print(f"   ⚠️ Bad JSON format from AI. Retrying...")
                    continue 

            data = json.loads(cleaned_text)
            print(f"   ✅ Gemini found {len(data)} offers.")
            return data
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Quota" in error_msg:
                # YOUR LIMIT IS 5 RPM (1 request every 12 seconds).
                # We wait 20 seconds to be safe.
                print(f"   ⚠️ Quota Hit! Waiting 20s... ({attempt+1}/{max_retries})")
                time.sleep(20)
            else:
                print(f"   ❌ Parsing Error: {e}")
                return []
    
    return []

# --- MAIN EXECUTION ---
def main():
    print("--- STARTING AI BOT ---")
    
    model = setup_gemini()
    sheet = get_sheet_data()
    
    if not model or not sheet: 
        print("❌ STOPPING: Setup failed.")
        return

    driver = get_driver()
    all_rows = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    sites = [
        {"name": "Zong", "url": "https://www.zong.com.pk/prepaid"},
        {"name": "Jazz", "url": "https://jazz.com.pk/prepaid/all-in-one-offers"},
    ]

    for i, site in enumerate(sites):
        try:
            # SAFETY DELAY:
            # Your account allows 5 requests per minute.
            # We enforce a 15-second delay between every site scan.
            if i > 0: 
                print("   ⏳ Waiting 15s to respect API Rate Limits...")
                time.sleep(15)

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

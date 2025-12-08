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

# --- SETUP GEMINI AI ---
def setup_gemini():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("❌ CRITICAL: GEMINI_API_KEY not found in secrets!")
        return None
    
    print("🔑 Configuring Gemini API...")
    genai.configure(api_key=api_key)
    
    # CHANGED: We now prioritize 1.5 Flash because it has the best Free Tier limits
    priority_models = [
        'gemini-1.5-flash',       # STABLE & FREE (Best choice)
        'gemini-1.5-pro',         # Good fallback
        'gemini-flash-latest',    
        'gemini-pro'              
    ]
    
    target_model_name = None
    try:
        print("🔎 Listing available models...")
        # Get all models that support generating content
        all_models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # Check priority list
        for priority in priority_models:
            for available in all_models:
                if priority in available and "exp" not in available: # Avoid experimental models if possible
                    target_model_name = available
                    break
            if target_model_name: break
        
        # Fallback
        if not target_model_name and all_models:
            target_model_name = all_models[0]

        if target_model_name:
            print(f"👉 Selected Model: {target_model_name}")
            return genai.GenerativeModel(target_model_name)
        else:
            print("⚠️ Could not auto-detect. Forcing 'gemini-1.5-flash'...")
            return genai.GenerativeModel('gemini-1.5-flash')
            
    except Exception as e:
        print(f"❌ Error selecting model: {e}")
        return genai.GenerativeModel('gemini-1.5-flash')

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
    # SAFETY: Remove any markdown brackets
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

# --- AI PARSER (WITH RETRY LOGIC) ---
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
    
    # Retry Loop: Tries 3 times if Quota Exceeded
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            cleaned_text = response.text.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned_text)
            print(f"   ✅ Gemini found {len(data)} offers.")
            return data
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg or "Quota exceeded" in error_msg:
                wait_time = 20 # Wait 20 seconds
                print(f"   ⚠️ Quota Hit! Waiting {wait_time}s before retry ({attempt+1}/{max_retries})...")
                time.sleep(wait_time)
            else:
                print(f"   ❌ Gemini Error: {e}")
                return []
    
    return []

# --- MAIN EXECUTION ---
def main():
    print("--- STARTING AI BOT ---")
    
    # 1. Setup
    model = setup_gemini()
    sheet = get_sheet_data()
    
    if not model or not sheet: 
        print("❌ STOPPING: Setup failed.")
        return

    driver = get_driver()
    all_rows = []
    today = datetime.now().strftime("%Y-%m-%d")
    
    sites = [
        {"name": "Zong", "url": "[https://www.zong.com.pk/prepaid](https://www.zong.com.pk/prepaid)"},
        {"name": "Jazz", "url": "[https://jazz.com.pk/prepaid/all-in-one-offers](https://jazz.com.pk/prepaid/all-in-one-offers)"},
    ]

    for site in sites:
        try:
            print(f"🔹 Processing {site['name']}...")
            raw_text = get_page_content(driver, site['url'])
            
            # Helper to pause between sites to save quota
            if len(all_rows) > 0: 
                print("   Sleeping 10s to be safe...")
                time.sleep(10)

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

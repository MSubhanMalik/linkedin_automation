from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import Select
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks
from pydantic import BaseModel
import uvicorn
import requests
import logging
import re
from typing import Dict, Optional
from datetime import datetime, timedelta
import threading
import time
import random
import openai
from openai import OpenAI
import base64
import json
from gologin import GoLogin


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LinkedIn Account Creator API",
    description="API for creating LinkedIn accounts and handling email verification",
    version="1.0.0"
)

class LinkedInAccountRequest(BaseModel):
    first_name: str
    last_name: str
    google_email: str
    linkedin_password: str

    class Config:
        schema_extra = {
            "example": {
                "first_name": "John",
                "last_name": "Doe",
                "google_email": "john.doe@gmail.com",
                "linkedin_password": "SecurePass123!",
                "profile_id": "your_gologin_profile_id"
            }
        }

class LinkedInResponse(BaseModel):
    status: str
    message: str
    email: Optional[str] = None

class MailgunWebhookResponse(BaseModel):
    status: str
    message: str
    pin: Optional[str] = None
    body: Optional[str] = None


def store_verification_pin(pin: str):
    """Store the verification PIN in a file"""
    try:
        with open('output/verification_pin.txt', 'w') as f:
            f.write(pin)
        logger.info(f"Stored verification PIN: {pin}")
    except Exception as e:
        logger.error(f"Error storing PIN: {str(e)}")

def get_verification_pin() -> str:
    """Get the stored verification PIN"""
    try:
        with open('output/verification_pin.txt', 'r') as f:
            pin = f.read().strip()
        logger.info(f"Retrieved verification PIN: {pin}")
        return pin
    except FileNotFoundError:
        logger.warning("No verification PIN found")
        return ""
    except Exception as e:
        logger.error(f"Error reading PIN: {str(e)}")
        return ""

def clear_verification_pin():
    """Clear the stored verification PIN"""
    try:
        with open('output/verification_pin.txt', 'w') as f:
            f.write("")
        logger.info("Cleared verification PIN file")
    except Exception as e:
        logger.error(f"Error clearing PIN: {str(e)}")

def init_gologin_driver(profile_id: str = None):
    """Initialize GoLogin driver with profile using standard selenium"""
    try:
        # Initialize GoLogin
        gl = GoLogin({
            "token": "your_go_login_token",
            })
        profile_id = "your_profile_id"
        gl.setProfileId(profile_id)
        # Add proxy to the profile
        gl.addGologinProxyToProfile(profile_id, "us")

        # Start Browser and get websocket url
        debugger_address = gl.start()

        # Get Chromium version for webdriver
        chromium_version = gl.get_chromium_version()


        # Install webdriver
        service = Service(ChromeDriverManager(driver_version=chromium_version).install())

        chrome_options = webdriver.ChromeOptions()
        chrome_options.add_experimental_option("debuggerAddress", debugger_address)

        driver = webdriver.Chrome(service=service, options=chrome_options)

        logger.info(f"GoLogin driver initialized with profile: {profile_id}")
        return driver
        
    except Exception as e:
        logger.error(f"Error initializing GoLogin driver: {str(e)}")
        raise e
OPENAI_API_KEY = "api_key"

def analyze_screenshot_with_openai(screenshot_path: str) -> str:
    """Analyze screenshot with OpenAI o4 mini model and return the correct answer"""
    try:
        # Initialize OpenAI client
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # Encode the image to base64
        with open(screenshot_path, "rb") as image_file:
            encoded_image = base64.b64encode(image_file.read()).decode('utf-8')
        
        # Create the message for OpenAI
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "This is a CAPTCHA verification screen. Look at the image and tell me the correct answer which image is correctly upwards. The image must be correctly straight up like someone is standing up. In which image the animal is correct way up. Only answer if you are 90 percent confident. There are 6 images, three in first row and three in second row. Images in first row are 1, 2, 3 and images in second row are 4, 5, 6. Return ONLY a JSON object with the format: {\"answer\": \"number\"} where number is the correct answer (1, 2, 3, 4, etc.). Do not include any other text or explanation."
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{encoded_image}"
                            }
                        }
                    ]
                }
            ],
            max_tokens=50
        )
        
        # Extract the answer from the response
        answer_text = response.choices[0].message.content.strip()
        logger.info(f"OpenAI response: {answer_text}")
        
        # Parse JSON response
        try:
            answer_data = json.loads(answer_text)
            return answer_data.get("answer", "")
        except json.JSONDecodeError:
            # If JSON parsing fails, try to extract number from text
            import re
            number_match = re.search(r'(\d+)', answer_text)
            if number_match:
                return number_match.group(1)
            return ""
            
    except Exception as e:
        logger.error(f"Error analyzing screenshot with OpenAI: {str(e)}")
        return ""

def random_delay(min_delay=1, max_delay=3):
    """Add a random delay between actions to simulate human behavior"""
    delay = random.uniform(min_delay, max_delay)
    time.sleep(delay)

def simulate_human_typing(driver: webdriver.Chrome, element, text: str):
    """Simulate human-like typing with variable delays"""
    for char in text:
        element.send_keys(char)
        random_delay(0.15, 0.25)
def click_and_focus(driver: webdriver.Chrome, selector: str, by: By = By.CSS_SELECTOR):
    """Click and focus on an element with human-like behavior"""
    wait = WebDriverWait(driver, 10)
    element = wait.until(EC.element_to_be_clickable((by, selector)))
    actions = ActionChains(driver)
    actions.move_to_element(element)
    random_delay(1, 2)
    actions.click(element)
    actions.perform()
    random_delay(1, 2)
    return element

    
def handle_phone_verification(driver: webdriver.Chrome):
    """Handle phone verification using Selenium WebDriver"""
    phone_numbers = [
        # (phone_number, rental_code)
    ]
    # If you want to add more numbers, add tuples to the list above
    random.shuffle(phone_numbers)  # For future extensibility
    for phone_number, rental_code in phone_numbers:
        try:
            wait = WebDriverWait(driver, 10)
            country_select = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "select#select-register-phone-country")))
            select_country = Select(country_select)
            select_country.select_by_value("us")
            random_delay(1, 2)
            phone_field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[name='phoneNumber']")))
            click_and_focus(driver, "[name='phoneNumber']")
            phone_field.clear()
            simulate_human_typing(driver, phone_field, phone_number)
            random_delay(1, 2)
            click_and_focus(driver, "[type='submit']")
            random_delay(2, 4)
            time.sleep(5)
            try:
                pin_field = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "[name='pin']")))
            except Exception:
                logger.warning("PIN input not found after submitting phone. Retrying with next number if available.")
                continue 
            logger.info("Waiting for SMS verification PIN...")
            max_wait_time = 60
            start_time = time.time()
            pin = ""
            while time.time() - start_time < max_wait_time:
                api_key = "your_sms_api_key"
                pin = get_sms_pin_from_smspool(api_key, rental_code)
                if pin:
                    logger.info(f"Found verification PIN: {pin}")
                    break
                time.sleep(2)
            else:
                logger.warning("No SMS verification PIN received within timeout period")
                return False
            click_and_focus(driver, "[name='pin']")
            simulate_human_typing(driver, pin_field, pin)
            random_delay(1, 2)
            click_and_focus(driver, "[type='submit']")
            random_delay(2, 3)
            logger.info("Phone verification completed successfully")
            time.sleep(10)
            return True
        except Exception as e:
            logger.error(f"Error handling phone verification for {phone_number}: {str(e)}")
            continue
    return False
def  handle_captcha(driver: webdriver.Chrome):
    answer = analyze_screenshot_with_openai("output/screenshots/captcha_screen.png")          
    if answer:
        logger.info(f"OpenAI provided answer: {answer}")
        try:
            bg_position_map = {
                "1": "0% 0%",
                "2": "50% 0%", 
                "3": "100% 0%",
                "4": "0% 100%",
                "5": "50% 100%",
                "6": "100% 100%"
            }
            
            target_bg_position = bg_position_map.get(answer)
            if target_bg_position:
                try:
                    answer_button = driver.find_element(
                        By.CSS_SELECTOR, 
                        f"[style*='background-position: {target_bg_position}']"
                    )
                    answer_button.click()
                    logger.info(f"Clicked answer button with background-position: {target_bg_position}")
                    input("Enter to continue")
                    time.sleep(2)
                except:
                    logger.error("Failed to click any answer button")
            else:
                logger.warning(f"Invalid answer number: {answer}")
        except Exception as e:
            logger.warning(f"Could not find answer button for: {answer}. Error: {str(e)}")
    else:
        logger.warning("No answer received from OpenAI")


def check_verification(driver: webdriver.Chrome):
    """Handle verification challenges"""
    try:
        time.sleep(30)
        driver.switch_to.default_content()
        print("Switching to default content")
        iframe = driver.find_element(By.CSS_SELECTOR, "iframe.challenge-dialog__iframe")
        print("Iframe found")
        if iframe:
           try: 
                print("Switching to phone")
                driver.switch_to.frame(iframe)
                driver.find_element(By.CSS_SELECTOR, "[name='phoneNumber']")
                print("Phone Verification")
                handle_phone_verification(driver)
                driver.switch_to.default_content()
           except: 
                driver.switch_to.default_content()
                logger.info("Handling email verification")
                driver.switch_to.frame(iframe)
                print("Switching to captcha iframe")
                captcha_iframe = driver.find_element(By.ID, "captcha-internal")
                print("Captcha iframe found")
                driver.switch_to.frame(captcha_iframe)
                print("Switching to arkose frame")
                arkoseFrame = driver.find_element(By.ID, "arkoseframe")
                print("Arkose frame found")
                driver.switch_to.frame(arkoseFrame)
                frame3 = driver.find_element(By.XPATH, "//*[@id='arkose']/div/iframe")
                print("Enforcement Frame found")
                driver.switch_to.frame(frame3)
                frame4 = driver.find_element(By.ID, "game-core-frame")
                print("Game Core Frame found")
                driver.switch_to.frame(frame4)
                print("CAPTCHA FOUND")

                try: 
                    driver.find_element(By.CSS_SELECTOR, "[data-theme='home.verifyButton']").click()
                    print("Verify button clicked")
                    time.sleep(3)
                    driver.save_screenshot("output/screenshots/captcha_screen.png")
                except: 
                    driver.save_screenshot("output/screenshots/captcha_screen.png")
                    print("No verify button found")
                
                handle_captcha(driver)
                time.sleep(20)
                driver.switch_to.default_content()
                return True
    except Exception as e:
        logger.error(f"Error checking verification: {str(e)}")
        return False

def get_sms_pin_from_smspool(api_key: str, rental_code: str) -> str:
    """Retrieve the SMS PIN from SMSPool using their API."""
    try:
        time.sleep(20)
        response = requests.post(
            'https://api.smspool.net/rental/retrieve_messages',
            data={
                'key': api_key,
                'rental_code': rental_code
            }
        )
        response.raise_for_status()
        data = response.json()
        if data.get('success') == 1 and data.get('messages'):
            messages_dict = data['messages']
            if isinstance(messages_dict, dict) and messages_dict:
                print(messages_dict)
                
                latest_msg = messages_dict['0'].get('message', '')
                import re
                match = re.search(r'(\d{6})', latest_msg)
                if match:
                    return match.group(1)
        return ""
    except Exception as e:
        logger.error(f"Error retrieving SMS PIN from SMSPool: {str(e)}")
        return ""

def create_linkedin_account(data: dict, profile_id: str = None):
    """Create LinkedIn account using GoLogin profiles"""
    try:
        driver = init_gologin_driver(profile_id)
        
        try:
            driver.get("https://www.google.com/")
            time.sleep(20)
            driver.get("https://www.facebook.com/")
            time.sleep(20)
            logger.info("Navigating to LinkedIn signup page")
            driver.get("https://www.linkedin.com/signup")
            random_delay(2, 4)
            
            logger.info("Entering email")
            email_field = click_and_focus(driver, '[name="email-address"]')
            simulate_human_typing(driver, email_field, data['google_email'])
            random_delay(1, 2)
            
            logger.info("Entering password")
            password_field = click_and_focus(driver, '[name="password"]')
            simulate_human_typing(driver, password_field, data['linkedin_password'])
            random_delay(1, 2)
            
            click_and_focus(driver, '[class*="join-form__form-body-submit-button"]')
            time.sleep(8)

            logger.info("Entering first name")
            first_name_field = click_and_focus(driver, '[name="first-name"]')
            simulate_human_typing(driver, first_name_field, data['first_name'])
            random_delay(1, 2)
            
            logger.info("Entering last name")
            last_name_field = click_and_focus(driver, '[name="last-name"]')
            simulate_human_typing(driver, last_name_field, data['last_name'])
            random_delay(2, 3)
            click_and_focus(driver, '[class*="join-form__form-body-submit-button join-form__form-body-submit-button--no-agreement-text"]')
            check_verification(driver)
            
            click_and_focus(driver, "button.artdeco-button.artdeco-button--4.artdeco-button--primary.ember-view.full-width")
            click_and_focus(driver, "button.artdeco-button.artdeco-button--muted.artdeco-button--4.artdeco-button--tertiary.ember-view.full-width.mb4")
            
            school_field = click_and_focus(driver, "#typeahead-input-for-school-name")
            random_delay(1, 2)
            simulate_human_typing(driver, school_field, "John hopkins")
            random_delay(1, 2)
            
            click_and_focus(driver, "#onboarding-profile-edu-start-year")
            # Wait for dropdown to be populated and select the year
            wait = WebDriverWait(driver, 10)
            start_year_select = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#onboarding-profile-edu-start-year")))
            select_start = Select(start_year_select)
            select_start.select_by_value("2020")
            random_delay(1, 2)
            
            click_and_focus(driver, "#onboarding-profile-edu-end-year")
            # Wait for dropdown to be populated and select the year
            end_year_select = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "#onboarding-profile-edu-end-year")))
            select_end = Select(end_year_select)
            select_end.select_by_value("2024")
            random_delay(1, 2)
            
            click_and_focus(driver, "button.onboarding-profile-cta.full-width.artdeco-button.artdeco-button--4.artdeco-button--primary.ember-view")
            time.sleep(10)
            
            logger.info("Waiting for email verification PIN...")
            max_wait_time = 15  # Wait 15 seconds for email PIN
            start_time = time.time()
            
            while time.time() - start_time < max_wait_time:
                pin = get_verification_pin()
                if pin:
                    logger.info(f"Found verification PIN: {pin}")
                    break
                time.sleep(2) 
            else:
                logger.warning("No email verification PIN received within 15 seconds, clicking resend button")
                # Click the resend button if no PIN found
                try:
                    resend_button = driver.find_element(By.CSS_SELECTOR, "button.artdeco-button.artdeco-button--muted.artdeco-button--2.artdeco-button--tertiary.ember-view")
                    resend_button.click()
                    logger.info("Clicked resend button for email verification")
                    time.sleep(15)
                    pin = get_verification_pin()
                    if pin:
                        logger.info(f"Found verification PIN: {pin}")
                    time.sleep(2)
                except Exception as e:
                    logger.error(f"Error clicking resend button: {str(e)}")
                    return {
                        "status": "error",
                        "message": f"Failed to click resend button: {str(e)}",
                        "email": data['google_email'],
                        "verification_type": "email",
                    }
            print(pin)
            pin_field = click_and_focus(driver, "#email-confirmation-input")
            simulate_human_typing(driver, pin_field, pin)
            random_delay(1, 2)
            
            click_and_focus(driver, "button.artdeco-button.artdeco-button--4.artdeco-button--primary.ember-view.full-width.mt5.mb4.t-18.t-18--open.t-white.t-normal")
            time.sleep(10)
            
            return {
                "status": "success",
                "message": "LinkedIn account creation process completed",
                "email": data['google_email'],
                "verification_type": "sms",
            }
            
        finally:
            try:
                driver.quit()
            except Exception as e:
                logger.warning(f"Error closing driver: {str(e)}")
            
    except Exception as e:
        logger.error(f"Error during account creation: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.post("/mailgun-webhook")
async def handle_mailgun_webhook(request: Request, background_tasks: BackgroundTasks):
    def process_mailgun_webhook(form_data):
        try:
            body_plain = form_data.get("stripped-text", "")
            pin_match = re.search(r'your pin is (\d{6})|^(\d{6})$', body_plain, re.IGNORECASE | re.MULTILINE)
            if pin_match:
                pin = pin_match.group(1) or pin_match.group(2)
                logger.info(f"Found LinkedIn verification PIN: {pin}")
                store_verification_pin(pin)
            else:
                logger.warning("No PIN found in email body")
        except Exception as e:
            logger.error(f"Error processing Mailgun webhook in background: {str(e)}")

    try:
        form_data = await request.form()
        background_tasks.add_task(process_mailgun_webhook, dict(form_data))
        return {
            "status": "started",
            "message": "Mailgun webhook processing started in background"
        }
    except Exception as e:
        logger.error(f"Error processing Mailgun webhook: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }
@app.post("/create-linkedin",
    response_model=LinkedInResponse,
    tags=["LinkedIn"],
    summary="Create a new LinkedIn account",
    description="Creates a new LinkedIn account with the provided details and handles the automation process"
)
async def create_account(request: LinkedInAccountRequest, background_tasks: BackgroundTasks):
    try:
        logger.info(f"Starting LinkedIn account creation for email: {request.google_email}")
        clear_verification_pin()
        
        # Extract profile_id from request data
        request_data = request.model_dump()
        
        # Use the hardcoded profile ID for now
        profile_id = "profile_id"
        
        # Pass profile_id separately to the function
        background_tasks.add_task(create_linkedin_account, request_data, profile_id)
        return {
            "status": "started",
            "message": "LinkedIn account creation started in background",
            "email": request.google_email,
            "verification_type": None
        }
    except Exception as e:
        logger.error(f"API error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def start_server():
    """Function to run the FastAPI server"""
    uvicorn.run(app, host="0.0.0.0", port=8000)

if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server)
    server_thread.daemon = True
    server_thread.start()
    
    logger.info("Starting LinkedIn Account Creator service with Mailgun webhook...")
    
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Shutting down...") 

 # -*- coding: utf-8 -*-
"""
AI-Powered Contact Form Bot (Updated 2026 Engine)
- Powered by: Zevahit Integration Engine
- Core Fixes: Fixed "no_form_found" via Shadow DOM piercing & Advanced Iframe routing. Fixed trailing syntax error.
"""
import os
import json
import base64
import time
import logging
import sys
import re
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=FutureWarning)
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
import twocaptcha

# ------------------------------------------
#  CONFIGURATION - GitHub Secrets se aata hai
# ------------------------------------------

GEMINI_API_KEY      = os.environ["GEMINI_API_KEY"]       # Google AI Studio se free key
CAPTCHA_API_KEY     = os.environ["CAPTCHA_API_KEY"]
GOOGLE_SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]       # Sheet URL se ID
GOOGLE_CREDS_JSON   = os.environ["GOOGLE_CREDS_JSON"]     # Service account JSON

# Gemini setup - 3.1 Flash Lite (500 req/day free tier)
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-3.1-flash-lite")

FIRST_NAME  = "Ray"
LAST_NAME   = "Sharma"
FULL_NAME   = "Ray"
COMPANY     = "Zevahit"
EMAIL       = "sales@zevahit.com"
PHONE       = "+18005550199"

SUBJECT_TEMPLATE = "Does AI recommend you when buyers ask?"
MESSAGE_TEMPLATE = "Hi,\n\n{intro}Quick question: when a buyer asks ChatGPT, Perplexity, or Google's AI \"what's the best tool/provider for [your category]?\" - does your name come up?\n\nFor most B2B brands it doesn't yet. These AI answers pull from sources that mention and cite you across the web. No mentions, no citations - so the AI recommends a competitor instead, and you never even see it happen.\n\nThat's what we fix at Zevahit. We get your brand featured and cited on real, high-authority editorial sites - the exact signals that both Google rankings AND AI search engines rely on to decide who to trust and recommend.\n\nWant to see where you currently stand? Reply with your category and I'll send a free snapshot of how visible you are in AI search today, plus the 3 quickest wins.\n\n- Ray, Zevahit\nzevahit.com\nClient reviews: https://clutch.co/profile/zevahit#reviews"

PROCESS_LIMIT = None  # None = sab sites ek hi run mein

CONTACT_KEYWORDS = ["contact", "contact-us", "contactus", "contact-form", "get-in-touch",
                    "getintouch", "reach-us", "reachus", "reach-out", "write-to-us",
                    "get-started", "getstarted", "start-here", "enquiry", "enquire",
                    "enquiries", "inquiry", "inquire", "lets-talk", "let-s-talk", "lets-connect",
                    "work-with-us", "hire-us", "hire", "start-project", "start-a-project",
                    "request-quote", "request-a-quote", "get-a-quote", "get-quote", "quote",
                    "book-a-call", "book-call", "book-a-consultation", "book-consultation",
                    "free-consultation", "free-audit", "free-quote", "schedule", "schedule-a-call",
                    "consultation", "talk-to-us", "connect", "connect-with-us", "say-hello",
                    "hello", "support", "help", "get-in-touch-with-us", "contact-sales", "demo", "request-demo"]

# ------------------------------------------
#  LOGGING
# ------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)

# ------------------------------------------
#  GOOGLE SHEETS SETUP
# ------------------------------------------

def init_sheets():
    """Google Sheets connection initialize karo aur city column set karo."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=[
            "https://spreadsheets.google.com/feeds",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)

    try:
        ws = sh.worksheet("websites")
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet("websites", rows=1000, cols=7)
        ws.update("A1:G1", [["website", "city", "status", "submitted_at", "notes", "fields_filled", "ai_actions"]])

    return ws


def get_all_rows(ws):
    return ws.get_all_records()


def update_sheet_row(ws, row_num, status, notes="", fields_filled="", ai_actions=""):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    excel_row = row_num + 1
    
    headers = ws.row_values(1)
    try:
        status_idx = headers.index("status")
        start_col = chr(65 + status_idx)  
        end_col = chr(65 + status_idx + 4)
        ws.update("{}{}:{}{}".format(start_col, excel_row, end_col, excel_row),
                  [[status, now, notes, fields_filled, ai_actions]])
    except ValueError:
        ws.update("C{}:G{}".format(excel_row, excel_row),
                  [[status, now, notes, fields_filled, ai_actions]])
        
    log.info("  [Sheets] Row {} -> {}".format(excel_row, status))


def get_pending_rows(ws):
    rows = ws.get_all_records()
    pending = []
    for i, row in enumerate(rows):
        url     = str(row.get("website", "")).strip()
        status  = str(row.get("status", "")).strip().lower()
        if url and status not in ("submitted",):
            pending.append((i + 1, row))  
    return pending

# ------------------------------------------
#  URL HELPERS
# ------------------------------------------

def normalise_url(url):
    url = str(url).strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def dismiss_cookie_banner(page):
    accept_texts = ["accept all", "accept all cookies", "accept cookies", "accept",
                    "i agree", "agree", "agree & continue", "got it", "allow all",
                    "allow cookies", "allow", "ok", "okay", "i accept", "accept & close",
                    "continue", "i understand", "understand", "consent", "yes, i agree",
                    "close", "dismiss", "no problem", "sounds good"]
    selectors = ("button, a, input[type='button'], input[type='submit'], "
                 "[role='button'], div[onclick], span[onclick]")
    try:
        buttons = page.locator(selectors).all()
        for btn in buttons[:40]:
            try:
                txt = (btn.inner_text(timeout=100) or "").strip().lower()
            except Exception:
                continue
            if not txt or len(txt) > 20:
                continue
            if any(t == txt for t in accept_texts):
                try:
                    if btn.is_visible(timeout=200):
                        btn.click(timeout=1000)
                        log.info("  [Cookie] dismissed: {}".format(txt[:25]))
                        time.sleep(0.5)
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def check_form_presence_deep(page):
    """B2B forms are frequently inside shadow DOMs or third party wrappers."""
    try:
        selectors = ['input:not([type="hidden"])', 'textarea', 'iframe[src*="hsforms"]', 
                     'iframe[src*="calendly"]', 'iframe[src*="forms"]', '.hs-form']
        for sel in selectors:
            if page.locator(sel).first.count() > 0:
                return True
        shadow_inputs = page.evaluate("""() => {
            let found = false;
            const findShadow = (root) => {
                if (!root || found) return;
                if (root.querySelector && (root.querySelector('input:not([type="hidden"])') || root.querySelector('textarea'))) {
                    found = true; return;
                }
                let items = root.querySelectorAll ? root.querySelectorAll('*') : [];
                for (let i of items) { if (i.shadowRoot) findShadow(i.shadowRoot); }
            };
            findShadow(document);
            return found;
        }""")
        return shadow_inputs
    except:
        return False


def find_contact_page(page, base_url):
    current_url = page.url
    try:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(0.5)
        page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass

    try:
        links = page.locator("a").all()
        for link in links:
            try:
                href = link.get_attribute("href") or ""
                link_text = ""
                try:
                    link_text = (link.inner_text(timeout=200) or "").lower()
                except Exception:
                    pass
                if any(kw in href.lower() for kw in CONTACT_KEYWORDS) or \
                   any(kw.replace("-", " ") in link_text for kw in CONTACT_KEYWORDS):
                    if any(kw in current_url.lower() for kw in CONTACT_KEYWORDS):
                        return True
                    log.info("  Navigating to link: {}".format(href))
                    try:
                        link.click(timeout=5000)
                        page.wait_for_load_state("domcontentloaded", timeout=8000)
                        return True
                    except Exception:
                        pass
    except Exception:
        pass

    if check_form_presence_deep(page):
        return True

    # Mutated Fallback paths scan 
    for kw in ["contact", "contact-us", "demo", "request-demo", "get-started", "talk-to-sales"]:
        candidate = "{}/{}".format(base_url, kw)
        try:
            resp = page.goto(candidate, timeout=8000, wait_until="domcontentloaded")
            if resp and resp.status < 400 and "404" not in page.title().lower():
                log.info("  Direct Mutation Match: {}".format(candidate))
                return True
        except Exception:
            pass
    return False

# ------------------------------------------
#  CAPTCHA SOLVER (2captcha)
# ------------------------------------------

def solve_captcha(page, website):
    try:
        solver = twocaptcha.TwoCaptcha(CAPTCHA_API_KEY)
        frame = page.locator('iframe[src*="recaptcha"], iframe[src*="hcaptcha"]').first
        if frame.is_visible(timeout=500):
            src = frame.get_attribute("src") or ""
            sitekey = ""
            for part in src.split("&"):
                if "k=" in part or "sitekey=" in part:
                    sitekey = part.split("=")[1].split("&")[0]
                    break
            if sitekey:
                log.info("  [CAPTCHA] Solver Engine Activated...")
                result = solver.recaptcha(sitekey=sitekey, url=website)
                token = result["code"]
                page.evaluate(f"try {{ document.getElementById('g-recaptcha-response').innerHTML = '{token}'; }} catch(e) {{}}")
                return True
    except Exception:
        pass
    return False

# ------------------------------------------
#  AI PERSONALIZATION 
# ------------------------------------------

def get_page_text(page):
    try:
        txt = page.evaluate(
            """() => {
                let out = '';
                document.querySelectorAll('h1,h2,title,p').forEach(el => {
                    if (el.innerText) out += el.innerText.trim() + ' | ';
                });
                return out;
            }"""
        )
        return (txt or "")[:4000]
    except Exception:
        return ""


def generate_personalized_line(page, website):
    site_text = get_page_text(page)
    if len(site_text.strip()) < 40:
        return ""

    prompt = """You are writing the FIRST sentence of a cold outreach message to a B2B brand.
Here is text scraped from their website ({website}):
{site_text}

Write ONE short, genuine opening line (max 22 words) that shows we actually looked at their site.
Rules:
- Mention something REAL and specific about what they do: their product category or target industry.
- No "I hope this finds you well". No marketing slogans verbatim.
- End with a comma or dash so the next sentence flows naturally.
- Return ONLY the line itself. No quotes, no explanations, no markdown."""

    prompt = prompt.format(website=website, site_text=site_text)
    try:
        resp = gemini_model.generate_content(prompt)
        raw = (resp.text or "").strip().replace("```", "").strip('"').strip("'").split("\n")[0]
        if 5 < len(raw.split()) < 30:
            log.info("  [Personalize] Hook Built: {}".format(raw[:80]))
            return raw
    except Exception:
        pass
    return ""

# ------------------------------------------
#  AI FORM ANALYSIS 
# ------------------------------------------

def get_page_html(page):
    """Deep Flattener mapping logic that targets Shadow DOM elements and child frame inputs."""
    try:
        js_extractor = """() => {
            const getAttrs = (el) => {
                let tag = el.tagName.toLowerCase();
                let res = [];
                ['id', 'name', 'type', 'placeholder', 'class', 'role', 'aria-label'].forEach(a => {
                    let v = el.getAttribute(a); if(v) res.push(`${a}="${v}"`);
                });
                return `<${tag} ${res.join(' ')}>${['button', 'label'].includes(tag) ? el.innerText.strip() : ''}</${tag}>`;
            };
            let stream = '';
            document.querySelectorAll('form, input, textarea, button, select, label, [role="form"]').forEach(el => {
                stream += getAttrs(el) + '\\n';
            });
            const processShadow = (root) => {
                if(!root) return;
                root.querySelectorAll('input, textarea, button, select, label').forEach(m => { stream += '[SHADOW] ' + getAttrs(m) + '\\n'; });
                root.querySelectorAll('*').forEach(child => { if(child.shadowRoot) processShadow(child.shadowRoot); });
            };
            processShadow(document);
            return stream;
        }"""
        chunks = [page.evaluate(js_extractor)]
        for fr in page.frames:
            if fr != page.main_frame:
                try:
                    fh = fr.evaluate(js_extractor)
                    if fh.strip(): chunks.append("[IFRAME] " + fh)
                except: pass
        return "\n".join(chunks)[:25000]
    except Exception:
        return ""


def ask_claude(page, website, subject, message):
    page_html = get_page_html(page)
    prompt = """You are a web automation expert. Find structural selectors to fill this contact form on: {website}
Form DOM Map:
{html}

Details to fill:
- Full Name: {full_name}
- First Name: {first_name}
- Last Name: {last_name}
- Company: {company}
- Email: {email}
- Phone: {phone}
- Subject/Title: {subject}
- Message (Keep EXACTLY as copy):
{message}

Return ONLY a standard valid JSON array format. Ex:
[ {{"action": "fill", "selector": "input[name='email']", "value": "value"}}, {{"action": "click", "selector": "button[type='submit']"}} ]
Rules: No markdown wrap, no explanations. Skip empty elements."""

    prompt = prompt.format(website=website, html=page_html, full_name=FULL_NAME, first_name=FIRST_NAME, last_name=LAST_NAME, company=COMPANY, email=EMAIL, phone=PHONE, subject=subject, message=message)
    
    resp = gemini_model.generate_content(prompt)
    raw = resp.text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    return json.loads(raw.strip())

# ------------------------------------------
#  EXECUTE ACTIONS
# ------------------------------------------

def execute_actions(page, actions):
    filled = []
    submitted = False
    if not actions: return filled, submitted

    for action in actions:
        act = action.get("action", "").lower()
        sel = action.get("selector", "")
        val = action.get("value", "")
        if not sel: continue

        target = None
        try:
            if page.locator(sel).first.is_visible(timeout=300):
                target = page.locator(sel).first
        except: pass

        if not target:
            for frame in page.frames:
                try:
                    if frame.locator(sel).first.is_visible(timeout=200):
                        target = frame.locator(sel).first; break
                except: pass

        if not target: continue

        try:
            if act == "fill":
                target.scroll_into_view_if_needed(timeout=1000)
                target.fill(val)
                filled.append(sel.split("[")[0][:20])
            elif act == "check":
                target.check(timeout=1000)
            elif act == "select":
                target.select_option(val)
            elif act == "click":
                url_before = page.url
                try: target.click(timeout=3000)
                except: target.evaluate("el => el.click()")
                
                time.sleep(5)
                success_keywords = ["thank", "thanks", "sent", "success", "submitted", "received", "scheduled"]
                page_text = ""
                try: page_text = page.inner_text("body", timeout=1000).lower()
                except: pass
                
                if page.url != url_before or any(w in page_text for w in success_keywords):
                    submitted = True
        except Exception:
            pass
            
    return filled, submitted

# ------------------------------------------
#  MAIN RUNNER
# ------------------------------------------

def main():
    log.info("Connecting to Google Sheets...")
    ws = init_sheets()
    pending = get_pending_rows(ws)
    log.info("Pending sites: {}".format(len(pending)))

    if not pending:
        log.info("No pending sites. Done!")
        return

    to_process = pending[:PROCESS_LIMIT]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"])
        context = browser.new_context(user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
        
        tabs = []
        context.on("page", lambda p: tabs.append(p))
        pg = context.new_page()
        pg.set_default_timeout(25000)

        for row_idx, row_data in to_process:
            tabs.clear()
            website = normalise_url(row_data.get("website", ""))
            current_subject = SUBJECT_TEMPLATE
            log.info("\nOpening: {}".format(website))

            try:
                pg.goto(website, timeout=35000, wait_until="domcontentloaded")
                time.sleep(3)
                dismiss_cookie_banner(pg)

                try: intro_line = generate_personalized_line(pg, website)
                except: intro_line = ""
                
                intro_block = (intro_line.strip() + "\n\n") if intro_line.strip() else ""
                current_message = MESSAGE_TEMPLATE.format(intro=intro_block)

                find_contact_page(pg, website)
                time.sleep(2)
                
                active_page = tabs[-1] if tabs else pg
                dismiss_cookie_banner(active_page)
                solve_captcha(active_page, website)

                try:
                    actions = ask_claude(active_page, website, current_subject, current_message)
                    log.info("  [AI Matrix Selector] {} actions generated".format(len(actions)))
                except Exception as e:
                    update_sheet_row(ws, row_idx, "error", "AI Selector Break: {}".format(str(e)[:50]))
                    continue

                filled, submitted = execute_actions(active_page, actions)

                # Screenshots Logic Layer
                try:
                    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', website)[:40]
                    os.makedirs("screenshots/after_submit", exist_ok=True)
                    active_page.screenshot(path="screenshots/after_submit/{}.png".format(safe_name), full_page=False)
                except: pass

                if submitted:
                    status = "submitted"
                    notes = "Form submitted and verified via context changes."
                elif not filled:
                    status = "no_form_found"
                    notes = "Skipped: Forms or embedded appointment widgets could not be mapped inside DOM."
                else:
                    status = "filled_not_submitted"
                    notes = f"Fields filled ({', '.join(filled)}), submit trigger didn't catch redirect response."

                update_sheet_row(ws, row_idx, status, notes=notes, fields_filled=", ".join(filled))
                
                for extra_tab in context.pages:
                    if extra_tab != pg: extra_tab.close()
                time.sleep(5)

            except Exception as row_err:
                log.error("Row Error trapped gracefully: {}".format(row_err))
                update_sheet_row(ws, row_idx, "error", notes=str(row_err)[:60])
                for extra_tab in context.pages:
                    if extra_tab != pg: extra_tab.close()

        browser.close()

if __name__ == "__main__":
    main()

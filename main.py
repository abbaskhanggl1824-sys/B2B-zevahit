# -*- coding: utf-8 -*-
"""
AI-Powered Contact Form Bot (Dynamic Engine Update)
- Engine Wrapper: Multi-Tier Fail-Safe Sheet Handler
- Fixed: Sheets mismatch sync drops & structural header updates.
- Patched: Hard per-site wall-clock timeout (signal.alarm) + Gemini request timeouts
  + wedged-page recovery, so a single bad site can no longer hang the whole run.
"""
import os
import json
import base64
import time
import logging
import sys
import re
import signal
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", category=FutureWarning)
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright
import twocaptcha

# ------------------------------------------
#  CONFIGURATION - GitHub Secrets
# ------------------------------------------

GEMINI_API_KEY      = os.environ["GEMINI_API_KEY"]
CAPTCHA_API_KEY     = os.environ["CAPTCHA_API_KEY"]
GOOGLE_SHEET_ID     = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS_JSON   = os.environ["GOOGLE_CREDS_JSON"]

# Gemini Setup
genai.configure(api_key=GEMINI_API_KEY)
gemini_model = genai.GenerativeModel("gemini-3.1-flash-lite")

# Hard ceiling (seconds) for total time spent on ONE website. After this the
# row is abandoned and marked error, no matter where it is stuck.
PER_SITE_TIMEOUT = 150
# Timeouts for the two Gemini API calls so they can never silently block forever.
GEMINI_HOOK_TIMEOUT = 30
GEMINI_FORM_TIMEOUT = 40

FIRST_NAME  = "Ray"
LAST_NAME   = "Sharma"
FULL_NAME   = "Ray"
COMPANY     = "Zevahit"
EMAIL       = "sales@zevahit.com"
PHONE       = "+18005550199"

SUBJECT_TEMPLATE = "Does AI recommend you when buyers ask?"
MESSAGE_TEMPLATE = "Hi,\n\n{intro}Quick question: when a buyer asks ChatGPT, Perplexity, or Google's AI \"what's the best tool/provider for [your category]?\" - does your name come up?\n\nFor most B2B brands it doesn't yet. These AI answers pull from sources that mention and cite you across the web. No mentions, no citations - so the AI recommends a competitor instead, and you never even see it happen.\n\nThat's what we fix at Zevahit. We get your brand featured and cited on real, high-authority editorial sites - the exact signals that both Google rankings AND AI search engines rely on to decide who to trust and recommend.\n\nWant to see where you currently stand? Reply with your category and I'll send a free snapshot of how visible you are in AI search today, plus the 3 quickest wins.\n\n- Ray, Zevahit\nzevahit.com\nClient reviews: https://clutch.co/profile/zevahit#reviews"

PROCESS_LIMIT = None

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
#  HARD TIMEOUT GUARD (per-site wall clock)
# ------------------------------------------

class RowTimeout(Exception):
    """Raised by the SIGALRM handler when a single site exceeds PER_SITE_TIMEOUT."""
    pass


def _alarm_handler(signum, frame):
    raise RowTimeout("Per-site wall-clock timeout exceeded")


# Register once at import time. SIGALRM is only available on Unix (GitHub
# Actions Ubuntu runners are Unix, so this is fine).
signal.signal(signal.SIGALRM, _alarm_handler)

# ------------------------------------------
#  GOOGLE SHEETS ENGINE (UPGRADED)
# ------------------------------------------

def init_sheets():
    """Dynamically loads sheet workspace or forces lowercase tab parsing."""
    creds_dict = json.loads(GOOGLE_CREDS_JSON)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(GOOGLE_SHEET_ID)

    # Tab mapping lookup logic
    ws = None
    try:
        ws = sh.worksheet("websites")
    except gspread.WorksheetNotFound:
        # Fallback layer: Case insensitive scanning
        for sheet in sh.worksheets():
            if sheet.title.lower().strip() == "websites":
                ws = sheet
                break
        if not ws:
            log.warning("Tab 'websites' not found. Creating a fresh tracking sheet tab...")
            ws = sh.add_worksheet("websites", rows=1000, cols=7)
            ws.update("A1:G1", [["website", "city", "status", "submitted_at", "notes", "fields_filled", "ai_actions"]])

    # Structure integrity validation
    headers = [str(h).strip().lower() for h in ws.row_values(1)]
    if not headers or "website" not in headers:
        log.warning("Sheet Headers out of sync. Injecting structural automation grid row...")
        ws.update("A1:G1", [["website", "city", "status", "submitted_at", "notes", "fields_filled", "ai_actions"]])
        time.sleep(1)

    return ws


def update_sheet_row(ws, row_num, status, notes="", fields_filled="", ai_actions=""):
    """Deep structural cell mapping targeting directly into correct index offsets."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    excel_row = row_num + 1

    headers = [str(h).strip().lower() for h in ws.row_values(1)]
    try:
        status_idx = headers.index("status")
        start_col = chr(65 + status_idx)
        end_col = chr(65 + status_idx + 4)
        ws.update("{}{}:{}{}".format(start_col, excel_row, end_col, excel_row),
                  [[status, now, notes, fields_filled, ai_actions]])
    except Exception:
        # Static Matrix Fallback mapping structure
        ws.update("C{}:G{}".format(excel_row, excel_row),
                  [[status, now, notes, fields_filled, ai_actions]])

    log.info("  [Sheets Save Engine] Captured Row {} -> Sync status: {}".format(excel_row, status))


def get_pending_rows(ws):
    """Parses structural maps mapping non-blank rows while preserving casing variants."""
    rows = ws.get_all_records()
    pending = []

    for i, row in enumerate(rows):
        # Normalization layer for row dictionary casing differences
        normalized_row = {str(k).strip().lower(): v for k, v in row.items()}

        url = str(normalized_row.get("website", "")).strip()
        status = str(normalized_row.get("status", "")).strip().lower()

        if url and status not in ("submitted", "processing", "no_form_found"):
            pending.append((i + 1, normalized_row))

    return pending

# ------------------------------------------
#  BROWSER AUTOMATION WRAPPERS
# ------------------------------------------

def normalise_url(url):
    url = str(url).strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")


def dismiss_cookie_banner(page):
    accept_texts = ["accept all", "accept all cookies", "accept cookies", "accept",
                    "i agree", "agree", "agree & continue", "got it", "allow all",
                    "allow cookies", "allow", "ok", "okay", "i accept", "accept & close"]
    try:
        buttons = page.locator("button, a, [role='button']").all()
        for btn in buttons[:30]:
            txt = (btn.inner_text(timeout=100) or "").strip().lower()
            if txt and any(t == txt for t in accept_texts):
                btn.click(timeout=1000)
                return True
    except: pass
    return False


def check_form_presence_deep(page):
    try:
        for sel in ['input:not([type="hidden"])', 'textarea', 'iframe[src*="forms"]', '.hs-form']:
            if page.locator(sel).first.count() > 0: return True
        return page.evaluate("""() => {
            let f = false;
            const scan = (r) => {
                if (!r || f) return;
                if (r.querySelector && (r.querySelector('input:not([type="hidden"])') || r.querySelector('textarea'))) { f = true; return; }
                let el = r.querySelectorAll ? r.querySelectorAll('*') : [];
                for (let i of el) { if (i.shadowRoot) scan(i.shadowRoot); }
            };
            scan(document); return f;
        }""")
    except: return False


def find_contact_page(page, base_url):
    current_url = page.url
    try:
        links = page.locator("a").all()
        for link in links:
            href = link.get_attribute("href") or ""
            txt = (link.inner_text(timeout=100) or "").lower()
            if any(kw in href.lower() for kw in CONTACT_KEYWORDS) or any(kw.replace("-", " ") in txt for kw in CONTACT_KEYWORDS):
                if any(kw in current_url.lower() for kw in CONTACT_KEYWORDS): return True
                try:
                    link.click(timeout=4000)
                    page.wait_for_load_state("domcontentloaded", timeout=6000)
                    return True
                except: pass
    except: pass

    if check_form_presence_deep(page): return True

    for kw in ["contact", "contact-us", "demo", "get-started"]:
        try:
            resp = page.goto("{}/{}".format(base_url, kw), timeout=6000, wait_until="domcontentloaded")
            if resp and resp.status < 400: return True
        except: pass
    return False

# ------------------------------------------
#  CAPTCHA & AI CONTEXT LOGIC
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
                    sitekey = part.split("=")[1].split("&")[0]; break
            if sitekey:
                log.info("  [CAPTCHA] Engine triggered...")
                res = solver.recaptcha(sitekey=sitekey, url=website)
                token = res["code"]
                page.evaluate(f"try {{ document.getElementById('g-recaptcha-response').innerHTML = '{token}'; }} catch(e) {{}}")
                return True
    except: pass
    return False


def generate_personalized_line(page, website):
    try:
        txt = page.evaluate("() => { let out = ''; document.querySelectorAll('h1,h2,title,p').forEach(el => { out += el.innerText + ' | ' }); return out; }")[:3000]
        if len(txt.strip()) < 40: return ""
        prompt = "Write ONE cold B2B opening hook sentence based on this text map from {website}: {txt}\nMax 22 words, end with a comma, no explanations, no markdown format rules."
        resp = gemini_model.generate_content(
            prompt.format(website=website, txt=txt),
            request_options={"timeout": GEMINI_HOOK_TIMEOUT}
        )
        hook = resp.text.strip().replace("```", "").strip('"').split("\n")[0]
        if 5 < len(hook.split()) < 35: return hook
    except: pass
    return ""


def get_page_html(page):
    try:
        js = """() => {
            let out = '';
            document.querySelectorAll('form, input, textarea, button, select, label').forEach(el => {
                let attrs = []; ['id', 'name', 'type', 'placeholder'].forEach(a => { let v = el.getAttribute(a); if(v) attrs.push(`${a}="${v}"`); });
                out += `<${el.tagName.toLowerCase()} ${attrs.join(' ')}>${el.innerText || ''}</...>\n`;
            });
            return out;
        }"""
        chunks = [page.evaluate(js)]
        for f in page.frames:
            if f != page.main_frame:
                try: chunks.append(f.evaluate(js))
                except: pass
        return "\n".join(chunks)[:25000]
    except: return ""


def ask_gemini_for_form(page, website, subject, message):
    """Sends the form DOM map to Gemini and returns a list of fill/click actions."""
    html = get_page_html(page)
    prompt = """You are a functional web parser script executor. Return ONLY a standard structured JSON array list mapping actions for this DOM:
    {html}
    Mapping instructions:
    - Target matching standard form items using fields: Full Name={full_name}, Email={email}, Company={company}, Phone={phone}, Subject={subject}, Message Field={message}
    - Final element should always be the click element targeting button[type='submit'] inside form.
    Format example: [ {{"action": "fill", "selector": "input[name='email']", "value": "..."}} ]
    No explanations, no code block quotes wraps."""

    prompt = prompt.format(html=html, full_name=FULL_NAME, email=EMAIL, company=COMPANY, phone=PHONE, subject=subject, message=message)
    resp = gemini_model.generate_content(prompt, request_options={"timeout": GEMINI_FORM_TIMEOUT})
    raw = resp.text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    return json.loads(raw.strip())


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
            if page.locator(sel).first.is_visible(timeout=300): target = page.locator(sel).first
        except: pass

        if not target:
            for f in page.frames:
                try:
                    if f.locator(sel).first.is_visible(timeout=200): target = f.locator(sel).first; break
                except: pass

        if not target: continue

        try:
            if act == "fill":
                target.scroll_into_view_if_needed(timeout=1000)
                target.fill(val)
                filled.append(sel.split("[")[0][:15])
            elif act == "check":
                target.check(timeout=1000)
            elif act == "select":
                target.select_option(val)
            elif act == "click":
                url_before = page.url
                try: target.click(timeout=3000)
                except: target.evaluate("el => el.click()")

                time.sleep(5)
                success_keys = ["thank", "thanks", "sent", "success", "submitted", "received"]
                body_txt = ""
                try: body_txt = page.inner_text("body", timeout=1000).lower()
                except: pass

                if page.url != url_before or any(w in body_txt for w in success_keys):
                    submitted = True
        except: pass

    return filled, submitted

# ------------------------------------------
#  MAIN RUNNER SYSTEM ENGINE
# ------------------------------------------

def main():
    log.info("=== Bot Workspace Execution Pipeline Started ===")
    ws = init_sheets()
    pending = get_pending_rows(ws)
    log.info("Pending rows parsed successfully from registry: {}".format(len(pending)))

    if not pending:
        log.info("Tracking register returns 0 workloads. Process Terminated.")
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
            # Grabs domain using prioritized key variations match fallback
            website_raw = row_data.get("website", row_data.get("url", ""))
            website = normalise_url(website_raw)
            current_subject = SUBJECT_TEMPLATE

            log.info("\nLaunching Processing Vector: {}".format(website))

            # Arm the hard wall-clock guard for this single site.
            signal.alarm(PER_SITE_TIMEOUT)

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
                    actions = ask_gemini_for_form(active_page, website, current_subject, current_message)
                    log.info("  [AI Grid Engine Map] Successfully parsed form targets.")
                except Exception as e:
                    update_sheet_row(ws, row_idx, "error", "Form Engine Structure Read Error: {}".format(str(e)[:45]))
                    continue

                filled, submitted = execute_actions(active_page, actions)

                if submitted:
                    status, notes = "submitted", "Pipeline verified submission context."
                elif not filled:
                    status, notes = "no_form_found", "Skipped: Structural layout forms not mapped."
                else:
                    status, notes = "filled_not_submitted", f"Trigger dropped redirect checks ({', '.join(filled)})."

                update_sheet_row(ws, row_idx, status, notes=notes, fields_filled=", ".join(filled))

                for extra_tab in context.pages:
                    if extra_tab != pg: extra_tab.close()
                time.sleep(4)

            except RowTimeout:
                # Hard ceiling hit. Abandon this site and rebuild the page,
                # because a wedged page would carry the hang into the next row.
                log.error("Hard per-site timeout ({}s) hit, skipping: {}".format(PER_SITE_TIMEOUT, website))
                update_sheet_row(ws, row_idx, "error", notes="Hard per-site timeout ({}s)".format(PER_SITE_TIMEOUT))
                try:
                    for extra_tab in context.pages:
                        if extra_tab != pg: extra_tab.close()
                except: pass
                try:
                    pg.close()
                except: pass
                pg = context.new_page()
                pg.set_default_timeout(25000)

            except Exception as row_err:
                log.error("Graceful Trap catching processing error: {}".format(row_err))
                update_sheet_row(ws, row_idx, "error", notes=str(row_err)[:60])
                for extra_tab in context.pages:
                    if extra_tab != pg: extra_tab.close()

            finally:
                # Always disarm the alarm so it can never fire during the next
                # row's setup or during the Sheets write above.
                signal.alarm(0)

        browser.close()
    log.info("=== Bot Workspace Execution Pipeline Complete ===")

if __name__ == "__main__":
    main()

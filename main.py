def main():
    log.info("=== DEBUG: Bot Execution Started ===")
    
    # 1. Verification Checklist for Environment Variables
    required_env_vars = ["GEMINI_API_KEY", "CAPTCHA_API_KEY", "GOOGLE_SHEET_ID", "GOOGLE_CREDS_JSON"]
    missing_vars = [var for var in required_env_vars if var not in os.environ]
    
    if missing_vars:
        log.error("CRITICAL CONFIG ERROR: Missing GitHub Secrets: {}".format(missing_vars))
        log.error("Fix: Go to GitHub Repo Settings -> Secrets and variables -> Actions and add them.")
        sys.exit(1)

    # 2. Google Sheets Access Validation
    log.info("Connecting to Google Sheets...")
    try:
        ws = init_sheets()
    except json.JSONDecodeError:
        log.error("CRITICAL GOOGLE ERROR: GOOGLE_CREDS_JSON is not a valid JSON format!")
        sys.exit(1)
    except Exception as sheets_err:
        log.error("CRITICAL GOOGLE ERROR: Connection failed! Detail: {}".format(sheets_err))
        log.error("Fix: Ensure your Google Sheet ID is correct and your Service Account Email has 'Editor' rights on that sheet.")
        sys.exit(1)

    # 3. Read Pending Rows
    try:
        pending = get_pending_rows(ws)
        log.info("Pending sites found: {}".format(len(pending)))
    except Exception as read_err:
        log.error("CRITICAL SHEET READ ERROR: Could not read 'websites' worksheet: {}".format(read_err))
        sys.exit(1)

    if not pending:
        log.info("No pending sites to process. Done!")
        return

    to_process = pending[:PROCESS_LIMIT]

    # 4. Playwright Browser Automation Block
    try:
        with sync_playwright() as p:
            log.info("Launching Headless Chromium Browser...")
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
                log.info("\nProcessing Target: {}".format(website))

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

                    try:
                        safe_name = re.sub(r'[^a-zA-Z0-9]', '_', website)[:40]
                        os.makedirs("screenshots/after_submit", exist_ok=True)
                        active_page.screenshot(path="screenshots/after_submit/{}.png".format(safe_name), full_page=False)
                    except: pass

                    if submitted:
                        status = "submitted"
                        notes = "Form submitted successfully."
                    elif not filled:
                        status = "no_form_found"
                        notes = "Skipped: No form elements detected."
                    else:
                        status = "filled_not_submitted"
                        notes = f"Fields filled ({', '.join(filled)}), submit did not confirm."

                    update_sheet_row(ws, row_idx, status, notes=notes, fields_filled=", ".join(filled))
                    
                    for extra_tab in context.pages:
                        if extra_tab != pg: extra_tab.close()
                    time.sleep(5)

                except Exception as row_err:
                    log.error("Error processing row {}: {}".format(row_idx, row_err))
                    update_sheet_row(ws, row_idx, "error", notes=str(row_err)[:60])
                    for extra_tab in context.pages:
                        if extra_tab != pg: extra_tab.close()

            browser.close()
            
    except Exception as browser_err:
        log.error("CRITICAL BROWSER ERROR: Playwright initialization failed: {}".format(browser_err))
        log.error("Fix: Ensure 'playwright install chromium --with-deps' is added to your workflow YAML file.")
        sys.exit(1)

    log.info("=== Bot Execution Finished Successfully ===")

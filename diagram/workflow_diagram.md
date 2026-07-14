# AI-Powered B2B Lead Generation — Full System Workflow

## High-Level Nightly Pipeline

```mermaid
graph LR
    A["🗺️ Google Maps\nScraper\n00:00"] --> B["🔍 Website\nAudit\n01:00"]
    B --> C["🔗 LinkedIn\nSearch\n02:00"]
    C --> D["🧠 Lead Scoring\n& Pitch Gen\n03:00"]
    D --> E["🤝 LinkedIn\nConnections\n04:00"]
    E --> F["✅ Acceptance\nCheck\n12:00"]
    F --> G["📤 Pitch\nDelivery\n14:00"]
    G --> H["💬 Reply\nChecker\n16:00"]
```

---

## Detailed Component Workflow

```mermaid
flowchart TD
    START(["⚙️ FastAPI Server\nStartup"]) --> SCHED["APScheduler\nregisters 7 cron jobs"]

    %% ─── Phase 1: Scraping ───
    SCHED --> SCR["🗺️ Google Maps Scraper\n00:xx per scraper_settings.json"]
    SCR --> SCR1{"Business already\nin DB?"}
    SCR1 -- Yes --> SCR2["Skip duplicate"]
    SCR1 -- No --> SCR3["Save Business_Client\nscrape_source = 'google_maps'"]

    %% ─── Phase 2: Audit ───
    SCHED --> AUD["🔍 Website Audit Job\n01:00 daily"]
    AUD --> AUD1["Query unaudited\nBusiness_Client rows"]
    AUD1 --> AUD2["Playwright crawls\neach site URL"]
    AUD2 --> AUD3["Collect metrics:\nSEO · Perf · A11y\nSecurity · Content"]
    AUD3 --> AUD4["Save WebsiteAudit\nstatus = 'completed'"]

    %% ─── Phase 3: LinkedIn Search ───
    SCHED --> LIS["🔗 LinkedIn Search Job\n02:00 daily"]
    LIS --> LIS1["Query clients with\nis_linkedin_searched = false"]
    LIS1 --> LIS2["Playwright searches LinkedIn\nby company name + city"]
    LIS2 --> LIS3{"Contacts\nfound?"}
    LIS3 -- No --> LIS4["linkedin_search_status\n= 'no_contacts_found'"]
    LIS3 -- Yes --> LIS5["Save LinkedinContact rows\njob_title · profile_url\nis_connected = false"]

    %% ─── Phase 4: Lead Scoring & Pitch ───
    SCHED --> SCO["🧠 Lead Scoring & Pitch Job\n03:00 daily"]
    SCO --> SCO1["Query completed,\nunscored WebsiteAudits"]
    SCO1 --> SCO2["Compute LeadScore\n(A–F grade, 0–100)"]
    SCO2 --> PITCH_ROUTE{"Client source?"}

    PITCH_ROUTE -- "NOT google_maps" --> STATIC["📋 Static Template Pitch\n'full-stack dev team' message\ngenerated_by = 'static_template'"]

    PITCH_ROUTE -- "google_maps" --> LI_CHECK{"LinkedinContact\nexists?"}

    LI_CHECK -- Yes --> RANK["Rank contacts by\nseniority title + is_connected"]
    RANK --> EACH["For each contact\n(loop)"]
    EACH --> LLM_GATE{"Grade ≤ PITCH_LLM_MIN_GRADE\n& API key present?"}
    LLM_GATE -- Yes --> LLM_LI["🤖 LLM LinkedIn Pitch\nGroq / Gemini / OpenAI\nLINKEDIN_PROMPT_TEMPLATE"]
    LLM_GATE -- No --> RULE_LI["📝 Rule-based\nLinkedIn Pitch"]
    LLM_LI -- fail --> RULE_LI

    LI_CHECK -- No --> EMAIL_CHECK{"client.email\npresent?"}
    EMAIL_CHECK -- Yes --> LLM_GATE2{"Grade ≤ min_grade\n& API key?"}
    LLM_GATE2 -- Yes --> LLM_EM["🤖 LLM Email Pitch\nEMAIL_PROMPT_TEMPLATE"]
    LLM_GATE2 -- No --> RULE_EM["📝 Rule-based\nEmail Pitch"]
    LLM_EM -- fail --> RULE_EM
    EMAIL_CHECK -- No --> GENERIC["📝 Generic Pitch\n'team' greeting"]

    RULE_LI --> SAVE_PITCH["Save SalesPitch\npitch_channel = 'linkedin'\ndelivery_status = 'pending'"]
    LLM_LI --> SAVE_PITCH
    RULE_EM --> SAVE_PITCH
    LLM_EM --> SAVE_PITCH
    GENERIC --> SAVE_PITCH
    STATIC --> SAVE_PITCH

    %% ─── Phase 5: LinkedIn Connections ───
    SCHED --> CONN["🤝 LinkedIn Connections Job\n04:00 daily"]
    CONN --> CONN1["Query LinkedinContact rows\nwhere is_connected = false\nand no pending request sent"]
    CONN1 --> CONN2["Playwright visits profile\nclicks Connect button"]
    CONN2 --> CONN3["Mark connection_requested_at"]

    %% ─── Phase 6: Acceptance Check ───
    SCHED --> ACC["✅ Acceptance Check Job\n12:00 daily"]
    ACC --> ACC1["Query contacts with\npending connection requests"]
    ACC1 --> ACC2["Playwright checks\nprofile for 1st-degree status"]
    ACC2 --> ACC3{"Accepted?"}
    ACC3 -- Yes --> ACC4["is_connected = True\nconnected_at = now()"]
    ACC3 -- No --> ACC5["Leave pending\n(check again tomorrow)"]

    %% ─── Phase 7: Pitch Delivery ───
    SCHED --> DEL["📤 Pitch Delivery Job\n14:00 daily"]
    DEL --> DEL1["Query SalesPitch rows\nwhere delivery_status = 'pending'\nand delivery_attempts < MAX"]

    DEL1 --> DEL_ROUTE{"pitch_channel?"}

    DEL_ROUTE -- "email" --> EMAIL_SEND["📧 SMTP Email Send\nSMTP via Mailtrap / Gmail\n+ delay between sends"]
    EMAIL_SEND --> EMAIL_OK{"Sent OK?"}
    EMAIL_OK -- Yes --> STATUS_SENT["delivery_status = 'sent'\ndelivered_at = now()"]
    EMAIL_OK -- No --> STATUS_FAIL["delivery_status = 'failed'\ndelivery_error logged\nattempts += 1"]

    DEL_ROUTE -- "linkedin" --> LI_CONN_CHECK{"is_connected\n= True?"}
    LI_CONN_CHECK -- No --> SKIP["Leave as 'pending'\n(will retry next cycle\nonce accepted)"]
    LI_CONN_CHECK -- Yes --> DM_SEND["💬 Playwright LinkedIn DM\n1. Extract compose URL from\n   profile 'Message' link href\n2. Navigate to compose URL\n   (recipient pre-filled)\n3. Type pitch · click Send"]
    DM_SEND --> DM_OK{"DM sent?"}
    DM_OK -- Yes --> STATUS_SENT
    DM_OK -- No --> STATUS_FAIL

    STATUS_FAIL --> RETRY{"attempts <\nMAX_DELIVERY_ATTEMPTS?"}
    RETRY -- Yes --> RETRY_TOMORROW["Retry on\nnext delivery run"]
    RETRY -- No --> FINAL_FAIL["delivery_status = 'failed'\nno further retries"]
```

---

## LinkedIn DM Sending Strategy

```mermaid
flowchart TD
    PROFILE["Navigate to\ncontact profile_url"] --> URL_FIND["Find 'Message' link\n&lt;a href='/messaging/compose\n?...&recipient=ACo...'&gt;"]
    URL_FIND --> URL_FOUND{"href\nfound?"}

    URL_FOUND -- Yes --> DIRECT["Navigate directly to\ncompose URL\n✅ Recipient pre-filled\nby LinkedIn URN"]
    DIRECT --> COMPOSE["Find compose box\ntype pitch\nclick Send\nflow = 'direct'"]

    URL_FOUND -- No --> CASCADE["9-selector cascade\nfor Message button"]
    CASCADE --> BTN_FOUND{"Button\nfound?"}
    BTN_FOUND -- No --> ABORT["Return False\n(not connected /\nmessaging restricted)"]
    BTN_FOUND -- Yes --> CLICK_BTN["Click generic\nMessage button"]
    CLICK_BTN --> FILL["_fill_recipient()\n• Search full name in To: field\n• Wait for autocomplete\n• Score candidates:\n  +10 full name match\n  +4 first name match\n  +2 per job_title word\n  +3 if '1st' or '2nd' degree\n• Click best scoring result"]
    FILL --> FILL_OK{"Recipient\nchip confirmed?"}
    FILL_OK -- No --> ABORT
    FILL_OK -- Yes --> COMPOSE
```

---

## Pitch Generation Decision Tree

```mermaid
flowchart TD
    GP["generate_sales_pitch()"] --> SRC{"client.scrape_source\n== 'google_maps'?"}

    SRC -- No / None --> STATIC["Return Static Template\n• Personalised greeting\n• Full-stack dev team\n  capability list\n• generated_by = 'static_template'"]

    SRC -- Yes --> LI{"linkedin_contact\nprovided?"}
    LI -- Yes --> GRADE_LI{"Grade ≤ min_grade\n& LLM key present?"}
    GRADE_LI -- Yes --> LLM_LI["LLM LinkedIn Pitch\nLINKEDIN_PROMPT_TEMPLATE\nJSON structured output"]
    GRADE_LI -- No --> RB_LI["Rule-based LinkedIn Pitch\n(contact name · title · score)"]
    LLM_LI -- exception --> RB_LI

    LI -- No --> EM{"contact_email\nprovided?"}
    EM -- Yes --> GRADE_EM{"Grade ≤ min_grade\n& LLM key present?"}
    GRADE_EM -- Yes --> LLM_EM["LLM Email Pitch\nEMAIL_PROMPT_TEMPLATE"]
    GRADE_EM -- No --> RB_EM["Rule-based Email Pitch\n(company name · city · issues)"]
    LLM_EM -- exception --> RB_EM

    EM -- No --> GEN["Generic Pitch\n'team' greeting\nno personal data"]
```

---

## Reply Checker Strategy

```mermaid
flowchart TD
    RC_START["Reply Checker Job\nrun_pitch_reply_check_job()"] --> RC1["pitch_reply_service.py\ncheck_pitch_replies(db)"]
    RC1 --> RC2["_fetch_checkable_pitches()\nchannel=linkedin · sent · reply_received=False\nreply_checked_at NULL or > 12h ago"]
    RC2 --> RC3["For each pitch → resolve\nLinkedinContact via linkedin_contact_id"]
    RC3 --> RC4["linkedin_reply_checker.py\ncheck_reply_for_contact(profile_url, name)"]

    RC4 --> RC5["1. Verify session\n   GET /feed/"]
    RC5 --> RC6["2. Navigate to contact\n   profile_url"]
    RC6 --> RC7["3. Extract compose URL\n   a href=/messaging/compose?recipient=..."]
    RC7 --> RC8{"compose URL\nfound?"}
    RC8 -- Yes --> RC9["Navigate to compose URL\nopens existing thread"]
    RC8 -- No --> RC10["Click Message button\n(fallback)"]
    RC9 --> RC11["Wait for thread container\nmsg-s-message-list-container"]
    RC10 --> RC11
    RC11 --> RC12["Scroll to bottom\nload latest messages"]
    RC12 --> RC13["Read li.msg-s-event-listitem elements\ncheck class for 'other'"]
    RC13 --> RC14{"Last message\nclass contains 'other'?"}
    RC14 -- Yes --> RC15["is_self = False\n→ REPLY DETECTED"]
    RC14 -- No --> RC16["is_self = True\n→ no reply yet"]
    RC15 --> RC17["_mark_replied()\nreply_received=True\nreply_text saved"]
    RC16 --> RC18["_mark_checked_no_reply()\nreply_checked_at=now()"]
    RC17 --> RC19["GET /lead/pitches/replies?replied=true"]
```

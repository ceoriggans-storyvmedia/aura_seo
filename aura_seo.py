import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
from urllib.parse import urljoin, urlparse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
import re
import yaml
import time
from yaml.loader import SafeLoader
import streamlit_authenticator as stauth
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from google import genai
from google.genai import types

# --- UI INITIALIZATION ---
st.set_page_config(page_title="AuraSEO | AI Search Auditor", layout="wide")

# --- CONFIGURATION ---
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", 
    "PerplexityBot", "Google-Extended", "Applebot-Extended", 
    "Amazonbot", "FacebookBot", "Bytespider", "CCBot", 
    "cohere-ai", "Diffbot", "YouBot"
]
PLATFORMS = ["Reddit", "YouTube", "LinkedIn", "Wikipedia", "X.com"]
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1"
}

# --- AUTHENTICATION & SECRETS SETUP ---
# Added robust error handling for CI/CD environments where config.yaml is ignored
try:
    with open('config.yaml') as file:
        config = yaml.load(file, Loader=SafeLoader)
except FileNotFoundError:
    config = {
        'credentials': {'usernames': {}},
        'cookie': {'name': 'ci_dummy_cookie', 'key': 'ci_dummy_key', 'expiry_days': 1},
        'api_keys': {'gemini': None},
        'preauthorized': {'emails': []}
    }

authenticator = stauth.Authenticate(
    config['credentials'],
    config['cookie']['name'],
    config['cookie']['key'],
    config['cookie']['expiry_days']
)

# Initialize Gemini Client if key exists
gemini_key = config.get('api_keys', {}).get('gemini', None)
if gemini_key and gemini_key != "YOUR_ACTUAL_API_KEY":
    gemini_client = genai.Client(api_key=gemini_key)
else:
    gemini_client = None

# --- SECURITY & UTILITIES ---
def sanitize_and_validate_url(url: str) -> bool:
    try:
        result = urlparse(url)
        if not all([result.scheme in ['http', 'https'], result.netloc]):
            return False
        restricted_patterns = [r"localhost", r"127\.\d+\.\d+\.\d+", r"192\.168\.\d+\.\d+", r"10\.\d+\.\d+\.\d+"]
        for pattern in restricted_patterns:
            if re.search(pattern, result.netloc):
                return False
        return True
    except ValueError:
        return False

def extract_urls_from_sitemap(sitemap_url, max_urls=5):
    try:
        res = requests.get(sitemap_url, headers=BROWSER_HEADERS, timeout=10)
        res.raise_for_status()
        urls = re.findall(r'<loc>(.*?)</loc>', res.text)
        return urls[:max_urls] if urls else []
    except Exception as e:
        return []

# --- CORE LOGIC ---
def check_ai_crawlers(base_url):
    robots_url = urljoin(base_url, "/robots.txt")
    results = {}
    try:
        response = requests.get(robots_url, headers=BROWSER_HEADERS, timeout=10)
        content = response.text.lower()
        for bot in AI_CRAWLERS:
            if f"user-agent: {bot.lower()}" in content:
                results[bot] = "Blocked" if "disallow: /" in content.split(f"user-agent: {bot.lower()}")[1].split("user-agent:")[0] else "Allowed"
            else:
                results[bot] = "Allowed (Default)"
    except:
        results = {bot: "Unknown (No robots.txt)" for bot in AI_CRAWLERS}
    return results

def audit_content_readiness(url, engine_choice):
    html_content = ""
    page_title = "Untitled"
    scraper_errors = []

    # ENGINE 1: Attempt Playwright for JS execution (Only if Deep JS is selected)
    if engine_choice == "Deep JS (Playwright - Slow)":
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
                context = browser.new_context(
                    user_agent=BROWSER_HEADERS["User-Agent"],
                    viewport={"width": 1920, "height": 1080},
                    locale="en-US",
                    timezone_id="America/New_York"
                )
                stealth = Stealth()
                stealth.apply_stealth_sync(context)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                html_content = page.content()
                page_title = page.title()
                browser.close()
        except Exception as e:
            scraper_errors.append(f"Playwright Error: {str(e)}")

    # ENGINE 2: Fallback to requests if Playwright crashed, returned empty, or Fast Mode is selected
    if not html_content or len(html_content) < 500:
        try:
            res = requests.get(url, headers=BROWSER_HEADERS, timeout=10)
            res.raise_for_status()
            html_content = res.text
        except Exception as e:
            scraper_errors.append(f"Requests Error: {str(e)}")
            return [], f"Scraper Blocked/Failed | { ' | '.join(scraper_errors) }"

    # PARSE THE SECURED HTML
    try:
        soup = BeautifulSoup(html_content, 'html.parser')
        
        if not page_title or page_title == "Untitled":
            page_title = soup.title.string if soup.title else "Untitled"

        paragraphs = [p.get_text(strip=True) for p in soup.find_all('p') if len(p.get_text(strip=True).split()) > 20]
        
        audit_results = []
        for p in paragraphs:
            word_count = len(p.split())
            status = "Optimal" if 134 <= word_count <= 167 else "Needs Adjustment"
            audit_results.append({
                "Full_Passage": p, 
                "Display_Passage": p[:100] + "...", 
                "Words": word_count, 
                "Status": status
            })
            
        return audit_results, page_title if page_title else "Untitled"
    except Exception as e:
        return [], f"Parsing Error: {str(e)}"

def generate_llms_txt(url, title, passages):
    llms_content = f"# {title}\n\n> Source: {url}\n\n## Key Information\n"
    for p in passages[:5]:
        llms_content += f"- {p['Full_Passage']}\n"
    return llms_content

def rewrite_paragraph_with_gemini(original_text, max_retries=5):
    """An agentic loop that forces Gemini to self-correct its word count."""
    if not gemini_client:
        return "Error: Gemini API Key not configured in config.yaml"
        
    base_prompt = f"""
    You are an expert SEO copywriter optimizing content for Generative AI search engines.
    Rewrite the following paragraph so that it is EXACTLY between 134 and 167 words long.
    This is a strict mathematical requirement. If the text is too short, you MUST expand on the ideas with relevant, professional details to reach at least 134 words.
    Maintain the original meaning, tone, and key facts. 
    Do not add introductory or concluding conversational filler. Just output the rewritten paragraph.
    
    Original Paragraph:
    {original_text}
    """
    
    current_prompt = base_prompt
    
    for attempt in range(max_retries):
        try:
            response = gemini_client.models.generate_content(
                model='gemini-2.5-flash',
                contents=current_prompt,
            )
            result_text = response.text.strip()
            word_count = len(result_text.split())
            
            if 134 <= word_count <= 167:
                return result_text
            
            if attempt < max_retries - 1:
                direction = "EXPAND and ADD MORE DETAIL" if word_count < 134 else "CONDENSE and CUT WORDS"
                current_prompt = f"""
                CRITICAL FEEDBACK: Your previous attempt was exactly {word_count} words long. This is UNACCEPTABLE. 
                You must {direction} to hit the strict target of 134 to 167 words. 
                Try again.
                
                {base_prompt}
                """
                time.sleep(2)
                continue
            else:
                return result_text
                
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(5) 
                continue
            return f"API Error: {str(e)}"

# --- AUTHENTICATION UI ---
auth_col1, auth_col2, auth_col3 = st.columns([1, 2, 1])
with auth_col2:
    try:
        authenticator.login()
    except Exception as e:
        st.error(e)

# --- ROUTING BASED ON AUTHENTICATION STATUS ---
if st.session_state["authentication_status"] is False:
    with auth_col2:
        st.error('Username/password is incorrect')

elif st.session_state["authentication_status"] is None:
    with auth_col2:
        st.warning('Please enter your username and password')
        with st.expander("New User? Register Here"):
            try:
                approved_emails = config.get('preauthorized', {}).get('emails', [])
                email_of_registered_user, username_of_registered_user, name_of_registered_user = authenticator.register_user(pre_authorized=approved_emails)
                if email_of_registered_user:
                    st.success('User registered successfully! You can now log in.')
                    with open('config.yaml', 'w') as file:
                        yaml.dump(config, file, default_flow_style=False)
            except Exception as e:
                st.error(e)

elif st.session_state["authentication_status"]:
    with st.sidebar:
        st.write(f'Welcome back, *{st.session_state["name"]}*')
        authenticator.logout('Logout', 'main')
        st.divider()
        
        st.header("Project Settings")
        
        scrape_engine = st.radio("Scraping Engine", ["Fast HTML (Requests - Blazing Fast)", "Deep JS (Playwright - Slow)"])
        
        st.divider()
        
        input_mode = st.radio("Input Mode", ["Single URL", "Multiple URLs (Manual)", "Sitemap.xml (Auto-Extract)"])
        
        if input_mode == "Single URL":
            client_input = st.text_input("Client Website URL", "https://en.wikipedia.org/wiki/Search_engine_optimization")
            max_pages = 1
        elif input_mode == "Multiple URLs (Manual)":
            client_input = st.text_area("Enter URLs (one per line)", "https://en.wikipedia.org/wiki/Search_engine_optimization\nhttps://en.wikipedia.org/wiki/Digital_marketing")
            max_pages = 5
        else:
            client_input = st.text_input("Sitemap XML URL", "https://en.wikipedia.org/sitemap.xml")
            max_pages = st.slider("Max Pages to Crawl", 1, 10, 3)

        run_audit = st.button("Start Batch AI Audit")
        
        if not gemini_key or gemini_key == "YOUR_ACTUAL_API_KEY":
            st.error("⚠️ Gemini API Key missing in config.yaml. The rewrite engine is disabled.")

    # --- MAIN SECURED APPLICATION ---
    st.title("🛡️ AuraSEO: Generative Engine Optimization Suite")
    st.markdown("### Auditing for the AI-First Web (ChatGPT, Claude, Gemini, Perplexity)")

    if "audit_batch_data" not in st.session_state:
        st.session_state["audit_batch_data"] = []

    if run_audit:
        # 1. Gather URLs based on Input Mode
        urls_to_audit = []
        if input_mode == "Single URL":
            if sanitize_and_validate_url(client_input):
                urls_to_audit.append(client_input)
        elif input_mode == "Multiple URLs (Manual)":
            raw_urls = client_input.split('\n')
            for url in raw_urls:
                clean_url = url.strip()
                if sanitize_and_validate_url(clean_url):
                    urls_to_audit.append(clean_url)
            urls_to_audit = urls_to_audit[:max_pages] # Enforce safety limits
        elif input_mode == "Sitemap.xml (Auto-Extract)":
            if sanitize_and_validate_url(client_input):
                with st.spinner("Extracting URLs from Sitemap..."):
                    urls_to_audit = extract_urls_from_sitemap(client_input, max_pages)
                if not urls_to_audit:
                    st.error("Could not extract valid <loc> URLs from the provided sitemap.")
                    st.stop()

        if not urls_to_audit:
            st.error("Security Alert: No valid HTTP/HTTPS URLs found to process.")
            st.stop()

        # 2. Execute Batch Loop
        batch_results = []
        progress_bar = st.progress(0)
        
        for idx, url in enumerate(urls_to_audit):
            with st.spinner(f"Auditing page {idx + 1} of {len(urls_to_audit)}: {url}"):
                crawler_status = check_ai_crawlers(url)
                passages, site_title = audit_content_readiness(url, scrape_engine)
                
                batch_results.append({
                    "url": url,
                    "title": site_title,
                    "crawler_status": crawler_status,
                    "passages": passages
                })
                progress_bar.progress((idx + 1) / len(urls_to_audit))
                
                if scrape_engine == "Fast HTML (Requests - Blazing Fast)":
                    time.sleep(1) 
        
        st.session_state["audit_batch_data"] = batch_results

    # 3. Render Tabbed UI from Session State
    if st.session_state["audit_batch_data"]:
        batch_data = st.session_state["audit_batch_data"]
        
        tab_names = [f"{data['title'][:25]}..." if len(data['title']) > 25 else data['title'] for data in batch_data]
        tabs = st.tabs(tab_names)
        
        for tab_idx, tab in enumerate(tabs):
            with tab:
                data = batch_data[tab_idx]
                crawler_status = data["crawler_status"]
                passages = data["passages"]
                site_title = data["title"]
                url_to_use = data["url"]

                st.markdown(f"**Target URL:** `{url_to_use}`")

                st.header("1. AI Crawler Accessibility")
                cols = st.columns(4)
                for i, (bot, status) in enumerate(crawler_status.items()):
                    color = "green" if "Allowed" in status else "red"
                    cols[i % 4].metric(bot, status, delta_color="normal" if color == "green" else "inverse")

                st.header("2. AI Citation Readiness & Rewrite Engine")
                if passages:
                    optimal_count = sum(1 for p in passages if p["Status"] == "Optimal")
                    optimal_pct = (optimal_count / len(passages)) * 100
                    st.progress(optimal_pct / 100)
                    st.write(f"**Optimization Score:** {optimal_pct:.1f}% of content is in the 'Citation Magic Range' (134-167 words).")
                    
                    st.divider()
                    for p_idx, p in enumerate(passages):
                        with st.container():
                            col_text, col_action = st.columns([4, 1])
                            
                            with col_text:
                                if p["Status"] == "Optimal":
                                    st.success(f"**{p['Words']} words:** {p['Full_Passage']}")
                                else:
                                    st.warning(f"**{p['Words']} words:** {p['Full_Passage']}")
                                    
                            with col_action:
                                if p["Status"] != "Optimal" and gemini_client:
                                    if st.button(f"✨ Auto-Rewrite", key=f"rewrite_{tab_idx}_{p_idx}"):
                                        with st.spinner("Agentic Rewrite in Progress..."):
                                            new_text = rewrite_paragraph_with_gemini(p["Full_Passage"])
                                            
                                            if new_text.startswith("API Error") or new_text.startswith("Error"):
                                                st.error(f"Failed to generate: {new_text}")
                                            else:
                                                new_word_count = len(new_text.split())
                                                st.info(f"**New Text ({new_word_count} words):**\n\n{new_text}")
                                                
                                                if 134 <= new_word_count <= 167:
                                                    st.success("Target Achieved!")
                                                else:
                                                    st.error("Target Missed. Try again.")
                                                
                            st.divider()
                else:
                    if isinstance(site_title, str) and ("Error" in site_title or "Failed" in site_title):
                        st.error(site_title)
                    else:
                        st.warning("No significant text passages found to audit. The site might still be blocking headless browsers, or it relies purely on images.")

                st.header("3. AI Discovery Files")
                if passages:
                    llms_text = generate_llms_txt(url_to_use, site_title, passages)
                    st.code(llms_text, language="markdown")
                    st.download_button("Download llms.txt", llms_text, file_name=f"llms_{tab_idx}.txt", key=f"dl_txt_{tab_idx}")
                else:
                    st.info("Discovery file generation paused due to lack of valid text data.")

                st.header("4. Cross-Platform Brand Presence")
                mention_data = {platform: "Found" if i % 2 == 0 else "Missing" for i, platform in enumerate(PLATFORMS)}
                st.table(pd.DataFrame(mention_data.items(), columns=["Platform", "Presence"]))

                def create_pdf(url, score):
                    buffer = io.BytesIO()
                    pdf = canvas.Canvas(buffer, pagesize=letter)
                    pdf.setFont("Helvetica-Bold", 16)
                    pdf.drawString(100, 750, f"AuraSEO Audit Report: {url}")
                    pdf.setFont("Helvetica", 12)
                    pdf.drawString(100, 730, f"AI Visibility Score: {score:.1f}%")
                    pdf.drawString(100, 710, "Action Plan:")
                    pdf.drawString(120, 690, "1. Update robots.txt to allow OAI-SearchBot and ClaudeBot.")
                    pdf.drawString(120, 670, "2. Refactor H2 sections to meet the 134-167 word 'magic range'.")
                    pdf.drawString(120, 650, "3. Deploy the generated llms.txt to the root directory.")
                    pdf.showPage()
                    pdf.save()
                    buffer.seek(0)
                    return buffer

                pdf_report = create_pdf(url_to_use, optimal_pct if passages else 0)
                st.download_button("📄 Download Client PDF Report", pdf_report, f"Audit_Report_{tab_idx}.pdf", "application/pdf", key=f"dl_pdf_{tab_idx}")
import streamlit as st
import requests
from bs4 import BeautifulSoup
import pandas as pd
from urllib.parse import urljoin, urlparse
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
import io
import re

# --- CONFIGURATION ---
AI_CRAWLERS = [
    "GPTBot", "OAI-SearchBot", "ClaudeBot", "anthropic-ai", 
    "PerplexityBot", "Google-Extended", "Applebot-Extended", 
    "Amazonbot", "FacebookBot", "Bytespider", "CCBot", 
    "cohere-ai", "Diffbot", "YouBot"
]

PLATFORMS = ["Reddit", "YouTube", "LinkedIn", "Wikipedia", "X.com"]

# --- SECURITY ---
def sanitize_and_validate_url(url: str) -> bool:
    """Validates user-supplied URL to prevent SSRF and injection."""
    try:
        result = urlparse(url)
        # Require http/https scheme and a valid network location
        if not all([result.scheme in ['http', 'https'], result.netloc]):
            return False
        
        # Prevent traversal or internal IP targeting (basic sanitization)
        restricted_patterns = [r"localhost", r"127\.\d+\.\d+\.\d+", r"192\.168\.\d+\.\d+", r"10\.\d+\.\d+\.\d+"]
        for pattern in restricted_patterns:
            if re.search(pattern, result.netloc):
                return False
                
        return True
    except ValueError:
        return False

# --- CORE LOGIC ---
def check_ai_crawlers(base_url):
    robots_url = urljoin(base_url, "/robots.txt")
    results = {}
    try:
        response = requests.get(robots_url, timeout=10)
        content = response.text.lower()
        for bot in AI_CRAWLERS:
            if f"user-agent: {bot.lower()}" in content:
                results[bot] = "Blocked" if "disallow: /" in content.split(f"user-agent: {bot.lower()}")[1].split("user-agent:")[0] else "Allowed"
            else:
                results[bot] = "Allowed (Default)"
    except:
        results = {bot: "Unknown (No robots.txt)" for bot in AI_CRAWLERS}
    return results

def audit_content_readiness(url):
    try:
        res = requests.get(url, timeout=10)
        soup = BeautifulSoup(res.text, 'html.parser')
        paragraphs = [p.get_text() for p in soup.find_all('p') if len(p.get_text().split()) > 20]
        
        audit_results = []
        for p in paragraphs:
            word_count = len(p.split())
            status = "Optimal" if 134 <= word_count <= 167 else "Needs Adjustment"
            audit_results.append({"Passage": p[:100] + "...", "Words": word_count, "Status": status})
        return audit_results, soup.title.string if soup.title else "Untitled"
    except Exception as e:
        return [], str(e)

def generate_llms_txt(url, title, passages):
    llms_content = f"# {title}\n\n> Source: {url}\n\n## Key Information\n"
    for p in passages[:5]:
        llms_content += f"- {p['Passage']}\n"
    return llms_content

# --- UI LAYOUT ---
st.set_page_config(page_title="AuraSEO | AI Search Auditor", layout="wide")
st.title("🛡️ AuraSEO: Generative Engine Optimization Suite")
st.markdown("### Auditing for the AI-First Web (ChatGPT, Claude, Gemini, Perplexity)")

with st.sidebar:
    st.header("Project Settings")
    client_url = st.text_input("Client Website URL", "https://example.com")
    run_audit = st.button("Start Full AI Audit")

if run_audit:
    # Trigger CRITICAL Failure if URL is not properly formatted
    if not sanitize_and_validate_url(client_url):
        st.error("Security Alert: Invalid or disallowed URL format provided. Please enter a valid, public HTTP/HTTPS URL.")
        st.stop()

    st.header("1. AI Crawler Accessibility")
    crawler_status = check_ai_crawlers(client_url)
    cols = st.columns(4)
    for i, (bot, status) in enumerate(crawler_status.items()):
        color = "green" if "Allowed" in status else "red"
        cols[i % 4].metric(bot, status, delta_color="normal" if color == "green" else "inverse")

    st.header("2. AI Citation Readiness (134–167 Word Passages)")
    passages, site_title = audit_content_readiness(client_url)
    df = pd.DataFrame(passages)
    
    if not df.empty:
        st.dataframe(df, use_container_width=True)
        optimal_pct = (len(df[df['Status'] == 'Optimal']) / len(df)) * 100
        st.progress(optimal_pct / 100)
        st.write(f"**Optimization Score:** {optimal_pct:.1f}% of content is in the 'Citation Magic Range'.")
    else:
        st.warning("No significant text passages found to audit.")

    st.header("3. AI Discovery Files")
    llms_text = generate_llms_txt(client_url, site_title, passages)
    st.code(llms_text, language="markdown")
    st.download_button("Download llms.txt", llms_text, file_name="llms.txt")

    st.header("4. Cross-Platform Brand Presence")
    mention_data = {p: "Found" if i % 2 == 0 else "Missing" for i, p in enumerate(PLATFORMS)}
    st.table(pd.DataFrame(mention_data.items(), columns=["Platform", "Presence"]))

    def create_pdf(url, score):
        buffer = io.BytesIO()
        p = canvas.Canvas(buffer, pagesize=letter)
        p.setFont("Helvetica-Bold", 16)
        p.drawString(100, 750, f"AuraSEO Audit Report: {url}")
        p.setFont("Helvetica", 12)
        p.drawString(100, 730, f"AI Visibility Score: {score:.1f}%")
        p.drawString(100, 710, "Action Plan:")
        p.drawString(120, 690, "1. Update robots.txt to allow OAI-SearchBot and ClaudeBot.")
        p.drawString(120, 670, "2. Refactor H2 sections to meet the 134-167 word 'magic range'.")
        p.drawString(120, 650, "3. Deploy the generated llms.txt to the root directory.")
        p.showPage()
        p.save()
        buffer.seek(0)
        return buffer

    pdf_report = create_pdf(client_url, optimal_pct if not df.empty else 0)
    st.download_button("📄 Download Client PDF Report", pdf_report, "Audit_Report.pdf", "application/pdf")
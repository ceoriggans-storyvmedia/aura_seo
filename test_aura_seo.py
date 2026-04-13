import pytest
import responses
from aura_seo import sanitize_and_validate_url, check_ai_crawlers, audit_content_readiness, generate_llms_txt

# --- SECURITY TESTS ---

def test_url_sanitization_valid():
    """Test that valid, external HTTP/HTTPS URLs pass."""
    assert sanitize_and_validate_url("https://example.com") is True
    assert sanitize_and_validate_url("http://www.test-site.org/page?query=1") is True

def test_url_sanitization_invalid_scheme():
    """Test that non-HTTP/HTTPS schemes are blocked."""
    assert sanitize_and_validate_url("ftp://example.com") is False
    assert sanitize_and_validate_url("file:///etc/passwd") is False
    assert sanitize_and_validate_url("javascript:alert(1)") is False

def test_url_sanitization_internal_ips():
    """Test that internal and loopback IP addresses are blocked to prevent SSRF."""
    assert sanitize_and_validate_url("http://localhost:8000") is False
    assert sanitize_and_validate_url("https://127.0.0.1/admin") is False
    assert sanitize_and_validate_url("http://192.168.1.100") is False
    assert sanitize_and_validate_url("http://10.0.0.5") is False

def test_url_sanitization_malformed():
    """Test that completely malformed strings fail gracefully."""
    assert sanitize_and_validate_url("not_a_url") is False
    assert sanitize_and_validate_url("") is False

# --- CORE LOGIC TESTS ---

@responses.activate
def test_check_ai_crawlers_allowed_and_blocked():
    """Test robots.txt parsing for allowed and blocked AI bots."""
    base_url = "https://example.com"
    robots_url = "https://example.com/robots.txt"
    
    mock_robots_txt = """
    User-agent: *
    Allow: /
    
    User-agent: GPTBot
    Disallow: /
    
    User-agent: ClaudeBot
    Allow: /
    """
    
    responses.add(responses.GET, robots_url, body=mock_robots_txt, status=200)
    
    results = check_ai_crawlers(base_url)
    
    assert results["GPTBot"] == "Blocked"
    assert results["ClaudeBot"] == "Allowed"
    assert results["PerplexityBot"] == "Allowed (Default)"

@responses.activate
def test_audit_content_readiness_magic_range():
    """Test that the parser correctly flags passages inside and outside the 134-167 word range."""
    test_url = "https://example.com/article"
    
    # Generate mock paragraphs
    short_p = "This is a short paragraph. " * 5  # ~30 words
    magic_p = "This is a perfectly sized paragraph designed to hit the magic range. " * 12 # ~144 words
    long_p = "This is a very long paragraph that goes on forever. " * 30 # ~330 words
    
    mock_html = f"""
    <html>
        <head><title>Test Article</title></head>
        <body>
            <p>{short_p}</p>
            <p>{magic_p}</p>
            <p>{long_p}</p>
        </body>
    </html>
    """
    
    responses.add(responses.GET, test_url, body=mock_html, status=200)
    
    passages, title = audit_content_readiness(test_url)
    
    assert title == "Test Article"
    assert len(passages) == 3
    assert passages[0]["Status"] == "Needs Adjustment"
    assert passages[1]["Status"] == "Optimal"
    assert passages[2]["Status"] == "Needs Adjustment"

def test_generate_llms_txt():
    """Test that llms.txt is formatted correctly."""
    url = "https://example.com"
    title = "Test Site"
    passages = [
        {"Passage": "Key point one.", "Words": 150, "Status": "Optimal"},
        {"Passage": "Key point two.", "Words": 140, "Status": "Optimal"}
    ]
    
    result = generate_llms_txt(url, title, passages)
    
    assert "# Test Site" in result
    assert "> Source: https://example.com" in result
    assert "- Key point one." in result
    assert "- Key point two." in result
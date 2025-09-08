#!/usr/bin/env python3
"""Test script for site detection logic without network calls"""

import sys
import os

# Mock the requests to avoid network calls
class MockResponse:
    def __init__(self, text="", status_code=200):
        self.text = text
        self.status_code = status_code
        self.content = text.encode() if isinstance(text, str) else text
        self.headers = {}

class MockSession:
    def get(self, url, **kwargs):
        if 'robots.txt' in url:
            if 'myshopify.com' in url:
                return MockResponse("User-agent: *\nSitemap: https://example.myshopify.com/sitemap.xml\n# Shopify\n", 200)
            else:
                return MockResponse("", 404)
        return MockResponse("", 404)
    
    def head(self, url, **kwargs):
        return MockResponse("", 404)

# Monkey patch requests in the module
import sitemap_scraper
original_session = sitemap_scraper.requests.Session

def mock_session():
    return MockSession()

sitemap_scraper.requests.Session = mock_session

# Now import and test
from sitemap_scraper import SitemapScraper

def test_detection_logic():
    """Test detection logic with various URL patterns"""
    
    test_cases = [
        {
            "url": "https://example.myshopify.com/sitemap.xml",
            "expected_type": "ecommerce",
            "expected_platform": "Shopify",
            "description": "Shopify domain"
        },
        {
            "url": "https://shop.example.com/sitemap_products_1.xml",
            "expected_type": "ecommerce",
            "expected_platform": "Shopify",
            "description": "Shopify product sitemap"
        },
        {
            "url": "https://store.example.com/sitemap_collections_1.xml",
            "expected_type": "ecommerce",
            "expected_platform": "Shopify",
            "description": "Shopify collections sitemap"
        },
        {
            "url": "https://example.com/product-sitemap.xml",
            "expected_type": "ecommerce",
            "expected_platform": "WooCommerce",
            "description": "WooCommerce sitemap"
        },
        {
            "url": "https://example.mybigcommerce.com/sitemap.xml",
            "expected_type": "ecommerce",
            "expected_platform": "BigCommerce",
            "description": "BigCommerce domain"
        },
        {
            "url": "https://shop.example.com/sitemap.xml",
            "expected_type": "ecommerce",
            "expected_platform": "Unknown",
            "description": "Generic shop subdomain"
        },
        {
            "url": "https://blog.example.com/sitemap.xml",
            "expected_type": "standard",
            "expected_platform": None,
            "description": "Standard blog site"
        },
    ]
    
    print("Site Detection Logic Test")
    print("="*60)
    
    passed = 0
    failed = 0
    
    for test in test_cases:
        print(f"\nTest: {test['description']}")
        print(f"URL: {test['url']}")
        
        try:
            scraper = SitemapScraper(test['url'], shopify_mode=False)
            site_info = scraper.detect_site_type()
            
            # Check type
            type_match = site_info['type'] == test['expected_type']
            platform_match = site_info['platform'] == test['expected_platform']
            
            print(f"Expected: type={test['expected_type']}, platform={test['expected_platform']}")
            print(f"Detected: type={site_info['type']}, platform={site_info['platform']}")
            
            if type_match and platform_match:
                print("✅ PASSED")
                passed += 1
            else:
                print("❌ FAILED")
                failed += 1
                if not type_match:
                    print(f"  Type mismatch: expected '{test['expected_type']}', got '{site_info['type']}'")
                if not platform_match:
                    print(f"  Platform mismatch: expected '{test['expected_platform']}', got '{site_info['platform']}'")
            
            if site_info['indicators']:
                print("Indicators found:")
                for ind in site_info['indicators']:
                    print(f"  - {ind}")
                    
        except Exception as e:
            print(f"❌ ERROR: {e}")
            failed += 1
    
    print("\n" + "="*60)
    print(f"Results: {passed} passed, {failed} failed")
    return failed == 0

if __name__ == "__main__":
    success = test_detection_logic()
    sys.exit(0 if success else 1)
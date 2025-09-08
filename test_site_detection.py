#!/usr/bin/env python3
"""Test script for site type detection functionality"""

from sitemap_scraper import SitemapScraper

def test_site_detection():
    """Test site type detection for various URLs"""
    
    test_sites = [
        ("https://example.myshopify.com/sitemap.xml", "Shopify"),
        ("https://shop.example.com/sitemap_products_1.xml", "Shopify"),
        ("https://store.example.com/sitemap.xml", "E-commerce"),
        ("https://example.com/product-sitemap.xml", "WooCommerce"),
        ("https://example.mybigcommerce.com/sitemap.xml", "BigCommerce"),
        ("https://example.com/sitemap.xml", "Standard"),
        ("https://blog.example.com/sitemap.xml", "Standard"),
    ]
    
    print("Testing Site Type Detection")
    print("="*60)
    
    for url, expected in test_sites:
        print(f"\nTesting: {url}")
        print(f"Expected: {expected}")
        
        try:
            # Create scraper instance
            scraper = SitemapScraper(url, shopify_mode=False)
            
            # Detect site type
            site_info = scraper.detect_site_type()
        except Exception as e:
            print(f"  Error during detection: {e}")
            print("-"*40)
            continue
        
        print(f"Detected:")
        print(f"  Type: {site_info['type']}")
        print(f"  Platform: {site_info['platform'] or 'None'}")
        print(f"  Confidence: {site_info['confidence']}")
        
        if site_info['indicators']:
            print(f"  Indicators:")
            for indicator in site_info['indicators']:
                print(f"    - {indicator}")
        
        # Check if detection matches expectation
        if expected == "Shopify" and site_info['platform'] == "Shopify":
            print("  ✅ Correct detection")
        elif expected == "WooCommerce" and site_info['platform'] == "WooCommerce":
            print("  ✅ Correct detection")
        elif expected == "BigCommerce" and site_info['platform'] == "BigCommerce":
            print("  ✅ Correct detection")
        elif expected == "E-commerce" and site_info['type'] == "ecommerce":
            print("  ✅ Correct e-commerce detection")
        elif expected == "Standard" and site_info['type'] == "standard":
            print("  ✅ Correct standard site detection")
        else:
            print(f"  ❌ Mismatch - expected {expected}")
        
        print("-"*40)

if __name__ == "__main__":
    test_site_detection()
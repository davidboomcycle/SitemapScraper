#!/usr/bin/env python3
"""Test script for Shopify mode functionality"""

from sitemap_scraper import SitemapScraper

def test_shopify_detection():
    """Test Shopify site detection and filtering"""
    
    # Test URLs
    test_urls = [
        "https://example.myshopify.com/products/test-product",
        "https://store.example.com/collections/all",
        "https://shop.example.com/collections/best-sellers",
        "https://example.com/products/item-123",
        "https://example.com/pages/about",
        "https://example.com/cart",
        "https://example.com/checkout",
    ]
    
    # Initialize scraper in Shopify mode
    scraper = SitemapScraper("https://example.com/sitemap.xml", shopify_mode=True, skip_products=True)
    
    print("Testing Shopify URL detection:")
    print("-" * 50)
    
    for url in test_urls:
        is_product = scraper._is_shopify_product_page(url)
        is_collection = scraper._is_shopify_collection_page(url)
        is_system = scraper._is_shopify_system_page(url)
        
        print(f"\nURL: {url}")
        print(f"  Is Product: {is_product}")
        print(f"  Is Collection: {is_collection}")
        print(f"  Is System Page: {is_system}")
        
        if scraper.skip_products and is_product:
            print(f"  -> Would be SKIPPED (product page)")
        elif is_system:
            print(f"  -> Would be SKIPPED (system page)")
        elif is_collection:
            print(f"  -> Would be INCLUDED (collection page)")
        else:
            print(f"  -> Would be INCLUDED (regular page)")

if __name__ == "__main__":
    test_shopify_detection()
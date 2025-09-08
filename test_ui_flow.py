#!/usr/bin/env python3
"""Test script to demonstrate the improved user interface flow"""

from sitemap_scraper import SitemapScraper

def simulate_detection_scenarios():
    """Show what the user interface will look like for different scenarios"""
    
    scenarios = [
        {
            "name": "Clear Shopify Site",
            "url": "https://example.myshopify.com/sitemap.xml",
            "description": "Site with obvious Shopify indicators"
        },
        {
            "name": "Custom Domain Shopify",
            "url": "https://customdomain.com/sitemap.xml", 
            "description": "Shopify site with custom domain (no obvious indicators)"
        },
        {
            "name": "WooCommerce Site",
            "url": "https://example.com/product-sitemap.xml",
            "description": "WordPress site with WooCommerce"
        },
        {
            "name": "Standard Website",
            "url": "https://blog.example.com/sitemap.xml",
            "description": "Regular website or blog"
        }
    ]
    
    print("User Interface Flow Simulation")
    print("="*60)
    print("This shows what users will see for different site types")
    print("="*60)
    
    for scenario in scenarios:
        print(f"\n{'='*80}")
        print(f"SCENARIO: {scenario['name']}")
        print(f"URL: {scenario['url']}")
        print(f"Description: {scenario['description']}")
        print(f"{'='*80}")
        
        try:
            # Create scraper and detect site type
            scraper = SitemapScraper(scenario['url'], shopify_mode=False)
            site_info = scraper.detect_site_type()
            
            # Simulate what user would see
            print(f"\n📊 Detection Results:")
            print(f"  Site Type: {site_info['type'].upper()}")
            print(f"  Platform: {site_info['platform'] or 'Not detected'}")
            print(f"  Confidence: {site_info['confidence'].upper()}")
            
            if site_info['indicators']:
                print(f"\n🔍 Indicators found:")
                for indicator in site_info['indicators']:
                    print(f"  • {indicator}")
            
            # Show what detection message user would see
            print(f"\n" + "="*60)
            print("SITE TYPE CONFIRMATION")
            print("="*60)
            
            if site_info['type'] == 'ecommerce':
                if site_info['platform'] == 'Shopify':
                    print(f"\n🛒 AUTO-DETECTED: Shopify Store (confidence: {site_info['confidence']})")
                elif site_info['platform'] == 'WooCommerce':
                    print(f"\n🛒 AUTO-DETECTED: WooCommerce Store (confidence: {site_info['confidence']})")
                else:
                    print(f"\n🛒 AUTO-DETECTED: E-commerce Site (platform unknown)")
            else:
                print(f"\n📄 AUTO-DETECTED: Standard Website (not e-commerce)")
            
            print("\nPlease confirm or select the correct site type:")
            print("\n  E-COMMERCE PLATFORMS:")
            print("  1. Shopify store")
            print("  2. WooCommerce store (WordPress)")
            print("  3. BigCommerce store")
            print("  4. Magento store")
            print("  5. Other e-commerce platform")
            print("\n  STANDARD SITES:")
            print("  6. Standard website (blog, corporate, informational)")
            print("  7. WordPress site (non-commerce)")
            print("  8. Custom CMS or static site")
            
            # Show what the default would be
            if site_info['type'] == 'ecommerce':
                if site_info['platform'] == 'Shopify':
                    default = '1'
                elif site_info['platform'] == 'WooCommerce':
                    default = '2'
                else:
                    default = '5'
            else:
                default = '6'
            
            print(f"\nDefault selection would be: {default}")
            print(f"User can easily override by pressing 1-8")
            
        except Exception as e:
            print(f"Error during detection: {e}")
        
        input("\nPress Enter to continue to next scenario...")

if __name__ == "__main__":
    simulate_detection_scenarios()
#!/usr/bin/env python3
"""
Intelligent Web Scraper with Sitemap Analysis

This script reads a sitemap.xml file, analyzes pages to determine the most important ones,
and scrapes the top 25 pages after user confirmation.

Features:
- Sitemap parsing and URL extraction
- Intelligent page importance scoring
- User confirmation interface  
- Robust web scraping with rate limiting
- Error handling and progress tracking
"""

import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import time
import re
from datetime import datetime
from typing import List, Dict, Tuple, Optional
import logging
from dataclasses import dataclass
import json
import os

@dataclass
class PageInfo:
    """Data class to store page information and scoring metrics"""
    url: str
    lastmod: Optional[str] = None
    changefreq: Optional[str] = None
    priority: Optional[float] = None
    score: float = 0.0
    depth: int = 0
    has_keywords: bool = False
    is_post: bool = False  # Track if this came from a post sitemap
    in_navigation: bool = False  # Track if this page is in main navigation
    
    def to_dict(self) -> Dict:
        return {
            'url': self.url,
            'lastmod': self.lastmod,
            'changefreq': self.changefreq,
            'priority': self.priority,
            'score': self.score,
            'depth': self.depth,
            'has_keywords': self.has_keywords,
            'is_post': self.is_post,
            'in_navigation': self.in_navigation
        }

class SitemapScraper:
    """Main scraper class for analyzing sitemaps and scraping important pages"""
    
    def __init__(self, sitemap_url: str, use_claude_api: bool = False, shopify_mode: bool = False, skip_products: bool = True):
        self.sitemap_url = sitemap_url
        self.use_claude_api = use_claude_api
        self.shopify_mode = shopify_mode
        self.skip_products = skip_products  # Skip individual product pages in Shopify mode
        self.claude_client = None
        self.navigation_urls = []  # Will store high-priority navigation URLs
        self.homepage_core_terms = []  # Will store core business terms from homepage content
        self.is_shopify_site = False  # Will be auto-detected
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
        
        # Setup logging - quiet console, detailed file
        file_handler = logging.FileHandler('scraper.log', encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.WARNING)  # Only warnings and errors to console
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        
        logging.basicConfig(
            level=logging.DEBUG,
            handlers=[file_handler, console_handler]
        )
        self.logger = logging.getLogger(__name__)
        
        # Initialize Claude API after logger is set up
        if use_claude_api:
            try:
                import anthropic
                # Disable anthropic HTTP logging
                import logging as anthropic_logging
                anthropic_logging.getLogger("anthropic").setLevel(anthropic_logging.WARNING)
                anthropic_logging.getLogger("httpx").setLevel(anthropic_logging.WARNING)
                
                api_key = os.getenv('ANTHROPIC_API_KEY')
                if api_key:
                    self.claude_client = anthropic.Anthropic(api_key=api_key)
                    print("Claude API enabled for intelligent page analysis")
                else:
                    print("ANTHROPIC_API_KEY not found. Set it as environment variable for Claude API features.")
                    self.use_claude_api = False
            except ImportError:
                print("Anthropic library not installed. Run: pip install anthropic")
                self.use_claude_api = False
        
        # Claude API cost tracking
        self.claude_input_tokens_used = 0
        self.claude_output_tokens_used = 0
        
        # Claude 3.5 Haiku pricing (2025 rates)
        self.claude_input_cost_per_million = 0.80  # $0.80 per million input tokens
        self.claude_output_cost_per_million = 4.00  # $4.00 per million output tokens
        
        # Removed 403 error tracking - now stops immediately on first error
        
        # Initialize patterns before any methods that use them
        self._initialize_patterns()
        
        # Auto-detect Shopify site
        self._detect_shopify_site()
        
    def detect_site_type(self) -> dict:
        """Detect the type of website and its characteristics"""
        site_info = {
            'type': 'standard',
            'platform': None,
            'indicators': [],
            'has_products': False,
            'has_collections': False,
            'estimated_product_pages': 0,
            'confidence': 'low'
        }
        
        try:
            # Parse domain and sitemap URL
            parsed_url = urlparse(self.sitemap_url)
            domain = parsed_url.netloc.lower()
            sitemap_path = self.sitemap_url.lower()
            
            # Check for Shopify indicators
            shopify_score = 0
            
            # Strong Shopify indicators
            if '.myshopify.com' in domain:
                site_info['indicators'].append('Shopify domain (.myshopify.com)')
                shopify_score += 100
            
            if '/sitemap_products_' in sitemap_path:
                site_info['indicators'].append('Shopify product sitemap structure')
                site_info['has_products'] = True
                shopify_score += 80
                
            if '/sitemap_collections_' in sitemap_path:
                site_info['indicators'].append('Shopify collections sitemap structure')
                site_info['has_collections'] = True
                shopify_score += 80
            
            # Medium Shopify indicators
            if 'shop.' in domain or 'store.' in domain:
                site_info['indicators'].append('E-commerce subdomain (shop/store)')
                shopify_score += 30
            
            # Try to fetch robots.txt for more clues
            try:
                robots_url = f"{parsed_url.scheme}://{parsed_url.netloc}/robots.txt"
                response = self.session.get(robots_url, timeout=5)
                if response.status_code == 200:
                    robots_content = response.text.lower()
                    
                    # Check for platform-specific patterns in robots.txt
                    if 'shopify' in robots_content:
                        site_info['indicators'].append('Shopify mentioned in robots.txt')
                        shopify_score += 50
                    
                    # Count product sitemaps
                    product_sitemap_count = robots_content.count('sitemap_products_')
                    if product_sitemap_count > 0:
                        site_info['estimated_product_pages'] = product_sitemap_count * 500  # Rough estimate
                        site_info['indicators'].append(f'Found {product_sitemap_count} product sitemaps')
                        
            except:
                pass  # robots.txt fetch failed, not critical
            
            # Check for WooCommerce indicators
            woo_score = 0
            if '/product-sitemap' in sitemap_path or '/product_cat-sitemap' in sitemap_path:
                site_info['indicators'].append('WooCommerce sitemap structure')
                woo_score += 80
            
            # Check for BigCommerce indicators
            bigcommerce_score = 0
            if '.mybigcommerce.com' in domain:
                site_info['indicators'].append('BigCommerce domain')
                bigcommerce_score += 100
            
            # Determine platform and confidence
            if shopify_score >= 80:
                site_info['type'] = 'ecommerce'
                site_info['platform'] = 'Shopify'
                site_info['confidence'] = 'high' if shopify_score >= 100 else 'medium'
                self.is_shopify_site = True
            elif woo_score >= 80:
                site_info['type'] = 'ecommerce'
                site_info['platform'] = 'WooCommerce'
                site_info['confidence'] = 'medium'
            elif bigcommerce_score >= 80:
                site_info['type'] = 'ecommerce'
                site_info['platform'] = 'BigCommerce'
                site_info['confidence'] = 'high'
            elif shopify_score >= 30 or 'shop' in domain or 'store' in domain:
                site_info['type'] = 'ecommerce'
                site_info['platform'] = 'Unknown'
                site_info['confidence'] = 'low'
                
        except Exception as e:
            self.logger.debug(f"Error detecting site type: {e}")
            
        return site_info
    
    def _detect_shopify_site(self):
        """Legacy method for backward compatibility"""
        site_info = self.detect_site_type()
        self.is_shopify_site = (site_info['platform'] == 'Shopify')
    
    def _initialize_patterns(self):
        """Initialize all URL patterns used for scoring"""
        # BUSINESS-FIRST KEYWORD HIERARCHY
        # TIER 1: CORE BUSINESS VALUE - What does the business DO/SELL? (HIGHEST PRIORITY)
        self.core_business_keywords = {
            # Homepage (most important single page)
            'home': 100,
            'index': 95,
            'main': 90,
            
            # Services & Solutions (what they DO)
            'services': 80,
            'service': 80,
            'solutions': 78,
            'offerings': 75,
            'what-we-do': 75,
            
            # Medical/Healthcare specific (procedures they PERFORM)
            'procedures': 85,
            'procedure': 85,
            'treatments': 85,
            'treatment': 85,
            'surgery': 82,
            'surgeries': 82,
            'therapy': 80,
            'therapies': 80,
            'specialties': 78,
            'specialty': 78,
            'conditions': 75,
            'condition': 75,
            
            # Products (what they SELL)
            'products': 78,
            'product': 78,
            'catalog': 75,
            'shop': 75,
            'store': 75,
            'inventory': 70,
            
            # Industry-specific service keywords
            'repair': 75,      # Plumbing, auto, etc.
            'installation': 75, # HVAC, electrical, etc.
            'maintenance': 72,
            'consultation': 70,
            'assessment': 70,
            'diagnosis': 70,
        }
        
        # TIER 2: BUSINESS SUPPORT - How they operate (MEDIUM PRIORITY)
        self.business_support_keywords = {
            'about': 40,     # About pages are important but secondary
            'about-us': 40,
            'contact': 35,   # Contact is support, not core value
            'contact-us': 35,
            'reach-us': 35,
            'pricing': 45,   # Pricing is crucial for business
            'prices': 45,
            'cost': 42,
            'rates': 42,
            'plans': 40,
            'packages': 40,
            'features': 38,
            'why-us': 35,
            'why-choose': 35,
            'faq': 30,
            'faqs': 30,
            'support': 28,
            'help': 28,
            'testimonials': 25,
            'reviews': 25,
            'case-studies': 25,
            'portfolio': 25,
            'work': 25,
            'gallery': 22,
            'news': 20,
            'blog': 5,  # Main blog landing only
        }
        
        # TIER 3: ORGANIZATIONAL INFO - Who/Where they are (LOWER PRIORITY)
        self.organizational_keywords = {
            'team': 15,      # Team pages are least important for business value
            'staff': 15,
            'doctor': 12,    # Individual staff pages
            'doctors': 18,   # Staff directory slightly better
            'physician': 12,
            'physicians': 18,
            'employee': 10,
            'employees': 15,
            'biography': 8,
            'bio': 8,
            'profile': 8,
            'profiles': 15,
            'location': 15,  # Location pages are organizational
            'locations': 20, # Locations directory slightly better
            'office': 12,
            'offices': 18,
            'branch': 12,
            'branches': 18,
            'address': 8,
            'directions': 8,
            'careers': 10,
            'jobs': 10,
            'employment': 10,
        }
        
        # Blog/News/Article detection patterns (heavy penalty)
        self.blog_post_patterns = [
            r'/blog/.+',              # ANY URL with /blog/ followed by content
            r'/post/.+',              # Any post URLs
            r'/posts/.+',             # Plural posts URLs
            r'/article/.+',           # Any article URLs  
            r'/articles/.+',          # Plural articles URLs
            r'/news/.+',              # Any news URLs - these are content marketing, not services
            r'/\d{4}/\d{2}/',         # Date-based URLs (2024/01/, etc.)
            r'/\d{4}-\d{2}-\d{2}',    # Date-based URLs (2024-01-01)
            # Long descriptive URLs (typical of blog posts)
            r'/[^/]*-[^/]*-[^/]*-[^/]*-[^/]*/',  # URLs with 4+ hyphens
            r'/how-to-[^/]+/',        # How-to articles
            r'/how-[^/]+-[^/]+/',     # How articles with multiple words
            r'/why-[^/]+/',           # Why articles  
            r'/what-[^/]+/',          # What articles
            r'/when-[^/]+/',          # When articles
            r'/where-[^/]+/',         # Where articles
            r'/\d+-[^/]+-[^/]+-[^/]+/', # Number-based articles (5-ways-to, 10-tips, etc.)
            r'/guide-[^/]+/',         # Guide articles
            r'/tips-[^/]+/',          # Tips articles
            r'/benefits-of-[^/]+/',   # Benefits articles
            r'/ultimate-guide-[^/]+/', # Ultimate guide articles
        ]
        
        # Patterns that usually indicate less important pages (medium penalty)
        self.low_priority_patterns = [
            r'/\d{4}/\d{2}/',         # Date-based URLs
            r'/page/\d+',             # Pagination
            r'/tag/',                 # Tag pages
            r'/category/',            # Category pages
            r'/author/',              # Author pages
            r'/search',               # Search pages
            r'/feed',                 # RSS feeds
            r'\.xml$',               # XML files
            r'\.pdf$',               # PDF files
            r'/archive',              # Archive pages
            r'/sitemap',              # Sitemap pages
        ]
        
        # Legal/Policy pages (heavy penalty - these are never valuable for business understanding)
        self.legal_policy_patterns = [
            r'/privacy',              # Privacy policy
            r'/privacy-policy',       # Privacy policy variants
            r'/terms',                # Terms pages
            r'/terms-of-service',     # Terms of service
            r'/terms-and-conditions', # Terms and conditions
            r'/legal',                # Legal pages
            r'/disclaimer',           # Disclaimers
            r'/cookies',              # Cookie policy
            r'/cookie-policy',        # Cookie policy
            r'/data-protection',      # Data protection
            r'/gdpr',                 # GDPR compliance
            r'/accessibility',        # Accessibility statements
            r'/compliance',           # Compliance pages
            r'/user-agreement',       # User agreements
            r'/license',              # License agreements
            r'/eula',                 # End user license
        ]
        
        # Patterns that indicate test/development/junk pages (heavy penalty)
        self.junk_patterns = [
            r'\?.*=.*',               # Query strings (e.g., ?utm_source=)
            r'/test[\-_]',            # Test pages
            r'/dev[\-_]',             # Development pages
            r'/staging',              # Staging pages
            r'/demo[\-_]',            # Demo pages
            r'/temp[\-_]',            # Temporary pages
            r'/draft',                # Draft pages
            r'/sample',               # Sample pages
            r'[\-_]test[\-_]',        # Test in middle of URL
            r'[\-_]dev[\-_]',         # Dev in middle of URL
            r'/placeholder',          # Placeholder pages
            r'/coming[\-_]soon',      # Coming soon pages
            r'/under[\-_]construction', # Under construction
            r'\.backup\.',            # Backup files
            r'\.old\.',               # Old files
            r'/backup/',              # Backup directories
            r'/old/',                 # Old directories
        ]
        
        # Shopify-specific patterns
        self.shopify_product_patterns = [
            r'/products/[^/]+$',      # Individual product pages
            r'/products/[^/]+\?',     # Product pages with query params
            r'/products/.+/[^/]+$',   # Nested product URLs
        ]
        
        self.shopify_collection_patterns = [
            r'/collections/[^/]+$',   # Collection pages (categories)
            r'/collections/all',       # All products collection
            r'/collections/[^/]+\?',  # Collections with query params
        ]
        
        # Shopify system pages to skip
        self.shopify_system_patterns = [
            r'/cart',                  # Shopping cart
            r'/checkout',              # Checkout pages
            r'/account',               # Account pages
            r'/password',              # Password pages
            r'/challenge',             # Challenge pages
            r'/tools/',                # Shopify tools
            r'/admin',                 # Admin pages
            r'/apps/',                 # App pages
            r'/cdn/',                  # CDN resources
            r'/services/',             # Service endpoints
            r'/payments/',             # Payment pages
            r'/wallets/',              # Digital wallet pages
            r'/orders/',               # Order pages
            r'/discount/',             # Discount pages
            r'/gift[\-_]cards?/',     # Gift card pages
        ]
        
        # Shopify-specific keywords (for better scoring)
        self.shopify_keywords = {
            'collections': 60,         # Collection pages are important
            'collection': 60,
            'catalog': 55,
            'categories': 55,
            'category': 55,
            'shop': 50,
            'store': 50,
            'products': 30,           # Products landing page (not individual products)
            'new-arrivals': 45,
            'best-sellers': 45,
            'sale': 40,
            'clearance': 35,
            'featured': 40,
        }
        
        # Frequency scoring weights
        self.freq_weights = {
            'always': 1.0,
            'hourly': 0.9,
            'daily': 0.8,
            'weekly': 0.7,
            'monthly': 0.6,
            'yearly': 0.4,
            'never': 0.1
        }
    
    
    def estimate_claude_cost(self, page_count: int) -> Dict[str, float]:
        """Estimate the cost of using Claude API for page analysis"""
        if not self.use_claude_api or not self.claude_client:
            return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0, "input_tokens": 0, "output_tokens": 0}
        
        # Estimate tokens per API call based on the current prompt
        # The prompt includes URL, domain, path (~50-150 characters)
        # Plus the instruction text (~500 characters)
        estimated_input_tokens_per_call = 200  # Conservative estimate
        estimated_output_tokens_per_call = 3   # Just a number response
        
        total_input_tokens = estimated_input_tokens_per_call * page_count
        total_output_tokens = estimated_output_tokens_per_call * page_count
        
        input_cost = (total_input_tokens / 1_000_000) * self.claude_input_cost_per_million
        output_cost = (total_output_tokens / 1_000_000) * self.claude_output_cost_per_million
        total_cost = input_cost + output_cost
        
        return {
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens, 
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": total_cost
        }
    
    def get_claude_cost_confirmation(self, page_count: int) -> bool:
        """Show Claude API cost estimate and get user confirmation"""
        if not self.use_claude_api or not self.claude_client:
            return True
            
        cost_estimate = self.estimate_claude_cost(page_count)
        
        print(f"\n{'='*80}")
        print("CLAUDE API COST ESTIMATE")
        print(f"{'='*80}")
        print(f"Pages to analyze with Claude AI: {page_count}")
        print(f"Estimated input tokens: {cost_estimate['input_tokens']:,}")
        print(f"Estimated output tokens: {cost_estimate['output_tokens']:,}")
        print(f"")
        print(f"Estimated costs (Claude 3.5 Haiku):")
        print(f"  Input tokens:  ${cost_estimate['input_cost']:.4f}")
        print(f"  Output tokens: ${cost_estimate['output_cost']:.4f}")
        print(f"  TOTAL COST:    ${cost_estimate['total_cost']:.4f}")
        
        if cost_estimate['total_cost'] < 0.01:
            print(f"\nCost is under $0.01 - very affordable!")
        elif cost_estimate['total_cost'] < 0.10:
            print(f"\nCost is under $0.10 - quite affordable.")
        elif cost_estimate['total_cost'] < 1.00:
            print(f"\nCost is under $1.00 - moderate cost for enhanced analysis.")
        else:
            print(f"\nCost is over $1.00 - consider if enhanced analysis is worth it.")
            
        print(f"\nNote: This is an estimate. Actual costs may vary slightly.")
        print(f"You can disable Claude API at any time by setting use_claude_api=False")
        
        while True:
            response = input(f"\nProceed with Claude API analysis? (y/n): ").strip().lower()
            if response in ['y', 'yes']:
                return True
            elif response in ['n', 'no']:
                print("Disabling Claude API for this session. Using standard scoring only.")
                self.use_claude_api = False
                self.claude_client = None
                return True
            else:
                print("Please enter 'y' or 'n'")
    
    def calculate_actual_claude_cost(self) -> Dict[str, float]:
        """Calculate actual Claude API costs based on tracked usage"""
        if self.claude_input_tokens_used == 0 and self.claude_output_tokens_used == 0:
            return {"input_cost": 0.0, "output_cost": 0.0, "total_cost": 0.0}
            
        input_cost = (self.claude_input_tokens_used / 1_000_000) * self.claude_input_cost_per_million
        output_cost = (self.claude_output_tokens_used / 1_000_000) * self.claude_output_cost_per_million
        total_cost = input_cost + output_cost
        
        return {
            "input_cost": input_cost,
            "output_cost": output_cost,
            "total_cost": total_cost
        }
    
    def _is_shopify_product_page(self, url: str) -> bool:
        """Check if URL is a Shopify product page"""
        if not self.shopify_mode:
            return False
            
        return any(re.search(pattern, url, re.IGNORECASE) 
                  for pattern in self.shopify_product_patterns)
    
    def _is_shopify_collection_page(self, url: str) -> bool:
        """Check if URL is a Shopify collection page"""
        return any(re.search(pattern, url, re.IGNORECASE) 
                  for pattern in self.shopify_collection_patterns)
    
    def _is_shopify_system_page(self, url: str) -> bool:
        """Check if URL is a Shopify system page that should be skipped"""
        return any(re.search(pattern, url, re.IGNORECASE) 
                  for pattern in self.shopify_system_patterns)
    
    def parse_sitemap(self, sitemap_url: str, is_post_sitemap: bool = False) -> List[PageInfo]:
        """Parse sitemap.xml and extract page information"""
        self.logger.info(f"Fetching sitemap from: {sitemap_url}")
        
        print(f"Fetching sitemap: {sitemap_url}")
        
        try:
            # First attempt with normal request
            response = self.session.get(sitemap_url, timeout=30)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                self.logger.error(f"403 Forbidden error - Website blocking access to: {sitemap_url}")
                print(f"\n{'='*80}")
                print("❌ ACCESS DENIED - 403 FORBIDDEN ERROR")
                print(f"{'='*80}")
                print(f"The website is blocking automated access to: {sitemap_url}")
                print(f"")
                print(f"This indicates the website has active bot protection measures.")
                print(f"Common reasons for 403 errors:")
                print(f"• Cloudflare or similar bot protection")
                print(f"• IP-based rate limiting or blocking")
                print(f"• Geographic restrictions")
                print(f"• Website requires authentication")
                print(f"• Server configured to block automated requests")
                print(f"")
                print(f"Solutions to try:")
                print(f"1. Use a VPN to change your IP address")
                print(f"2. Wait some time before retrying")
                print(f"3. Check the website's robots.txt for crawler policies")
                print(f"4. Try accessing the sitemap manually in a browser first")
                print(f"5. Contact the website owner for API access")
                print(f"")
                raise SystemExit("Script stopped due to 403 Forbidden error. Website is blocking automated access.")
            else:
                self.logger.error(f"Failed to fetch sitemap: {e}")
                raise
        except requests.RequestException as e:
            self.logger.error(f"Failed to fetch sitemap: {e}")
            raise
        
        pages = []
        
        try:
            # Handle compressed content - check multiple ways
            import gzip
            try:
                import brotli
                has_brotli = True
            except ImportError:
                has_brotli = False
                self.logger.warning("Brotli not available, install with: pip install brotli")
            
            # Debug headers (only log to file, not console)
            content_encoding = response.headers.get('content-encoding', '').lower()
            content_type = response.headers.get('content-type', '').lower()
            self.logger.debug(f"Content-Encoding: '{content_encoding}', Content-Type: '{content_type}'")
            self.logger.debug(f"First few bytes: {response.content[:10]}")
            
            # Detect compression type
            is_gzip = (content_encoding == 'gzip' or response.content[:2] == b'\x1f\x8b')
            is_brotli = (content_encoding == 'br')
            looks_binary = len([b for b in response.content[:50] if b < 32 or b > 126]) > 10
            
            # Try decompression
            if is_brotli and has_brotli:
                try:
                    content = brotli.decompress(response.content)
                    response_text = content.decode('utf-8')
                    self.logger.debug("Successfully decompressed Brotli content")
                except Exception as e:
                    self.logger.debug(f"Failed to decompress Brotli: {e}, trying raw content")
                    response_text = response.text
                    content = response.content
            elif is_gzip or (looks_binary and not is_brotli):
                try:
                    content = gzip.decompress(response.content)
                    response_text = content.decode('utf-8')
                    self.logger.debug("Successfully decompressed gzip content")
                except Exception as e:
                    self.logger.debug(f"Failed to decompress gzip: {e}, trying raw content")
                    response_text = response.text
                    content = response.content
            else:
                response_text = response.text
                content = response.content
            
            # Debug: Check what we actually received (safely) - only log to file
            content_preview = response_text[:200].encode('ascii', errors='replace').decode('ascii')
            self.logger.debug(f"Response content preview: {content_preview}")
            
            if not response_text.strip().startswith('<?xml'):
                self.logger.warning("Response doesn't appear to be XML. Checking if it's HTML...")
                if '<html' in response_text.lower():
                    self.logger.error("Received HTML page instead of XML sitemap. This might be:")
                    self.logger.error("- A 404 page")
                    self.logger.error("- A redirect page") 
                    self.logger.error("- An access denied page")
                    self.logger.error("Try checking the URL in your browser first")
                raise ValueError("Received HTML instead of XML sitemap")
            
            root = ET.fromstring(content)
            
            # Handle sitemap index files
            if 'sitemapindex' in root.tag:
                self.logger.info("Found sitemap index, parsing individual sitemaps...")
                
                # Collect all sub-sitemap URLs
                sub_sitemaps = []
                for sitemap in root:
                    loc_elem = sitemap.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                    if loc_elem is not None:
                        sub_sitemaps.append(loc_elem.text)
                
                print(f"Found sitemap index with {len(sub_sitemaps)} sub-sitemaps. Processing...")
                
                # Sort sitemaps to prioritize pages over posts
                def sitemap_priority(url):
                    url_lower = url.lower()
                    if 'page' in url_lower:
                        return 1  # High priority for pages
                    elif 'post' in url_lower:
                        return 3  # Lower priority for posts
                    else:
                        return 2  # Medium priority for others
                
                sub_sitemaps.sort(key=sitemap_priority)
                
                # In Shopify mode, show which sitemaps we're processing
                if self.shopify_mode:
                    print("\nShopify sitemap structure detected:")
                    products_count = sum(1 for url in sub_sitemaps if 'product' in url.lower())
                    collections_count = sum(1 for url in sub_sitemaps if 'collection' in url.lower())
                    pages_count = sum(1 for url in sub_sitemaps if 'page' in url.lower())
                    
                    print(f"  - Product sitemaps: {products_count} {'(WILL BE SKIPPED)' if self.skip_products else ''}")
                    print(f"  - Collection sitemaps: {collections_count}")
                    print(f"  - Page sitemaps: {pages_count}")
                    print()
                
                # Parse sitemaps in priority order
                for i, sub_sitemap_url in enumerate(sub_sitemaps, 1):
                    # Skip product sitemaps entirely if in Shopify mode with skip_products
                    if self.shopify_mode and self.skip_products:
                        if 'sitemap_products_' in sub_sitemap_url.lower() or '/products.' in sub_sitemap_url.lower():
                            print(f"  Skipping product sitemap {i}/{len(sub_sitemaps)}: {sub_sitemap_url}")
                            continue
                    is_post = 'post' in sub_sitemap_url.lower()
                    sitemap_type = 'post' if is_post else 'page'
                    print(f"  Processing {sitemap_type} sitemap {i}/{len(sub_sitemaps)}...")
                    self.logger.info(f"Parsing {'post' if is_post else 'page'} sitemap: {sub_sitemap_url}")
                    pages.extend(self.parse_sitemap(sub_sitemap_url, is_post))
                
                return pages
            
            # Parse regular sitemap
            for url in root:
                loc_elem = url.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                if loc_elem is not None:
                    url = loc_elem.text
                    
                    # Filter out Shopify product pages if skip_products is enabled
                    if self.shopify_mode and self.skip_products:
                        if self._is_shopify_product_page(url):
                            continue  # Skip individual product pages
                    
                    # Always filter out Shopify system pages
                    if self.shopify_mode and self._is_shopify_system_page(url):
                        continue
                    
                    page_info = PageInfo(url=url, is_post=is_post_sitemap)
                    
                    # Extract additional metadata
                    lastmod_elem = url.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}lastmod')
                    if lastmod_elem is not None:
                        page_info.lastmod = lastmod_elem.text
                    
                    changefreq_elem = url.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}changefreq')
                    if changefreq_elem is not None:
                        page_info.changefreq = changefreq_elem.text
                    
                    priority_elem = url.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}priority')
                    if priority_elem is not None:
                        try:
                            page_info.priority = float(priority_elem.text)
                        except ValueError:
                            pass
                    
                    pages.append(page_info)
            
            self.logger.info(f"Found {len(pages)} pages in sitemap")
            if len(pages) > 0:
                if self.shopify_mode and self.skip_products:
                    collections_count = sum(1 for p in pages if self._is_shopify_collection_page(p.url))
                    print(f"Extracted {len(pages)} URLs from sitemap (including {collections_count} collections)")
                else:
                    print(f"Extracted {len(pages)} URLs from sitemap")
            return pages
            
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse sitemap XML: {e}")
            raise
    
    def calculate_page_importance(self, pages: List[PageInfo]) -> List[PageInfo]:
        """Calculate importance scores - prioritize PAGES over blog posts"""
        self.logger.info("Calculating page importance scores...")
        
        print(f"\nAnalyzing {len(pages)} pages for business importance...")
        
        # Extract navigation URLs from homepage for priority scoring
        print("Step 1: Analyzing homepage navigation menu...")
        self.navigation_urls = self.extract_navigation_urls(self.sitemap_url)
        
        # Remove duplicates first (same URL from multiple sitemaps)
        unique_pages = {}
        for page in pages:
            if page.url not in unique_pages:
                unique_pages[page.url] = page
            else:
                # Keep the page with the higher priority (not a post if we have both)
                existing = unique_pages[page.url]
                if not existing.is_post and page.is_post:
                    # Keep existing (page over post)
                    continue
                elif existing.is_post and not page.is_post:
                    # Replace with new (page over post)
                    unique_pages[page.url] = page
                # If both same type, keep the first one
        
        deduplicated_pages = list(unique_pages.values())
        if len(pages) != len(deduplicated_pages):
            print(f"Removed {len(pages) - len(deduplicated_pages)} duplicate URLs")
        
        print("Step 2: Categorizing pages (business vs blog content)...")
        
        # Separate pages from blog posts using both sitemap source and URL patterns
        regular_pages = []
        blog_posts = []
        
        for page in deduplicated_pages:
            # Navigation pages are NEVER blog posts, regardless of URL patterns
            if page.in_navigation:
                regular_pages.append(page)
            elif self._is_blog_post(page):
                blog_posts.append(page)
            else:
                regular_pages.append(page)
        
        print(f"Found {len(regular_pages)} regular pages and {len(blog_posts)} blog posts")
        
        print("Step 3: Scoring page importance (business-first algorithm)...")
        
        # Score regular pages first (these are most important)
        if regular_pages:
            print(f"  Scoring {len(regular_pages)} regular pages...")
            for i, page in enumerate(regular_pages):
                if len(regular_pages) > 20 and i % 10 == 0 and i > 0:  # Progress indicator for large sets
                    print(f"    Progress: {i}/{len(regular_pages)} pages scored...")
                page.score = self._calculate_page_score(page)
        
        # Only score blog posts if we might need them (if user wants more pages than we have regular ones)
        # This saves a lot of time when there are many blog posts
        max_needed_blogs = max(0, 50 - len(regular_pages))  # Reasonable limit
        blogs_to_score = blog_posts[:max_needed_blogs]
        
        if blogs_to_score:
            print(f"  Scoring {len(blogs_to_score)} most recent blog posts (skipping {len(blog_posts) - len(blogs_to_score)} older posts)...")
            for i, page in enumerate(blogs_to_score):
                if len(blogs_to_score) > 20 and i % 15 == 0 and i > 0:  # Progress indicator for large sets
                    print(f"    Progress: {i}/{len(blogs_to_score)} blog posts scored...")
                page.score = self._calculate_page_score(page) - 100  # Still penalty, but not extreme
        
        # Set remaining blog posts to very low scores without detailed calculation
        for page in blog_posts[max_needed_blogs:]:
            page.score = -200  # Very low score, won't be selected
        
        print("Step 4: Ranking all pages by business importance...")
        
        # Sort regular pages by score
        regular_pages.sort(key=lambda p: p.score, reverse=True)
        blog_posts.sort(key=lambda p: p.score, reverse=True)
        
        # Combine ALL pages and sort by score (this ensures proper ranking regardless of category)
        all_scored = regular_pages + blog_posts
        all_scored.sort(key=lambda p: p.score, reverse=True)  # Final sort by score
        
        # FORCE HOMEPAGE TO #1 POSITION (regardless of scoring)
        homepage_page = None
        homepage_index = None
        
        # Find the homepage in the list
        for i, page in enumerate(all_scored):
            parsed_url = urlparse(page.url)
            if parsed_url.path in ['/', '/index.html', '/index.php', '/home', '/home.html', ''] or \
               parsed_url.path.lower() in ['/home', '/index', '/main']:
                homepage_page = page
                homepage_index = i
                break
        
        # Move homepage to position #1
        if homepage_page and homepage_index is not None:
            all_scored.pop(homepage_index)  # Remove from current position
            all_scored.insert(0, homepage_page)  # Insert at position 0 (first)
            print(f"Ensured homepage is ranked #1 (was position #{homepage_index + 1})")
        else:
            print("Warning: Could not identify homepage in page list")
        
        print(f"Completed analysis! Ranked {len(all_scored)} pages by business importance.")
        
        return all_scored
    
    def extract_navigation_urls(self, base_url: str) -> List[str]:
        """Extract navigation menu URLs from the homepage for priority scoring"""
        try:
            # Get the base URL without path for homepage
            from urllib.parse import urlparse, urljoin
            parsed_base = urlparse(base_url)
            homepage_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
            
            print(f"Analyzing homepage navigation: {homepage_url}")
            
            # Fetch homepage
            response = self.session.get(homepage_url, timeout=30)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            nav_urls = set()
            
            # Look for navigation in common locations - COMPREHENSIVE list including mobile/dropdown
            nav_selectors = [
                # Standard navigation patterns
                'header nav a', 'nav a', '.navigation a', '.nav a', '.menu a',
                '.main-menu a', '.primary-menu a', '.navbar a', 'header .menu a',
                'header ul a', '[role="navigation"] a',
                
                # Modern framework patterns
                '.nav-menu a', '.navigation-menu a', '.site-navigation a', 
                '.main-navigation a', '.primary-navigation a', '.top-menu a',
                '.header-menu a', '.site-menu a', '.global-nav a',
                
                # Mobile/hamburger menu patterns
                '.mobile-menu a', '.hamburger-menu a', '.burger-menu a',
                '.menu-toggle a', '.mobile-nav a', '.off-canvas a',
                '.drawer-menu a', '.slide-menu a', '.overlay-menu a',
                
                # Dropdown and mega menu patterns
                '.dropdown-menu a', '.mega-menu a', '.submenu a', '.sub-menu a',
                '.menu-item a', '.menu-link', '.nav-item a', '.nav-dropdown a',
                
                # Modern web app patterns
                '.sidebar-menu a', '.vertical-menu a', '.menu-sidebar a',
                '.app-menu a', '.dashboard-menu a', '.side-nav a',
                
                # Common CMS patterns (WordPress, Drupal, etc.)
                '.wp-menu a', '.menu-main a', '.menu-primary a', '.menu-header a',
                '.region-navigation a', '.block-menu a',
                
                # Bootstrap and framework patterns  
                '.navbar-nav a', '.nav-pills a', '.nav-tabs a', '.nav-link',
                '.navigation-list a', '.menu-list a', '.list-nav a',
                
                # Generic patterns for any framework
                'header a', '.header a', '#header a',  # All header links
                '.top-bar a', '.topbar a', '#topbar a',  # Top bars
                '.site-header a', '#site-header a',     # Site headers
                
                # Container-based patterns
                '.container nav a', '.wrapper nav a', '.content nav a',
                '.container header a', '.wrapper header a',
                
                # ID-based selectors
                '#navigation a', '#nav a', '#menu a', '#main-menu a',
                '#primary-menu a', '#header-menu a', '#site-navigation a',
                '#mobile-menu a', '#hamburger-menu a',
                
                # Footer navigation (sometimes contains main nav too)
                'footer nav a', '.footer-nav a', '.footer-menu a',
                
                # Fallback: any prominent link in likely navigation areas
                'header li a', '.header li a', '#header li a',
                '.menu li a', '.nav li a', '.navigation li a'
            ]
            
            # Extract URLs from each selector with debug output
            selector_results = {}
            debug_details = []
            
            for selector in nav_selectors:
                links = soup.select(selector)
                if links:
                    selector_results[selector] = len(links)
                    for link in links:
                        href = link.get('href')
                        link_text = link.get_text(strip=True)
                        
                        if href:
                            # Handle encoding issues safely
                            safe_link_text = link_text.encode('ascii', errors='ignore').decode('ascii')
                            debug_details.append(f"Found link: '{href}' (text: '{safe_link_text}') via {selector}")
                            # Convert relative URLs to absolute
                            full_url = urljoin(homepage_url, href)
                            
                            # Only include URLs from the same domain
                            parsed_full = urlparse(full_url)
                            if parsed_full.netloc == parsed_base.netloc:
                                # Clean up URL (remove fragments, query params for matching)
                                clean_url = full_url.split('#')[0].split('?')[0].rstrip('/')
                                debug_details.append(f"  -> Adding to nav_urls: {clean_url}")
                                nav_urls.add(clean_url)
                            else:
                                debug_details.append(f"  -> Skipping external link: {full_url}")
                        else:
                            safe_link_text = link_text.encode('ascii', errors='ignore').decode('ascii')
                            debug_details.append(f"Found link without href: (text: '{safe_link_text}') via {selector}")
            
            # ENHANCED: Also search for navigation terms throughout the page
            expected_nav_terms = ['Platform', 'Customers', 'Partnerships', 'About Us', 'About', 'Services', 'Solutions', 'Products', 'Company']
            
            term_nav_urls = set()
            for term in expected_nav_terms:
                # Find all links that contain this navigation term in their text
                for link in soup.find_all('a', href=True):
                    link_text = link.get_text(strip=True)
                    if term.lower() in link_text.lower() and len(link_text) < 100:  # Avoid very long text
                        href = link.get('href')
                        full_url = urljoin(homepage_url, href)
                        parsed_full = urlparse(full_url)
                        
                        if parsed_full.netloc == parsed_base.netloc:
                            clean_url = full_url.split('#')[0].split('?')[0].rstrip('/')
                            term_nav_urls.add(clean_url)
            
            # Combine navigation URLs from selectors and terms
            nav_urls.update(term_nav_urls)
            
            if term_nav_urls:
                print(f"Enhanced navigation search found {len(term_nav_urls)} additional pages")
            
            # Filter out common non-content URLs (less restrictive now)
            filtered_nav_urls = []
            exclude_patterns = [
                r'^mailto:', r'^tel:', r'^javascript:', r'^#$', r'^\?',  # Protocol/fragment exclusions
                r'\.pdf$', r'\.doc$', r'\.zip$', r'\.xls$', r'\.ppt$',  # File downloads
                r'/wp-admin', r'/admin', r'/login$', r'/signin$',        # Admin areas only
                r'\.js$', r'\.css$', r'\.xml$', r'\.json$'             # Asset files
            ]
            
            for url in nav_urls:
                if not any(re.search(pattern, url, re.IGNORECASE) for pattern in exclude_patterns):
                    filtered_nav_urls.append(url)
            
            # Remove homepage URL itself from navigation list
            homepage_variations = [homepage_url, homepage_url + '/', homepage_url + '/index', 
                                 homepage_url + '/index.html', homepage_url + '/home']
            filtered_nav_urls = [url for url in filtered_nav_urls if url not in homepage_variations]
            
            print(f"Found {len(filtered_nav_urls)} navigation menu pages")
            if filtered_nav_urls:
                print("Navigation pages:", ', '.join([url.replace(homepage_url, '') or '/' for url in filtered_nav_urls[:10]]))
            
            # ANALYZE HOMEPAGE CONTENT for core business terms
            print("Analyzing homepage content for core business terms...")
            self.homepage_core_terms = self._extract_homepage_core_terms(soup, homepage_url)
            
            return filtered_nav_urls
            
        except Exception as e:
            print(f"Could not extract navigation: {e}")
            return []
    
    def _extract_homepage_core_terms(self, soup, homepage_url: str) -> List[str]:
        """Extract core business terms using semantic NLP analysis with HTML hierarchy weighting"""
        try:
            # Use proper NLP analysis weighted by HTML semantic importance
            import re
            from collections import Counter
            
            # Content extraction with semantic weighting
            # Title = highest weight (5x), H1 = 4x, H2 = 3x, H3 = 2x, content = 1x
            weighted_content = []
            
            # 1. TITLE (Highest semantic importance - 5x weight)
            title_element = soup.find('title')
            if title_element:
                title_text = title_element.get_text().strip()
                print(f"  Title: {title_text}")
                # Extract key phrases and entities from title
                title_phrases = self._extract_key_phrases_and_entities(title_text)
                # Add with 5x weight
                for phrase in title_phrases:
                    weighted_content.extend([phrase] * 5)
            
            # 2. H1 HEADINGS (Very high importance - 4x weight)
            h1_elements = soup.find_all('h1')
            h1_texts = []
            for h1 in h1_elements:
                h1_text = h1.get_text().strip()
                if h1_text:
                    h1_texts.append(h1_text)
                    h1_phrases = self._extract_key_phrases_and_entities(h1_text)
                    # Add with 4x weight
                    for phrase in h1_phrases:
                        weighted_content.extend([phrase] * 4)
            
            if h1_texts:
                print(f"  H1 headings: {' | '.join(h1_texts)}")
            
            # 3. H2 HEADINGS (High importance - 3x weight)
            h2_elements = soup.find_all('h2')
            h2_count = 0
            for h2 in h2_elements[:10]:  # Limit to first 10 H2s
                h2_text = h2.get_text().strip()
                if h2_text and len(h2_text) < 200:  # Avoid very long headings
                    h2_count += 1
                    h2_phrases = self._extract_key_phrases_and_entities(h2_text)
                    # Add with 3x weight
                    for phrase in h2_phrases:
                        weighted_content.extend([phrase] * 3)
            
            if h2_count:
                print(f"  Analyzed {h2_count} H2 headings")
            
            # 4. H3 HEADINGS (Medium importance - 2x weight)
            h3_elements = soup.find_all('h3')
            h3_count = 0
            for h3 in h3_elements[:15]:  # Limit to first 15 H3s
                h3_text = h3.get_text().strip()
                if h3_text and len(h3_text) < 150:  # Avoid very long headings
                    h3_count += 1
                    h3_phrases = self._extract_key_phrases_and_entities(h3_text)
                    # Add with 2x weight
                    for phrase in h3_phrases:
                        weighted_content.extend([phrase] * 2)
            
            if h3_count:
                print(f"  Analyzed {h3_count} H3 headings")
            
            # 5. PROMINENT CONTENT SECTIONS (Medium importance - 2x weight)
            prominent_selectors = [
                '.hero', '.banner', '.intro', '.tagline', '.headline', '.lead',
                '.services-summary', '.about-summary', '.value-prop', '.elevator-pitch',
                '[class*="hero"]', '[class*="banner"]', '[class*="intro"]'
            ]
            
            prominent_content_found = 0
            for selector in prominent_selectors:
                elements = soup.select(selector)
                for element in elements:
                    text = element.get_text(strip=True)
                    if text and 50 < len(text) < 800:  # Meaningful content size
                        prominent_content_found += 1
                        prominent_phrases = self._extract_key_phrases_and_entities(text)
                        # Add with 2x weight
                        for phrase in prominent_phrases:
                            weighted_content.extend([phrase] * 2)
            
            if prominent_content_found:
                print(f"  Analyzed {prominent_content_found} prominent content sections")
            
            # 6. MAIN CONTENT AREAS (Base importance - 1x weight)
            main_content_selectors = [
                'main', '[role="main"]', '.main-content', '#main-content',
                '.content', '#content', '.page-content', '.entry-content'
            ]
            
            main_content_found = False
            for selector in main_content_selectors:
                main_element = soup.select_one(selector)
                if main_element:
                    # Extract first few paragraphs from main content
                    paragraphs = main_element.find_all('p')[:5]  # First 5 paragraphs
                    for p in paragraphs:
                        p_text = p.get_text(strip=True)
                        if 30 < len(p_text) < 500:  # Meaningful paragraph size
                            main_phrases = self._extract_key_phrases_and_entities(p_text)
                            # Add with 1x weight (base weight)
                            weighted_content.extend(main_phrases)
                    main_content_found = True
                    break
            
            if main_content_found:
                print(f"  Analyzed main content paragraphs")
            
            # ANALYZE WEIGHTED CONTENT using NLP frequency analysis
            print(f"  Processing {len(weighted_content)} weighted content elements...")
            
            # Count frequency of terms (weighted by semantic importance)
            term_frequency = Counter(weighted_content)
            
            # Filter and rank terms by business relevance and frequency
            business_terms = []
            
            # Get most frequent terms (these got the most weight from important HTML elements)
            most_frequent = term_frequency.most_common(50)
            
            for term, frequency in most_frequent:
                # Filter for business-relevant terms
                if self._is_business_relevant_term(term) and frequency >= 2:  # Must appear at least twice
                    business_terms.append((term, frequency))
            
            # Sort by frequency (higher = more semantically important based on HTML hierarchy)
            business_terms.sort(key=lambda x: x[1], reverse=True)
            
            # Extract just the terms (remove frequency counts)
            core_terms = [term for term, freq in business_terms[:20]]  # Top 20 most important
            
            if core_terms:
                print(f"  Core business terms identified: {', '.join(core_terms[:10])}")
                # Show weighted importance
                top_weighted = [(term, freq) for term, freq in business_terms[:8]]
                print(f"  Highest weighted terms: {', '.join([f'{term}({freq})' for term, freq in top_weighted])}")
            else:
                print(f"  No core business terms identified through NLP analysis")
            
            return core_terms
            
        except Exception as e:
            print(f"  Error in NLP homepage analysis: {e}")
            # Fallback to simple extraction if NLP fails
            return self._simple_fallback_extraction(soup)
    
    def _extract_key_phrases_and_entities(self, text: str) -> List[str]:
        """Extract key phrases and business entities using NLP techniques"""
        import re
        
        if not text or len(text.strip()) < 3:
            return []
            
        # Clean and normalize text
        text = text.strip()
        
        # Extract phrases and entities
        phrases = []
        
        # 1. Extract meaningful single words (filtered)
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        for word in words:
            if self._is_business_relevant_term(word):
                phrases.append(word)
        
        # 2. Extract two-word phrases (bigrams)
        words_list = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        for i in range(len(words_list) - 1):
            bigram = f"{words_list[i]}-{words_list[i+1]}"
            if len(words_list[i]) >= 3 and len(words_list[i+1]) >= 3:
                if self._is_business_relevant_term(words_list[i]) or self._is_business_relevant_term(words_list[i+1]):
                    phrases.append(bigram)
        
        # 3. Extract three-word phrases (trigrams) - most valuable for business terms
        for i in range(len(words_list) - 2):
            trigram = f"{words_list[i]}-{words_list[i+1]}-{words_list[i+2]}"
            if all(len(word) >= 3 for word in words_list[i:i+3]):
                # Check if any word in trigram is business relevant
                if any(self._is_business_relevant_term(word) for word in words_list[i:i+3]):
                    phrases.append(trigram)
        
        # 4. Extract specific business entity patterns
        business_patterns = [
            r'\b(?:seo|search engine optimization)\b',
            r'\b(?:web|website|web-site)\s+(?:design|development|dev)\b',
            r'\b(?:digital|online|internet)\s+(?:marketing|advertising|ads)\b',
            r'\b(?:google|facebook|social media|social)\s+(?:ads|advertising|marketing)\b',
            r'\b(?:ppc|pay per click|paid advertising)\b',
            r'\b(?:e-commerce|ecommerce|online store)\b',
            r'\b(?:content|copywriting|copy writing)\b',
            r'\b(?:branding|brand|identity)\b',
            r'\b(?:consulting|consultation|strategy)\b',
            r'\b(?:services|solutions|offerings)\b'
        ]
        
        for pattern in business_patterns:
            matches = re.findall(pattern, text.lower())
            for match in matches:
                # Normalize the match
                normalized = re.sub(r'\s+', '-', match.strip())
                if normalized:
                    phrases.append(normalized)
        
        # Remove duplicates while preserving order
        unique_phrases = []
        seen = set()
        for phrase in phrases:
            if phrase not in seen and len(phrase) >= 3:
                unique_phrases.append(phrase)
                seen.add(phrase)
        
        return unique_phrases
    
    def _is_business_relevant_term(self, term: str) -> bool:
        """Check if a term is business-relevant (not a stop word or generic term)"""
        if not term or len(term) < 3:
            return False
            
        # Common stop words and generic terms to exclude
        stop_words = {
            'the', 'and', 'for', 'are', 'was', 'you', 'all', 'any', 'can', 'had',
            'her', 'him', 'his', 'how', 'man', 'new', 'now', 'old', 'see', 'two',
            'way', 'who', 'boy', 'did', 'has', 'let', 'put', 'say', 'she', 'too',
            'use', 'our', 'out', 'day', 'get', 'may', 'own', 'try', 'ask', 'end',
            'why', 'also', 'back', 'call', 'came', 'each', 'even', 'find', 'give',
            'good', 'hand', 'here', 'just', 'keep', 'last', 'left', 'life', 'live',
            'look', 'made', 'make', 'many', 'more', 'most', 'move', 'much', 'must',
            'name', 'need', 'next', 'only', 'open', 'over', 'part', 'play', 'right',
            'said', 'same', 'seem', 'show', 'side', 'take', 'tell', 'than', 'that',
            'them', 'they', 'this', 'time', 'turn', 'very', 'want', 'well', 'went',
            'were', 'what', 'when', 'will', 'with', 'work', 'year', 'your', 'from',
            'have', 'been', 'about', 'would', 'there', 'their', 'could', 'other',
            'after', 'first', 'never', 'these', 'think', 'where', 'being', 'every',
            'great', 'might', 'shall', 'still', 'those', 'under', 'while', 'should',
            # Generic business terms that don't indicate specialization
            'business', 'company', 'service', 'services', 'solution', 'solutions',
            'professional', 'team', 'quality', 'best', 'top', 'leading', 'expert',
            'experience', 'years', 'since', 'established', 'founded', 'clients',
            'customers', 'projects', 'portfolio', 'contact', 'about', 'home',
            # Generic web terms
            'website', 'site', 'page', 'pages', 'content', 'information', 'learn',
            'read', 'click', 'visit', 'browse', 'online', 'internet', 'web'
        }
        
        term_lower = term.lower().strip()
        
        if term_lower in stop_words:
            return False
            
        # Business-relevant term patterns
        business_indicators = [
            # Core business service terms
            'seo', 'marketing', 'design', 'development', 'advertising', 'consulting',
            'strategy', 'optimization', 'analytics', 'automation', 'integration',
            'management', 'planning', 'research', 'analysis', 'training', 'support',
            
            # Industry-specific terms
            'digital', 'social', 'mobile', 'responsive', 'ecommerce', 'wordpress',
            'shopify', 'magento', 'drupal', 'joomla', 'custom', 'cms', 'api',
            'database', 'hosting', 'domain', 'ssl', 'security', 'backup',
            
            # Medical/healthcare
            'medical', 'healthcare', 'dental', 'surgery', 'treatment', 'therapy',
            'clinic', 'hospital', 'doctor', 'physician', 'nurse', 'patient',
            'diagnosis', 'procedure', 'rehabilitation', 'wellness', 'health',
            
            # Legal
            'legal', 'law', 'attorney', 'lawyer', 'litigation', 'contract',
            'compliance', 'regulation', 'patent', 'trademark', 'copyright',
            
            # Real estate
            'real', 'estate', 'property', 'residential', 'commercial', 'rental',
            'mortgage', 'investment', 'construction', 'renovation', 'architecture',
            
            # Financial
            'financial', 'accounting', 'bookkeeping', 'tax', 'audit', 'investment',
            'insurance', 'banking', 'loan', 'credit', 'payroll', 'budgeting',
            
            # Technical services
            'repair', 'maintenance', 'installation', 'plumbing', 'electrical',
            'hvac', 'roofing', 'flooring', 'painting', 'cleaning', 'landscaping'
        ]
        
        # Check if term contains any business indicators
        for indicator in business_indicators:
            if indicator in term_lower:
                return True
                
        # Check for multi-word business terms (hyphenated)
        if '-' in term_lower:
            parts = term_lower.split('-')
            if len(parts) >= 2 and any(part in business_indicators for part in parts):
                return True
        
        # If it's a reasonably long term that's not a stop word, include it
        return len(term_lower) >= 4 and term_lower not in stop_words
    
    def _simple_fallback_extraction(self, soup) -> List[str]:
        """Simple fallback extraction if NLP analysis fails"""
        try:
            terms = []
            
            # Extract from title
            title = soup.find('title')
            if title:
                words = re.findall(r'\b[a-zA-Z]{4,}\b', title.get_text().lower())
                terms.extend([w for w in words if self._is_business_relevant_term(w)])
            
            # Extract from first H1
            h1 = soup.find('h1')
            if h1:
                words = re.findall(r'\b[a-zA-Z]{4,}\b', h1.get_text().lower())
                terms.extend([w for w in words if self._is_business_relevant_term(w)])
            
            # Remove duplicates
            return list(dict.fromkeys(terms))
            
        except Exception:
            return []

    def _extract_business_terms_from_text(self, text: str) -> List[str]:
        """Extract business-relevant terms from text"""
        import re
        
        # Common stop words to filter out
        stop_words = {
            'a', 'an', 'and', 'are', 'as', 'at', 'be', 'by', 'for', 'from', 'has', 'he', 'in', 'is', 'it',
            'its', 'of', 'on', 'that', 'the', 'to', 'was', 'were', 'will', 'with', 'we', 'our', 'your',
            'you', 'they', 'them', 'their', 'this', 'these', 'those', 'can', 'have', 'had', 'get', 'all',
            'any', 'may', 'new', 'now', 'old', 'see', 'two', 'way', 'who', 'boy', 'did', 'has', 'let',
            'put', 'say', 'she', 'too', 'use', 'her', 'him', 'his', 'how', 'man', 'out', 'get', 'when',
            'where', 'why', 'what', 'which', 'than', 'more', 'most', 'best', 'top', 'great', 'good',
            'company', 'business', 'service', 'services'  # Too generic
        }
        
        # Extract potential business terms
        # Split on common separators and clean
        terms = re.split(r'[^\w\s-]', text.lower())
        
        business_terms = []
        for term in terms:
            # Clean whitespace
            term = term.strip()
            
            # Skip stop words, short terms, and very long terms
            if len(term) < 3 or len(term) > 25 or term in stop_words:
                continue
                
            # Look for multi-word business terms (hyphenated or space-separated)
            words = re.split(r'[\s-]+', term)
            
            # Add individual meaningful words
            for word in words:
                word = word.strip()
                if len(word) >= 3 and word not in stop_words:
                    business_terms.append(word)
            
            # Add the full term if it contains multiple words
            if len(words) > 1:
                clean_term = re.sub(r'\s+', '-', term.strip())
                if clean_term:
                    business_terms.append(clean_term)
        
        return business_terms
    
    def _calculate_page_score(self, page: PageInfo) -> float:
        """Calculate score for a single page"""
        score = 0.0
        parsed_url = urlparse(page.url)
        url_lower = page.url.lower()
        path_lower = parsed_url.path.lower()
        
        # Shopify-specific scoring
        if self.shopify_mode:
            # Collections get a significant bonus
            if self._is_shopify_collection_page(page.url):
                score += 80  # High priority for collection pages
                page.has_keywords = True
                
                # Featured collections get extra bonus
                featured_collections = ['all', 'best-sellers', 'new-arrivals', 'featured', 'sale']
                for featured in featured_collections:
                    if featured in path_lower:
                        score += 20
                        break
            
            # Individual products get penalty if not skipped already
            elif self._is_shopify_product_page(page.url):
                score -= 50  # Lower priority for individual products
            
            # Shopify-specific keywords
            for keyword, points in self.shopify_keywords.items():
                if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower:
                    score += points
                    page.has_keywords = True
        
        # 1. Check for junk/test pages (heavy penalty)
        is_junk = any(re.search(pattern, page.url, re.IGNORECASE) 
                     for pattern in self.junk_patterns)
        if is_junk:
            score -= 1000  # Heavy penalty for junk pages (essentially eliminates them)
            
        # 1b. Check for blog posts/news articles (significant penalty)
        if self._is_blog_post(page):
            score -= 150  # Significant penalty for blog content (marketing, not core business)
            
        # 1c. Check for legal/policy pages (very heavy penalty)
        is_legal_policy = any(re.search(pattern, page.url, re.IGNORECASE) 
                            for pattern in self.legal_policy_patterns)
        if is_legal_policy:
            score -= 500  # Very heavy penalty - legal pages are never valuable for business understanding
            
        # 2. Check for other low-priority patterns (medium penalty)
        is_low_priority = any(re.search(pattern, page.url, re.IGNORECASE) 
                            for pattern in self.low_priority_patterns)
        if is_low_priority:
            score -= 30  # Penalty for low-priority pages
            
        # 3. Navigation menu priority (HIGHEST PRIORITY - these tell the company story)
        page_url_clean = page.url.split('#')[0].split('?')[0].rstrip('/')
        if page_url_clean in self.navigation_urls:
            score += 200  # Massive bonus for navigation pages
            page.in_navigation = True
            page.has_keywords = True
        
        # 4. Homepage detection (ABSOLUTE HIGHEST priority - this is the most important page)
        if parsed_url.path in ['/', '/index.html', '/index.php', '/home', '/home.html', ''] or \
           path_lower in ['/home', '/index', '/main']:
            score += 500  # ABSOLUTE highest priority - homepage is most important
            page.has_keywords = True
            page.in_navigation = True  # Homepage is always part of navigation
        
        # 5. HOMEPAGE-DRIVEN CORE BUSINESS SCORING (Highest Priority)
        homepage_bonus_applied = False
        if self.homepage_core_terms:
            # Filter for truly core business terms (not generic words)
            core_business_terms = [term for term in self.homepage_core_terms 
                                 if term not in ['social', 'media', 'content', 'marketing', 'digital', 
                                               'management', 'strategy', 'strategic', 'guidance']]
            
            for term in core_business_terms:
                # More flexible matching for homepage terms
                term_variants = [term, term.replace('-', ''), term.replace('-', '_')]
                path_variants = [path_lower, path_lower.replace('-', ''), path_lower.replace('_', '')]
                
                # Check various combinations
                match_found = False
                for term_var in term_variants:
                    for path_var in path_variants:
                        if term_var in path_var:
                            match_found = True
                            break
                    if match_found:
                        break
                
                # Also check for semantic matches (e.g., 'seo' matches 'search-engine-optimization')
                if not match_found:
                    if term == 'seo' and ('search' in path_lower or 'engine' in path_lower):
                        match_found = True
                    elif term == 'search-engine-optimization' and 'seo' in path_lower:
                        match_found = True
                    elif term == 'google-ads' and ('ppc' in path_lower or 'ads' in path_lower):
                        match_found = True
                    elif term == 'web-design' and ('design' in path_lower or 'website' in path_lower):
                        match_found = True
                
                if match_found:
                    # MASSIVE bonus for matching homepage-identified core business terms
                    # This must be higher than any possible core business keyword combination
                    score += 600  # Increased to ensure homepage matches always win
                    page.has_keywords = True
                    homepage_bonus_applied = True
                    # Debug: show which term triggered the bonus
                    self.logger.debug(f"Homepage bonus applied to {page.url}: matched term '{term}'")
                    break  # Only apply bonus once per page
        
        # 6. BUSINESS-FIRST KEYWORD SCORING - Three-tier approach (secondary to homepage terms)
        keyword_matched = homepage_bonus_applied  # If homepage bonus applied, consider it matched
        
        # TIER 1: CORE BUSINESS VALUE (Massive bonuses - what they DO/SELL)
        # Only apply core business scoring if NO homepage match was found to avoid double-scoring
        if not homepage_bonus_applied:
            for keyword, points in self.core_business_keywords.items():
                if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower or \
                   path_lower == f'/{keyword}' or path_lower.endswith(f'/{keyword}'):
                    score += points * 2.0  # DOUBLE bonus for exact core business matches
                    page.has_keywords = True
                    keyword_matched = True
                elif keyword in path_lower:
                    score += points * 1.5  # Strong bonus for partial core business matches
                    page.has_keywords = True
                    keyword_matched = True
                
        # TIER 2: BUSINESS SUPPORT (Standard bonuses - how they operate) 
        # Only apply if no homepage bonus was applied
        if not homepage_bonus_applied and not keyword_matched:
            for keyword, points in self.business_support_keywords.items():
                if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower or \
                   path_lower == f'/{keyword}' or path_lower.endswith(f'/{keyword}'):
                    score += points * 1.2  # Modest bonus for exact support matches
                    page.has_keywords = True
                    keyword_matched = True
                elif keyword in path_lower:
                    score += points * 0.8  # Reduced bonus for partial support matches
                    page.has_keywords = True
                    keyword_matched = True
                    
        # TIER 3: ORGANIZATIONAL INFO (Minimal bonuses - who/where they are)
        # Only apply if no homepage bonus and no business keywords matched
        if not homepage_bonus_applied and not keyword_matched:
            for keyword, points in self.organizational_keywords.items():
                if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower or \
                   path_lower == f'/{keyword}' or path_lower.endswith(f'/{keyword}'):
                    score += points * 0.8  # Small bonus for exact organizational matches
                    page.has_keywords = True
                    keyword_matched = True
                elif keyword in path_lower:
                    score += points * 0.5  # Minimal bonus for partial organizational matches
                    page.has_keywords = True
                    keyword_matched = True
            
        # 7. URL depth score (shorter paths are more important) - ENHANCED
        path_parts = [part for part in parsed_url.path.split('/') if part]
        page.depth = len(path_parts)
        if page.depth == 0:  # Root level
            score += 25  # Boosted for core pages
        elif page.depth == 1:  # First level - where most core pages live
            score += 20  # Significantly boosted
        elif page.depth == 2:  # Second level
            score += 8
        elif page.depth == 3:  # Third level
            score += 2
        # Deeper pages get penalty
        elif page.depth > 3:
            score -= (page.depth - 3) * 3  # Increasing penalty for depth
            
        # 8. Priority score (if available in sitemap)
        if page.priority is not None:
            score += page.priority * 15  # Reduced weight compared to our logic
        
        # 9. Change frequency score
        if page.changefreq:
            freq_score = self.freq_weights.get(page.changefreq.lower(), 0.5)
            score += freq_score * 8  # Reduced weight
        
        # 10. Last modification recency (BOOSTED for fresh content)
        if page.lastmod:
            try:
                lastmod_date = datetime.fromisoformat(page.lastmod.replace('Z', '+00:00'))
                days_old = (datetime.now() - lastmod_date.replace(tzinfo=None)).days
                
                # Much higher recency bonus with faster decay
                if days_old <= 7:      # Within 1 week
                    recency_score = 30
                elif days_old <= 30:   # Within 1 month  
                    recency_score = 20
                elif days_old <= 90:   # Within 3 months
                    recency_score = 15
                elif days_old <= 180:  # Within 6 months
                    recency_score = 10
                elif days_old <= 365:  # Within 1 year
                    recency_score = 5
                else:                  # Older than 1 year
                    recency_score = 0
                
                score += recency_score
                
            except (ValueError, TypeError):
                # Handle various date formats
                try:
                    # Try without timezone info
                    lastmod_date = datetime.fromisoformat(page.lastmod.replace('T', ' ').replace('Z', ''))
                    days_old = (datetime.now() - lastmod_date).days
                    if days_old <= 30:
                        score += 20
                    elif days_old <= 90:
                        score += 10
                except:
                    pass
        
        # Apply Claude API intelligence boost if available (only for potentially high-scoring pages)
        if self.use_claude_api and self.claude_client and score > -50:  # Only call Claude for pages with decent base scores
            claude_boost = self._get_claude_intelligence_score(page)
            score += claude_boost
        
        return round(score, 2)
    
    def _get_claude_intelligence_score(self, page: PageInfo) -> float:
        """Use Claude API to intelligently analyze page importance"""
        try:
            # Extract domain and URL components for analysis
            parsed_url = urlparse(page.url)
            domain = parsed_url.netloc.replace('www.', '')
            path = parsed_url.path
            
            # Create a prompt for Claude to analyze the page
            prompt = f"""You must respond with ONLY a single number between -50 and +50. No text, no explanation.

URL: {page.url}
Domain: {domain}
Path: {path}

Rate this URL's business importance (-50 to +50):
- Homepage: +40
- About/Contact/Services: +30 to +35
- Product/service pages: +20 to +30
- Blog posts: -10 to +5
- Test/dev pages: -40
- Login/admin pages: -30

RESPOND WITH ONLY THE NUMBER (e.g. 25 or -15)"""

            # Call Claude API
            message = self.claude_client.messages.create(
                model="claude-3-haiku-20240307",  # Using Haiku for speed and cost efficiency
                max_tokens=10,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # Track actual token usage
            if hasattr(message, 'usage'):
                self.claude_input_tokens_used += message.usage.input_tokens
                self.claude_output_tokens_used += message.usage.output_tokens
            
            # Parse the response
            response_text = message.content[0].text.strip()
            
            # Extract number from response - try multiple approaches
            import re
            
            # First, try to parse as a direct number
            try:
                claude_score = float(response_text)
                # Clamp to expected range
                claude_score = max(-50, min(50, claude_score))
                return claude_score
            except ValueError:
                pass
            
            # Second, try regex to find number
            number_match = re.search(r'(-?\d+(?:\.\d+)?)', response_text)
            if number_match:
                try:
                    claude_score = float(number_match.group(1))
                    # Clamp to expected range
                    claude_score = max(-50, min(50, claude_score))
                    return claude_score
                except ValueError:
                    pass
            
            # If all parsing fails
            # Only log parsing failures to file, not console
            self.logger.debug(f"Could not parse Claude response: '{response_text}' for {page.url}")
            return 0
                
        except Exception as e:
            # Only log API errors to file, not console
            self.logger.debug(f"Claude API error for {page.url}: {e}")
            return 0
    
    def _is_blog_post(self, page: PageInfo) -> bool:
        """Check if a page is a blog post using comprehensive patterns"""
        # Use sitemap source if available
        if page.is_post:
            return True
            
        # Use the comprehensive blog post patterns
        return any(re.search(pattern, page.url, re.IGNORECASE) 
                  for pattern in self.blog_post_patterns)
    
    def display_top_pages(self, pages: List[PageInfo], count: int) -> Tuple[List[PageInfo], int]:
        """Display the top pages and get user confirmation for additional pages"""
        top_pages = pages[:count]
        
        # Use wider display format for better URL visibility
        separator_width = 180
        print(f"\n{'='*separator_width}")
        print(f"TOP {count} MOST IMPORTANT CORE BUSINESS PAGES TO SCRAPE")
        print(f"{'='*separator_width}")
        print(f"{'Rank':<4} {'Score':<6} {'Type':<5} {'Nav':<4} {'Depth':<5} {'Keywords':<8} {'URL':<120}")
        print(f"{'-'*separator_width}")
        
        for i, page in enumerate(top_pages, 1):
            keywords_mark = "Y" if page.has_keywords else ""  # Changed from ✓ to Y for Windows compatibility
            nav_mark = "NAV" if page.in_navigation else ""  # Mark navigation pages
            page_type = "Post" if self._is_blog_post(page) else "Page"
            # Show much longer URLs - only truncate if extremely long
            url_display = page.url[:115] + "..." if len(page.url) > 118 else page.url
            print(f"{i:<4} {page.score:<6} {page_type:<5} {nav_mark:<4} {page.depth:<5} {keywords_mark:<8} {url_display}")
        
        print(f"{'-'*separator_width}")
        # Count pages vs posts in selection
        pages_selected = sum(1 for page in top_pages if not self._is_blog_post(page))
        posts_selected = count - pages_selected
        nav_pages_selected = sum(1 for page in top_pages if page.in_navigation)
        
        # Count Shopify-specific pages if in Shopify mode
        if self.shopify_mode:
            collections_selected = sum(1 for page in top_pages if self._is_shopify_collection_page(page.url))
            products_selected = sum(1 for page in top_pages if self._is_shopify_product_page(page.url))
        
        claude_status = " + Claude AI" if self.use_claude_api and self.claude_client else ""
        print(f"Selection logic: BUSINESS-FIRST - What does the company DO/SELL?{claude_status}")
        if self.shopify_mode and hasattr(self, 'shopify_mode'):
            print(f"- Selected: {collections_selected} Collections, {products_selected} Products, {pages_selected - collections_selected - products_selected} Other Pages")
        else:
            print(f"- Selected: {pages_selected} Pages ({nav_pages_selected} from main navigation), {posts_selected} Posts")
        print(f"- TIER 1: CORE BUSINESS (procedures, services, products): 140-200 points")
        print(f"- TIER 2: BUSINESS SUPPORT (about, pricing, contact): 28-54 points") 
        print(f"- TIER 3: ORGANIZATIONAL (staff, locations, careers): 4-24 points")
        print(f"- Navigation menu pages: +200 bonus (company story)")
        print(f"- Homepage: +500 points (ABSOLUTE HIGHEST - most important)")
        print(f"- Recency bonus: +30pts(<1wk), +20pts(<1mo), +15pts(<3mo)")
        print(f"- Blog posts: -100 penalty (content marketing, not core business)")
        print(f"- Junk pages: -1000 penalty (test/dev/admin pages)")
        if self.shopify_mode:
            print(f"- Shopify collections: +80 bonus (category pages)")
            print(f"- Shopify products: -50 penalty (individual product pages)")
        if self.use_claude_api and self.claude_client:
            print(f"- Claude AI analysis: +/-50 point intelligent adjustments")
        print(f"PRIORITY: Services/Procedures/Products > About/Pricing > Staff/Locations")
        
        # Show next tier of important pages for user consideration
        additional_pages = self.display_next_tier_pages(pages, count)
        
        return top_pages, additional_pages
    
    def display_next_tier_pages(self, pages: List[PageInfo], initial_count: int) -> int:
        """Display next tier of pages and get user decision on additional scraping"""
        if len(pages) <= initial_count:
            print(f"\nNo additional pages available beyond the top {initial_count}.")
            return 0
            
        # Show next 25 most important pages (or remaining pages if fewer)
        next_tier_count = min(25, len(pages) - initial_count)
        next_tier_pages = pages[initial_count:initial_count + next_tier_count]
        
        print(f"\n{'='*180}")
        print(f"NEXT {next_tier_count} MOST IMPORTANT PAGES (for your consideration)")
        print(f"{'='*180}")
        print(f"{'Rank':<4} {'Score':<6} {'Type':<5} {'Nav':<4} {'Depth':<5} {'Keywords':<8} {'URL':<120}")
        print(f"{'-'*180}")
        
        for i, page in enumerate(next_tier_pages, initial_count + 1):
            keywords_mark = "Y" if page.has_keywords else ""
            nav_mark = "NAV" if page.in_navigation else ""
            page_type = "Post" if self._is_blog_post(page) else "Page"
            url_display = page.url[:115] + "..." if len(page.url) > 118 else page.url
            print(f"{i:<4} {page.score:<6} {page_type:<5} {nav_mark:<4} {page.depth:<5} {keywords_mark:<8} {url_display}")
        
        print(f"{'-'*180}")
        
        # Analyze next tier composition
        next_tier_pages_count = sum(1 for page in next_tier_pages if not self._is_blog_post(page))
        next_tier_posts_count = next_tier_count - next_tier_pages_count
        next_tier_nav_count = sum(1 for page in next_tier_pages if page.in_navigation)
        
        print(f"Next tier contains: {next_tier_pages_count} Pages ({next_tier_nav_count} navigation), {next_tier_posts_count} Posts")
        
        # Get user decision on additional pages
        max_additional = len(pages) - initial_count
        print(f"\nYou can scrape up to {max_additional} additional pages from the remaining {max_additional} total.")
        print(f"The next {next_tier_count} shown above are the most important of the remaining pages.")
        
        while True:
            try:
                response = input(f"\nHow many ADDITIONAL pages to scrape? (0-{max_additional}, default: {initial_count}): ").strip()
                if not response:
                    additional_count = initial_count  # Default to same number as initial request
                else:
                    additional_count = int(response)
                    
                if additional_count < 0:
                    print("Please enter a number 0 or greater")
                    continue
                elif additional_count > max_additional:
                    print(f"Maximum additional pages available: {max_additional}")
                    continue
                else:
                    break
                    
            except ValueError:
                print("Please enter a valid number")
        
        if additional_count > 0:
            print(f"\n✓ Will scrape {additional_count} additional pages (total: {initial_count + additional_count} pages)")
        else:
            print(f"\n✓ Will scrape only the initial {initial_count} pages (no additional pages)")
            
        return additional_count
    
    def extract_text_content(self, html_content: str, url: str) -> str:
        """Extract readable text content and image information from HTML"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script and style elements
            for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                script.decompose()
            
            extracted_content = []
            
            # Add page title
            title = soup.find('title')
            if title:
                extracted_content.append(f"PAGE TITLE: {title.get_text().strip()}\n")
            
            # Add main heading
            h1 = soup.find('h1')
            if h1:
                extracted_content.append(f"MAIN HEADING: {h1.get_text().strip()}\n")
            
            # Extract meta description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                extracted_content.append(f"META DESCRIPTION: {meta_desc.get('content').strip()}\n")
            
            # Extract readable text from main content areas
            main_content_selectors = [
                'main', '[role="main"]', '.main-content', '#main-content',
                '.content', '#content', 'article', '.post-content'
            ]
            
            main_text = []
            content_found = False
            
            for selector in main_content_selectors:
                main_element = soup.select_one(selector)
                if main_element:
                    main_text.append(main_element.get_text(separator=' ', strip=True))
                    content_found = True
                    break
            
            # If no main content area found, extract from body
            if not content_found:
                body = soup.find('body')
                if body:
                    main_text.append(body.get_text(separator=' ', strip=True))
            
            if main_text:
                # Clean up the text
                text = ' '.join(main_text)
                # Remove extra whitespace and normalize
                text = re.sub(r'\s+', ' ', text).strip()
                # Limit length to avoid extremely long content
                if len(text) > 10000:
                    text = text[:10000] + "... [Content truncated]"
                extracted_content.append(f"MAIN CONTENT:\n{text}\n")
            
            # Extract image information (alt text only)
            images = soup.find_all('img')
            if images:
                alt_texts = []
                for img in images[:20]:  # Limit to first 20 images
                    # Get alt text
                    alt = img.get('alt', '').strip()
                    if alt:
                        alt_texts.append(alt)
                    
                    # Get title attribute as fallback
                    title_attr = img.get('title', '').strip()
                    if not alt and title_attr:
                        alt_texts.append(title_attr)
                
                if alt_texts:
                    extracted_content.append("\nIMAGE ALT TEXT:")
                    for i, alt_text in enumerate(alt_texts, 1):
                        extracted_content.append(f"  IMAGE {i}: {alt_text}")
            
            # Extract headings structure
            headings = soup.find_all(['h2', 'h3', 'h4'])
            if headings:
                extracted_content.append("\nSECTION HEADINGS:")
                for heading in headings[:10]:  # Limit to first 10 headings
                    level = heading.name.upper()
                    text = heading.get_text().strip()
                    if text:
                        extracted_content.append(f"  {level}: {text}")
            
            return '\n'.join(extracted_content)
            
        except Exception as e:
            self.logger.error(f"Error extracting content from {url}: {e}")
            return f"ERROR: Could not extract content from this page. Raw HTML length: {len(html_content)} characters"
    
    def get_user_confirmation(self, pages: List[PageInfo], total_pages: int) -> bool:
        """Ask user for final confirmation to proceed with scraping"""
        print(f"\n{'='*80}")
        print(f"FINAL SELECTION: {len(pages)} PAGES TO SCRAPE")
        print(f"{'='*80}")
        
        for i, page in enumerate(pages, 1):
            nav_mark = " [NAV]" if page.in_navigation else ""
            page_type = " [BLOG]" if self._is_blog_post(page) else ""
            # Show clean URLs for confirmation
            from urllib.parse import urlparse
            parsed = urlparse(page.url)
            clean_path = parsed.path if parsed.path != '/' else '/'
            print(f"{i:2d}. {clean_path:<50} (score: {page.score:.1f}){nav_mark}{page_type}")
        
        print(f"\n{'='*80}")
        print("This process will:")
        print("- Download and extract readable text content from each page")
        print("- Extract image titles and alt text") 
        print("- Save content as organized text files")
        print("- Respect rate limits (2 second delay between requests)")
        print("- Log all activity to scraper.log")
        print(f"- Process {len(pages)} pages (estimated time: {len(pages) * 2} seconds)")
        
        while True:
            response = input(f"\nProceed with scraping these {len(pages)} pages? (y/n): ").strip().lower()
            if response in ['y', 'yes']:
                return True
            elif response in ['n', 'no']:
                return False
            else:
                print("Please enter 'y' or 'n'")
    
    def scrape_pages(self, pages: List[PageInfo]) -> Dict[str, str]:
        """Scrape the selected pages and extract readable text content"""
        results = {}
        total_pages = len(pages)
        
        self.logger.info(f"Starting to scrape {total_pages} pages...")
        
        for i, page in enumerate(pages, 1):
            try:
                print(f"Scraping {i}/{total_pages}: {page.url}")
                
                # Make request with rate limiting
                response = self.session.get(page.url, timeout=30)
                response.raise_for_status()
                
                # Extract readable content
                extracted_text = self.extract_text_content(response.text, page.url)
                
                # Store extracted content in memory (no file creation)
                results[page.url] = {
                    'content': extracted_text,
                    'page': page
                }
                
                self.logger.info(f"Extracted content: {page.url}")
                
                # Rate limiting
                time.sleep(2)
                
            except requests.RequestException as e:
                self.logger.error(f"Failed to scrape {page.url}: {e}")
                continue
            except Exception as e:
                self.logger.error(f"Unexpected error scraping {page.url}: {e}")
                continue
        
        self.logger.info(f"Scraping complete. {len(results)} pages processed")
        
        # Create consolidated markdown file in current directory
        markdown_file = self.create_markdown_summary(pages, results)
        
        return {page.url: markdown_file for page in pages if page.url in results}
    
    def create_markdown_summary(self, pages: List[PageInfo], results: Dict[str, dict]) -> str:
        """Create a consolidated markdown file from all scraped pages"""
        # Extract domain name for the title
        parsed_url = urlparse(self.sitemap_url)
        domain = parsed_url.netloc.replace('www.', '')
        
        # Create markdown file in current directory
        markdown_path = f"{domain.replace('.', '_')}_complete_content.md"
        
        with open(markdown_path, 'w', encoding='utf-8') as md_file:
            # Write header
            md_file.write(f"# {domain.title()} - Complete Website Content\n\n")
            md_file.write(f"*Scraped on {datetime.now().strftime('%Y-%m-%d at %H:%M:%S')}*\n\n")
            md_file.write(f"**Source:** {self.sitemap_url}\n\n")
            md_file.write("---\n\n")
            
            # Process each page in order
            for i, page in enumerate(pages, 1):
                if page.url in results:
                    content = results[page.url]['content']
                    
                    # Extract page title from content
                    page_title = "Untitled Page"
                    main_content = content
                    
                    # Find the title in content
                    lines = content.split('\n')
                    for j, line in enumerate(lines):
                        if line.startswith('PAGE TITLE:'):
                            page_title = line.replace('PAGE TITLE:', '').strip()
                            break
                    
                    # Clean the title for markdown
                    if not page_title or page_title == "Untitled Page":
                        page_title = f"Page {i}"
                    
                    # Write page section
                    md_file.write(f"## {page_title}\n\n")
                    md_file.write(f"**URL:** {page.url}\n")
                    md_file.write(f"**Score:** {page.score}\n\n")
                    
                    # Write content (clean it up for markdown)
                    if main_content:
                        # Basic markdown formatting improvements
                        formatted_content = main_content.replace('\n\n\n', '\n\n')
                        md_file.write(f"{formatted_content}\n\n")
                    else:
                        md_file.write("*Content could not be extracted*\n\n")
                    
                    md_file.write("---\n\n")
        
        self.logger.info(f"Created consolidated markdown file: {markdown_path}")
        print(f"\nConsolidated markdown file created: {markdown_path}")
        return markdown_path

def find_sitemap_url(base_url: str) -> str:
    """Try to find the sitemap URL for a given website"""
    import requests
    
    # Ensure URL has protocol
    if not base_url.startswith(('http://', 'https://')):
        base_url = 'https://' + base_url
    
    # Remove trailing slash
    base_url = base_url.rstrip('/')
    
    # Common sitemap locations to try
    sitemap_paths = [
        '/sitemap.xml',
        '/sitemap_index.xml',
        '/sitemaps.xml',
        '/sitemap1.xml',
        '/wp-sitemap.xml',
        '/sitemap/sitemap.xml'
    ]
    
    print(f"Looking for sitemap at {base_url}...")
    
    for path in sitemap_paths:
        test_url = base_url + path
        try:
            response = requests.head(test_url, timeout=10)
            if response.status_code == 200:
                print(f"Found sitemap: {test_url}")
                return test_url
        except:
            continue
    
    # If no sitemap found, try robots.txt
    try:
        robots_url = base_url + '/robots.txt'
        response = requests.get(robots_url, timeout=10)
        if response.status_code == 200:
            for line in response.text.split('\n'):
                if line.lower().startswith('sitemap:'):
                    sitemap_url = line.split(':', 1)[1].strip()
                    print(f"Found sitemap in robots.txt: {sitemap_url}")
                    return sitemap_url
    except:
        pass
    
    # Default fallback
    default_url = base_url + '/sitemap.xml'
    print(f"No sitemap found, trying default: {default_url}")
    return default_url

def main():
    """Main function to run the scraper"""
    print("Intelligent Sitemap Web Scraper")
    print("=" * 40)
    
    # Get website URL from user
    website_url = input("Enter website URL (e.g., example.com): ").strip()
    if not website_url:
        print("Error: Website URL is required")
        return
    
    # Find sitemap URL first for detection
    sitemap_url = find_sitemap_url(website_url)
    
    # Create temporary scraper instance for site detection
    temp_scraper = SitemapScraper(sitemap_url, use_claude_api=False, shopify_mode=False)
    
    # Detect site type
    print("\n" + "="*60)
    print("SITE TYPE DETECTION")
    print("="*60)
    
    site_info = temp_scraper.detect_site_type()
    
    print(f"\n📊 Detection Results:")
    print(f"  Site Type: {site_info['type'].upper()}")
    print(f"  Platform: {site_info['platform'] or 'Not detected'}")
    print(f"  Confidence: {site_info['confidence'].upper()}")
    
    if site_info['indicators']:
        print(f"\n🔍 Indicators found:")
        for indicator in site_info['indicators']:
            print(f"  • {indicator}")
    
    if site_info['estimated_product_pages'] > 0:
        print(f"\n⚠️  Estimated product pages: {site_info['estimated_product_pages']:,}+")
        print(f"  (Crawling all products could take {site_info['estimated_product_pages'] * 2 // 60} minutes)")
    
    # Ask user to confirm or correct the detection
    print("\n" + "="*60)
    print("SITE TYPE CONFIRMATION")
    print("="*60)
    
    # Show what was detected with visual indicator
    if site_info['type'] == 'ecommerce':
        if site_info['platform'] == 'Shopify':
            print(f"\n🛒 AUTO-DETECTED: Shopify Store (confidence: {site_info['confidence']})")
        elif site_info['platform'] == 'WooCommerce':
            print(f"\n🛒 AUTO-DETECTED: WooCommerce Store (confidence: {site_info['confidence']})")
        elif site_info['platform'] == 'BigCommerce':
            print(f"\n🛒 AUTO-DETECTED: BigCommerce Store (confidence: {site_info['confidence']})")
        else:
            print(f"\n🛒 AUTO-DETECTED: E-commerce Site (platform unknown)")
    else:
        print(f"\n📄 AUTO-DETECTED: Standard Website (not e-commerce)")
    
    # ALWAYS show the menu for user to confirm or correct
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
    
    # Set default based on detection
    if site_info['type'] == 'ecommerce':
        if site_info['platform'] == 'Shopify':
            default_choice = '1'
        elif site_info['platform'] == 'WooCommerce':
            default_choice = '2'
        elif site_info['platform'] == 'BigCommerce':
            default_choice = '3'
        else:
            default_choice = '5'
    else:
        default_choice = '6'
    
    # Get user input with default
    while True:
        choice = input(f"\nEnter choice (1-8, default: {default_choice}): ").strip()
        if not choice:
            choice = default_choice
        
        if choice in ['1', '2', '3', '4', '5', '6', '7', '8']:
            break
        else:
            print("Please enter a number between 1 and 8")
    
    # Process the choice
    shopify_mode = False
    skip_products = True
    site_type_name = ""
    
    if choice == '1':
        shopify_mode = True
        site_type_name = "Shopify"
        print("\n✅ Site type set to: SHOPIFY STORE")
    elif choice == '2':
        shopify_mode = True
        site_type_name = "WooCommerce"
        print("\n✅ Site type set to: WOOCOMMERCE STORE")
    elif choice == '3':
        shopify_mode = True
        site_type_name = "BigCommerce"
        print("\n✅ Site type set to: BIGCOMMERCE STORE")
    elif choice == '4':
        shopify_mode = True
        site_type_name = "Magento"
        print("\n✅ Site type set to: MAGENTO STORE")
    elif choice == '5':
        shopify_mode = True
        site_type_name = "E-commerce"
        print("\n✅ Site type set to: E-COMMERCE PLATFORM")
    elif choice == '6':
        site_type_name = "Standard"
        print("\n✅ Site type set to: STANDARD WEBSITE")
    elif choice == '7':
        site_type_name = "WordPress"
        print("\n✅ Site type set to: WORDPRESS (NON-COMMERCE)")
    elif choice == '8':
        site_type_name = "Custom"
        print("\n✅ Site type set to: CUSTOM/STATIC SITE")
    
    # If e-commerce mode is enabled, ask about skipping products
    if shopify_mode:
        print("\n" + "="*60)
        print("E-COMMERCE MODE ENABLED")
        print("="*60)
        print("\nE-commerce sites typically have:")
        print("  • Few collection/category pages (10-50) - HIGH VALUE")
        print("  • Many individual product pages (100-10,000+) - LOW VALUE for overview")
        print("\nRecommendation: Skip individual products, keep collections")
        
        skip_input = input("\nSkip individual product pages? (y/n, default: y): ").strip().lower()
        if skip_input == 'n' or skip_input == 'no':
            skip_products = False
            print("⚠️  Will include individual product pages (this may take MUCH longer)")
        else:
            print("✅ Will skip individual product pages but INCLUDE collection/category pages")
    
    print("\n" + "="*60)
    
    # Get number of pages to scrape
    while True:
        try:
            num_pages = input("\nHow many pages to scrape? (default: 10): ").strip()
            if not num_pages:
                num_pages = 10
            else:
                num_pages = int(num_pages)
            if num_pages <= 0 or num_pages > 50:
                print("Please enter a number between 1 and 50")
                continue
            break
        except ValueError:
            print("Please enter a valid number")
    
    # Ask about Claude API enhancement
    use_claude = False
    claude_input = input("\nUse Claude API for smarter page analysis? (y/n, default: n): ").strip().lower()
    if claude_input in ['y', 'yes']:
        use_claude = True
        print("Claude API enabled - will provide intelligent page importance analysis")
    
    # Initialize the actual scraper with user-confirmed settings
    scraper = SitemapScraper(sitemap_url, use_claude_api=use_claude, shopify_mode=shopify_mode, skip_products=skip_products)
    
    try:
        # Parse sitemap
        pages = scraper.parse_sitemap(sitemap_url)
        
        if not pages:
            print("No pages found in sitemap")
            return
        
        # Show Claude API cost estimate if enabled (before doing expensive analysis)
        if use_claude and not scraper.get_claude_cost_confirmation(len(pages)):
            # User declined Claude API, but we can continue with standard scoring
            pass
        
        # Calculate importance scores
        scored_pages = scraper.calculate_page_importance(pages)
        
        # Display top pages and get user decision on additional pages
        top_pages, additional_count = scraper.display_top_pages(scored_pages, num_pages)
        
        # Combine initial selection with additional pages based on user decision
        total_pages_to_scrape = top_pages
        if additional_count > 0:
            additional_pages = scored_pages[num_pages:num_pages + additional_count]
            total_pages_to_scrape = top_pages + additional_pages
        
        # Get final user confirmation
        if scraper.get_user_confirmation(total_pages_to_scrape, len(scored_pages)):
            # Scrape pages
            results = scraper.scrape_pages(total_pages_to_scrape)
            print(f"\nScraping completed! {len(results)} pages processed")
            print(f"Initial selection: {len(top_pages)} pages")
            if additional_count > 0:
                print(f"Additional pages: {additional_count} pages")
                
            # Show actual Claude API costs if used
            if use_claude and scraper.claude_input_tokens_used > 0:
                actual_costs = scraper.calculate_actual_claude_cost()
                print(f"\nClaude API Usage Summary:")
                print(f"  Input tokens used:  {scraper.claude_input_tokens_used:,}")
                print(f"  Output tokens used: {scraper.claude_output_tokens_used:,}")
                print(f"  Actual cost: ${actual_costs['total_cost']:.4f}")
        else:
            print("Scraping cancelled by user")
    
    except SystemExit as e:
        # Script stopped due to 403 error or user cancellation
        print(f"\nScript terminated: {e}")
        exit(1)
    except Exception as e:
        print(f"Error: {e}")
        logging.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
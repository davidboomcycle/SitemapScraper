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
    
    def __init__(self, sitemap_url: str, use_claude_api: bool = False):
        self.sitemap_url = sitemap_url
        self.use_claude_api = use_claude_api
        self.claude_client = None
        self.navigation_urls = []  # Will store high-priority navigation URLs
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
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
        
        # High-priority keywords for critical pages
        self.critical_keywords = {
            'home': 50,      # Homepage indicators
            'index': 45,
            'main': 40,
            'about': 35,     # About page variants
            'about-us': 35,
            'contact': 5,    # Contact page variants - minimal bonus (just forms/info)
            'contact-us': 5,
            'reach-us': 5,
            'services': 25,  # Main service pages
            'products': 25,  # Main product pages
            'solutions': 25,
        }
        
        # Medium-priority keywords for important pages
        self.important_keywords = {
            'pricing': 20,
            'plans': 20,
            'features': 18,
            'why-us': 18,
            'team': 15,
            'careers': 15,
            'jobs': 15,
            'support': 15,
            'help': 15,
            'faq': 15,
            'portfolio': 12,
            'work': 12,
            'case-studies': 12,
            'testimonials': 10,
            'reviews': 10,
            'news': 10,
            'blog': 1,  # Severely reduced - main blog pages only
        }
        
        # Blog post detection patterns (heavy penalty)
        self.blog_post_patterns = [
            r'/blog/.+',              # ANY URL with /blog/ followed by anything
            r'/post/.+',              # Any post URLs
            r'/posts/.+',             # Plural posts URLs
            r'/article/.+',           # Any article URLs  
            r'/articles/.+',          # Plural articles URLs
            r'/news/.+',              # Any news URLs
            r'/\d{4}/\d{2}/',         # Date-based URLs
            # Long descriptive URLs (typical of blog posts)
            r'/[^/]*-[^/]*-[^/]*-[^/]*-[^/]*/',  # URLs with 4+ hyphens (like how-to articles)
            r'/how-[^/]+/',           # How-to articles
            r'/why-[^/]+/',           # Why articles  
            r'/what-[^/]+/',          # What articles
            r'/when-[^/]+/',          # When articles
            r'/where-[^/]+/',         # Where articles
            r'/\d+-[^/]+-[^/]+-[^/]+/', # Number-based articles (5-ways-to, 10-tips, etc.)
            r'/guide-[^/]+/',         # Guide articles
            r'/tips-[^/]+/',          # Tips articles
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
            r'/privacy',              # Privacy policy
            r'/terms',                # Terms pages
            r'/legal',                # Legal pages
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
    
    def parse_sitemap(self, sitemap_url: str, is_post_sitemap: bool = False) -> List[PageInfo]:
        """Parse sitemap.xml and extract page information"""
        self.logger.info(f"Fetching sitemap from: {sitemap_url}")
        
        try:
            # First attempt with normal request
            response = self.session.get(sitemap_url, timeout=30)
            response.raise_for_status()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 403:
                self.logger.warning(f"403 Forbidden - trying alternative approaches...")
                
                # Try different User-Agent strings
                user_agents = [
                    'Googlebot/2.1 (+http://www.google.com/bot.html)',
                    'Mozilla/5.0 (compatible; Bingbot/2.0; +http://www.bing.com/bingbot.htm)',
                    'curl/7.68.0',
                    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
                ]
                
                for ua in user_agents:
                    try:
                        self.logger.info(f"Trying with User-Agent: {ua[:50]}...")
                        headers = self.session.headers.copy()
                        headers['User-Agent'] = ua
                        response = self.session.get(sitemap_url, headers=headers, timeout=30)
                        response.raise_for_status()
                        self.logger.info("Success with alternative User-Agent!")
                        break
                    except requests.RequestException:
                        continue
                else:
                    # If all User-Agents fail, try with delay and minimal headers
                    try:
                        self.logger.info("Trying with minimal headers and delay...")
                        time.sleep(2)  # Add delay
                        minimal_headers = {'User-Agent': 'curl/7.68.0'}
                        response = requests.get(sitemap_url, headers=minimal_headers, timeout=30)
                        response.raise_for_status()
                        self.logger.info("Success with minimal headers!")
                    except requests.RequestException:
                        self.logger.error(f"All bypass attempts failed for: {sitemap_url}")
                        raise e
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
                
                # Parse sitemaps in priority order
                for sub_sitemap_url in sub_sitemaps:
                    is_post = 'post' in sub_sitemap_url.lower()
                    self.logger.info(f"Parsing {'post' if is_post else 'page'} sitemap: {sub_sitemap_url}")
                    pages.extend(self.parse_sitemap(sub_sitemap_url, is_post))
                
                return pages
            
            # Parse regular sitemap
            for url in root:
                loc_elem = url.find('.//{http://www.sitemaps.org/schemas/sitemap/0.9}loc')
                if loc_elem is not None:
                    page_info = PageInfo(url=loc_elem.text, is_post=is_post_sitemap)
                    
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
            return pages
            
        except ET.ParseError as e:
            self.logger.error(f"Failed to parse sitemap XML: {e}")
            raise
    
    def calculate_page_importance(self, pages: List[PageInfo]) -> List[PageInfo]:
        """Calculate importance scores - prioritize PAGES over blog posts"""
        self.logger.info("Calculating page importance scores...")
        
        # Extract navigation URLs from homepage for priority scoring
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
        
        # Separate pages from blog posts using both sitemap source and URL patterns
        regular_pages = []
        blog_posts = []
        
        for page in deduplicated_pages:
            if self._is_blog_post(page):
                blog_posts.append(page)
            else:
                regular_pages.append(page)
        
        print(f"Found {len(regular_pages)} regular pages and {len(blog_posts)} blog posts")
        
        # Score regular pages first
        for page in regular_pages:
            page.score = self._calculate_page_score(page)
        
        # Score blog posts (will only be used if we need more than available pages)
        for page in blog_posts:
            page.score = self._calculate_page_score(page) - 100  # Still penalty, but not extreme
        
        # Sort regular pages by score
        regular_pages.sort(key=lambda p: p.score, reverse=True)
        blog_posts.sort(key=lambda p: p.score, reverse=True)
        
        # Combine: pages first, then blog posts if needed
        all_scored = regular_pages + blog_posts
        
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
            
            return filtered_nav_urls
            
        except Exception as e:
            print(f"Could not extract navigation: {e}")
            return []
    
    def _calculate_page_score(self, page: PageInfo) -> float:
        """Calculate score for a single page"""
        score = 0.0
        parsed_url = urlparse(page.url)
        url_lower = page.url.lower()
        path_lower = parsed_url.path.lower()
        
        # 1. Check for junk/test pages (heavy penalty)
        is_junk = any(re.search(pattern, page.url, re.IGNORECASE) 
                     for pattern in self.junk_patterns)
        if is_junk:
            score -= 1000  # Heavy penalty for junk pages (essentially eliminates them)
            
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
        
        # 5. Critical keyword matching (exact matches get full points) - BOOSTED
        for keyword, points in self.critical_keywords.items():
            # Check for exact keyword matches in path
            if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower or \
               path_lower == f'/{keyword}' or path_lower.endswith(f'/{keyword}'):
                score += points * 1.5  # Boost core business pages even more
                page.has_keywords = True
            # Partial matches get reduced points
            elif keyword in path_lower:
                score += points * 0.8  # Slightly better partial matches
                page.has_keywords = True
            
        # 6. Important keyword matching
        for keyword, points in self.important_keywords.items():
            if f'/{keyword}' in path_lower or f'{keyword}/' in path_lower:
                score += points
                page.has_keywords = True
            elif keyword in path_lower:
                score += points * 0.5
                page.has_keywords = True
            
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
        
        # Apply Claude API intelligence boost if available
        if self.use_claude_api and self.claude_client:
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
        """Check if a page is a blog post"""
        # Use sitemap source if available, otherwise simple URL patterns
        if page.is_post:
            return True
            
        # Simple fallback patterns for obvious blog posts
        obvious_post_patterns = [
            r'/blog/',
            r'/news/',
            r'/\d{4}/',  # Year in URL
            r'/post/',
            r'/article/',
        ]
        
        return any(pattern in page.url.lower() for pattern in obvious_post_patterns)
    
    def display_top_pages(self, pages: List[PageInfo], count: int) -> List[PageInfo]:
        """Display the top pages and get user confirmation"""
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
        
        claude_status = " + Claude AI" if self.use_claude_api and self.claude_client else ""
        print(f"Selection logic: NAVIGATION FIRST, then PAGES, prioritizing RECENT updates{claude_status}")
        print(f"- Selected: {pages_selected} Pages ({nav_pages_selected} from main navigation), {posts_selected} Posts")
        print(f"- Navigation menu pages get HIGHEST priority (200 points) - these tell the company story")
        print(f"- Regular pages (non-blog) are selected first by score")
        print(f"- Recently updated pages get major bonus (up to +30 points)")
        print(f"- Blog posts only included if <{count} regular pages available")
        print(f"- Junk pages filtered out (test/dev/query strings): -1000 penalty")
        if self.use_claude_api and self.claude_client:
            print(f"- Claude AI analysis: Intelligent +/-50 point adjustments")
        print(f"- Homepage: 500 points (ABSOLUTE HIGHEST - most important page)")
        print(f"- Navigation menu pages: 200 points")
        print(f"- Critical pages (about, contact, services): 38-75 points")
        print(f"- Recency bonus: 30pts(<1wk), 20pts(<1mo), 15pts(<3mo), 10pts(<6mo)")
        print(f"- URL depth bonus: Root=25, Level1=20, Level2=8 points")
        
        return top_pages
    
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
    
    def get_user_confirmation(self, pages: List[PageInfo]) -> bool:
        """Ask user for confirmation to proceed with scraping"""
        print(f"\n{'='*80}")
        print(f"SELECTED PAGES TO SCRAPE ({len(pages)} pages)")
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
    
    # Get number of pages to scrape
    while True:
        try:
            num_pages = input("How many pages to scrape? (default: 10): ").strip()
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
    claude_input = input("Use Claude API for smarter page analysis? (y/n, default: n): ").strip().lower()
    if claude_input in ['y', 'yes']:
        use_claude = True
        print("Claude API enabled - will provide intelligent page importance analysis")
    
    # Find sitemap URL
    sitemap_url = find_sitemap_url(website_url)
    
    # Initialize scraper
    scraper = SitemapScraper(sitemap_url, use_claude_api=use_claude)
    
    try:
        # Parse sitemap
        pages = scraper.parse_sitemap(sitemap_url)
        
        if not pages:
            print("No pages found in sitemap")
            return
        
        # Calculate importance scores
        scored_pages = scraper.calculate_page_importance(pages)
        
        # Display top pages
        top_pages = scraper.display_top_pages(scored_pages, num_pages)
        
        # Get user confirmation
        if scraper.get_user_confirmation(top_pages):
            # Scrape pages
            results = scraper.scrape_pages(top_pages)
            print(f"\nScraping completed! {len(results)} pages processed")
        else:
            print("Scraping cancelled by user")
    
    except Exception as e:
        print(f"Error: {e}")
        logging.error(f"Fatal error: {e}")

if __name__ == "__main__":
    main()
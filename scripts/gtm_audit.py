#!/usr/bin/env python3
"""
GTM & Tag Audit Script

Audits any website for Google Tag Manager containers, GA4/UA properties,
third-party tracking scripts, cookies, and compliance issues.

Usage:
    python gtm_audit.py https://www.example.com
    python gtm_audit.py https://www.example.com --pdf
    python gtm_audit.py https://www.example.com --output report.md
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# Data collection
# ---------------------------------------------------------------------------

def collect_data(url: str, timeout_ms: int = 30000, headless: bool = False) -> dict:
    """Launch a browser, navigate to the URL, and collect all tag data.

    Uses headed mode by default because many sites (especially those behind
    Akamai, Cloudflare, etc.) block headless browsers. The window is positioned
    offscreen so it doesn't interfere with your work.  Pass --headless to
    override if the target site allows it.
    """
    data = {
        "url": url,
        "audit_date": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "network_requests": [],
        "scripts": [],
    }

    with sync_playwright() as p:
        launch_args = ["--window-position=-9999,-9999"] if not headless else []
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="en-US",
        )
        page = context.new_page()

        # Capture network requests
        captured_requests = []

        def on_request(request):
            captured_requests.append({
                "url": request.url,
                "method": request.method,
                "resource_type": request.resource_type,
            })

        page.on("request", on_request)

        print(f"  Loading {url} ...")
        try:
            response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            if response and response.status == 403:
                browser.close()
                if headless:
                    print("  Got 403 — site likely blocks headless browsers.")
                    print("  Retrying in headed mode (window will appear offscreen) ...")
                    browser = p.chromium.launch(headless=False, args=["--window-position=-9999,-9999"])
                    context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="en-US")
                    page = context.new_page()
                    page.on("request", on_request)
                    response = page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                    if response and response.status == 403:
                        print("  ERROR: Still getting 403. The site may require authentication or be geo-restricted.")
                        browser.close()
                        return data
                else:
                    print("  ERROR: Got 403 — the site may require authentication or be geo-restricted.")
                    return data
        except Exception as e:
            print(f"  Warning: page load issue ({e}), continuing with partial data...")

        # Wait a bit for late-firing tags
        page.wait_for_timeout(3000)

        data["network_requests"] = captured_requests

        # Run analysis JS in the browser
        print("  Extracting tag data ...")
        data["page_analysis"] = page.evaluate("""() => {
            const results = {};

            // --- GTM and GA IDs ---
            const scripts = Array.from(document.querySelectorAll('script'));
            const gtmIds = new Set();
            const gaIds = new Set();
            const allScriptSrcs = [];
            const inlineSnippets = [];

            scripts.forEach(s => {
                const src = s.src || '';
                const text = s.textContent || '';
                const combined = src + ' ' + text;

                if (src) allScriptSrcs.push(src);

                // GTM IDs
                const gtmMatches = combined.match(/GTM-[A-Z0-9]+/g);
                if (gtmMatches) gtmMatches.forEach(id => gtmIds.add(id));

                // GA4 IDs
                const ga4Matches = combined.match(/G-[A-Z0-9]+/g);
                if (ga4Matches) ga4Matches.forEach(id => gaIds.add(id));

                // UA IDs
                const uaMatches = combined.match(/UA-\\d+-\\d+/g);
                if (uaMatches) uaMatches.forEach(id => gaIds.add(id));

                // Inline tracking snippets
                if (/dataLayer|gtag|analytics|pixel|fbq|_satellite|hj\\(|clarity|_paq/.test(text)) {
                    inlineSnippets.push(text.substring(0, 800));
                }
            });

            results.gtmIds = [...gtmIds];
            results.gaIds = [...gaIds];
            results.allScriptSrcs = allScriptSrcs;
            results.inlineSnippets = inlineSnippets;

            // --- dataLayer ---
            results.dataLayer = typeof dataLayer !== 'undefined'
                ? JSON.parse(JSON.stringify(dataLayer)).slice(0, 30)
                : null;

            // --- google_tag_manager keys ---
            results.gtmKeys = typeof google_tag_manager !== 'undefined'
                ? Object.keys(google_tag_manager)
                : null;

            // --- Cookies ---
            results.cookies = document.cookie.split(';').map(c => c.trim());

            // --- Consent banner ---
            results.hasConsentBanner = !!(
                document.querySelector('[class*="consent"], [id*="consent"], [class*="cookie-banner"], [id*="cookie"], [class*="gdpr"], [id*="gdpr"], [class*="onetrust"], [id*="onetrust"]')
            );

            // --- Meta tags ---
            results.metaTags = Array.from(document.querySelectorAll('meta')).map(m => ({
                name: m.getAttribute('name') || m.getAttribute('property') || m.getAttribute('http-equiv'),
                content: m.getAttribute('content')
            })).filter(m => m.name);

            // --- noscript GTM ---
            results.noscriptGTM = Array.from(document.querySelectorAll('noscript')).some(
                ns => ns.innerHTML.includes('googletagmanager')
            );

            // --- Third-party domains ---
            const perfEntries = performance.getEntriesByType('resource');
            const pageDomain = location.hostname;
            const thirdPartyDomains = new Set();
            perfEntries.forEach(e => {
                try {
                    const h = new URL(e.name).hostname;
                    if (!h.includes(pageDomain.replace('www.', ''))) thirdPartyDomains.add(h);
                } catch {}
            });
            results.thirdPartyDomains = [...thirdPartyDomains].sort();
            results.totalRequests = perfEntries.length;

            // --- Performance ---
            const nav = performance.getEntriesByType('navigation')[0];
            const paint = performance.getEntriesByType('paint');
            results.performance = {
                domComplete: nav ? Math.round(nav.domComplete) : null,
                loadComplete: nav ? Math.round(nav.loadEventEnd) : null,
                fcp: null,
            };
            paint.forEach(p => {
                if (p.name === 'first-contentful-paint') {
                    results.performance.fcp = Math.round(p.startTime);
                }
            });

            // --- Page title & generator ---
            results.pageTitle = document.title;
            const gen = document.querySelector('meta[name="Generator"], meta[name="generator"]');
            results.generator = gen ? gen.getAttribute('content') : null;

            return results;
        }""")

        browser.close()

    return data


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------

KNOWN_VENDORS = {
    "googletagmanager.com": "Google Tag Manager",
    "google-analytics.com": "Google Analytics",
    "analytics.google.com": "Google Analytics",
    "doubleclick.net": "DoubleClick (Google Ads)",
    "googlesyndication.com": "Google AdSense",
    "googleadservices.com": "Google Ads Conversion",
    "facebook.net": "Meta Pixel",
    "facebook.com": "Meta Pixel",
    "connect.facebook.net": "Meta Pixel",
    "siteimproveanalytics.com": "Siteimprove",
    "crazyegg.com": "Crazy Egg",
    "hotjar.com": "Hotjar",
    "clarity.ms": "Microsoft Clarity",
    "go-mpulse.net": "Akamai mPulse",
    "akstat.io": "Akamai mPulse",
    "adobe.com": "Adobe Analytics",
    "omtrdc.net": "Adobe Analytics",
    "demdex.net": "Adobe Audience Manager",
    "adobedtm.com": "Adobe Launch",
    "newrelic.com": "New Relic",
    "nr-data.net": "New Relic",
    "segment.io": "Segment",
    "segment.com": "Segment",
    "heapanalytics.com": "Heap",
    "mixpanel.com": "Mixpanel",
    "amplitude.com": "Amplitude",
    "fullstory.com": "FullStory",
    "tealiumiq.com": "Tealium",
    "tealium.com": "Tealium",
    "matomo.cloud": "Matomo",
    "plausible.io": "Plausible",
    "linkedin.com/px": "LinkedIn Insight Tag",
    "snap.licdn.com": "LinkedIn Insight Tag",
    "ads.linkedin.com": "LinkedIn Ads",
    "twitter.com": "X/Twitter Pixel",
    "t.co": "X/Twitter Pixel",
    "snapchat.com": "Snapchat Pixel",
    "sc-static.net": "Snapchat Pixel",
    "tiktok.com": "TikTok Pixel",
    "pinterest.com": "Pinterest Tag",
    "pinimg.com": "Pinterest Tag",
    "bing.com": "Microsoft Advertising/UET",
    "bat.bing.com": "Microsoft UET",
    "youtube.com": "YouTube",
    "quantserve.com": "Quantcast",
    "chartbeat.com": "Chartbeat",
    "parsely.com": "Parse.ly",
    "onesignal.com": "OneSignal",
    "intercom.io": "Intercom",
    "drift.com": "Drift",
    "hubspot.com": "HubSpot",
    "hs-analytics.net": "HubSpot Analytics",
    "marketo.net": "Marketo",
    "mktoresp.com": "Marketo",
    "optimizely.com": "Optimizely",
    "launchdarkly.com": "LaunchDarkly",
    "cookiebot.com": "Cookiebot (Consent)",
    "onetrust.com": "OneTrust (Consent)",
    "trustarc.com": "TrustArc (Consent)",
    "dap.digitalgov.gov": "Federal DAP (GSA)",
}


VENDOR_CONTEXT = {
    "Google Tag Manager": {
        "description": "Tag management system that loads and manages other tracking tags.",
    },
    "Google Analytics": {
        "description": "Web analytics platform for measuring site traffic and user behavior.",
    },
    "DoubleClick (Google Ads)": {
        "description": "Google advertising platform. Collects data for audience building and remarketing.",
        "compliance_note": "Government sites should not enable advertising features. This fires because Google Signals is enabled in GA4 property settings.",
    },
    "Google AdSense": {
        "description": "Google advertising network for displaying ads.",
    },
    "Google Ads Conversion": {
        "description": "Google Ads conversion tracking.",
    },
    "Siteimprove": {
        "description": "Accessibility scanning, SEO monitoring, and analytics. Common in government.",
        "action": "Verify the team is actively using the Siteimprove dashboard — if not, this is unnecessary overhead.",
    },
    "Crazy Egg": {
        "description": "Heatmaps, scroll maps, and session recordings. Collects detailed visitor interaction data.",
        "compliance_note": "Session recording captures mouse movements, clicks, scrolls, and form interactions. On sites with sensitive content, a privacy impact assessment should confirm this is acceptable.",
        "is_session_recording": True,
    },
    "Hotjar": {
        "description": "Heatmaps, session recordings, and user feedback. Collects detailed visitor interaction data.",
        "compliance_note": "Session recording captures mouse movements, clicks, scrolls, and form interactions. A privacy impact assessment should confirm this is acceptable.",
        "is_session_recording": True,
    },
    "Microsoft Clarity": {
        "description": "Session replay and heatmap tool by Microsoft.",
        "compliance_note": "Session recording may capture sensitive interactions.",
        "is_session_recording": True,
    },
    "FullStory": {
        "description": "Digital experience analytics with session replay.",
        "compliance_note": "Session recording may capture sensitive interactions.",
        "is_session_recording": True,
    },
    "Akamai mPulse": {
        "description": "Real User Monitoring (RUM) for performance. Typically tied to Akamai CDN contracts.",
        "action": "Reasonable to keep if the ops team monitors it, but verify all loaded plugins are needed.",
    },
    "Federal DAP (GSA)": {
        "description": "Federal Digital Analytics Program run by GSA. Required for many federal executive branch websites.",
    },
    "Adobe Analytics": {
        "description": "Enterprise web analytics platform (Adobe Experience Cloud).",
    },
    "Adobe Launch": {
        "description": "Adobe tag management system (Adobe Experience Platform).",
    },
    "Meta Pixel": {
        "description": "Facebook/Meta advertising tracking pixel for conversion tracking and audience building.",
        "compliance_note": "Advertising tracker — verify this is appropriate for the site.",
    },
    "LinkedIn Insight Tag": {
        "description": "LinkedIn advertising conversion tracking and audience building.",
    },
    "HubSpot": {
        "description": "Marketing automation and CRM platform.",
    },
    "HubSpot Analytics": {
        "description": "HubSpot marketing analytics tracking.",
    },
    "Segment": {
        "description": "Customer data platform that routes analytics data to downstream tools.",
    },
    "New Relic": {
        "description": "Application performance monitoring (APM) and observability.",
    },
    "YouTube": {
        "description": "Video embedding and playback. Standard for embedded video content. Low concern.",
    },
    "OneTrust (Consent)": {
        "description": "Cookie consent management platform.",
    },
    "Cookiebot (Consent)": {
        "description": "Cookie consent management platform.",
    },
    "TrustArc (Consent)": {
        "description": "Privacy and consent management platform.",
    },
    "Optimizely": {
        "description": "A/B testing and experimentation platform.",
    },
    "LaunchDarkly": {
        "description": "Feature flag and feature management platform.",
    },
    "Mixpanel": {
        "description": "Product analytics platform focused on user behavior tracking.",
    },
    "Amplitude": {
        "description": "Product analytics platform for user behavior and engagement.",
    },
    "Heap": {
        "description": "Auto-capture analytics platform that records all user interactions.",
    },
    "Chartbeat": {
        "description": "Real-time content analytics for publishers.",
    },
    "Intercom": {
        "description": "Customer messaging and support platform.",
    },
}

VENDOR_ID_PATTERNS = {
    "Siteimprove": (r"siteanalyze_(\d+)\.js", "Account ID"),
    "Crazy Egg": (r"scripts/(\d+/\d+)\.js", "Account/Page"),
    "Akamai mPulse": (r"boomerang/([A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+-[A-Z0-9]+)", "API Key"),
    "Hotjar": (r"hotjar[^/]*/(\d{6,})", "Site ID"),
    "Microsoft Clarity": (r"clarity\.ms/tag/(\w+)", "Project ID"),
    "HubSpot": (r"hs-analytics\.net/analytics/.*?/(\d+)\.js", "Portal ID"),
    "HubSpot Analytics": (r"hs-analytics\.net/analytics/.*?/(\d+)\.js", "Portal ID"),
    "Chartbeat": (r"chartbeat\.com/js/(\d+)\.js", "Account ID"),
    "New Relic": (r"js-agent\.newrelic\.com/nr-\d+.*?xpid=([A-Za-z0-9]+)", "App ID"),
}


def identify_vendor(hostname: str) -> str | None:
    """Match a hostname to a known vendor."""
    for pattern, vendor in KNOWN_VENDORS.items():
        if pattern in hostname:
            return vendor
    return None


def extract_vendor_id(vendor_name: str, urls: list) -> tuple[str, str] | None:
    """Extract a vendor-specific account/project ID from URLs."""
    pattern_info = VENDOR_ID_PATTERNS.get(vendor_name)
    if not pattern_info:
        return None
    pattern, label = pattern_info
    for url in urls:
        match = re.search(pattern, url)
        if match:
            return (label, match.group(1))
    return None


def extract_ga4_events(network_requests: list, stream_id: str) -> list[str]:
    """Extract event names from GA4 collect requests for a specific stream."""
    events = set()
    for r in network_requests:
        url = r["url"]
        if "/g/collect" not in url and "/collect" not in url:
            continue
        if f"tid={stream_id}" not in url:
            continue
        parsed = parse_qs(urlparse(url).query)
        if "en" in parsed:
            events.add(unquote(parsed["en"][0]))
    return sorted(events)


def parse_cookie_session_count(cookies: list, suffix: str) -> int | None:
    """Extract session count from a _ga_ cookie value.

    Cookie format: GS1.1.<timestamp>.<session_count>.<rest>
    """
    for cookie in cookies:
        name, _, value = cookie.partition("=")
        name = name.strip()
        if name == f"_ga_{suffix}":
            parts = value.split(".")
            if len(parts) >= 4:
                try:
                    return int(parts[3])
                except (ValueError, IndexError):
                    pass
    return None


def analyze(data: dict) -> dict:
    """Analyze collected data and produce structured findings."""
    pa = data["page_analysis"]
    findings = {
        "url": data["url"],
        "audit_date": data["audit_date"],
        "page_title": pa.get("pageTitle", ""),
        "generator": pa.get("generator"),
        "gtm_containers": [],
        "ga4_streams": [],
        "ua_properties": [],
        "third_party_tags": [],
        "cookies": {"analytics": [], "orphan_ga": [], "mapped": []},
        "outbound_beacons": [],
        "compliance": {},
        "performance": pa.get("performance", {}),
        "data_layer": pa.get("dataLayer"),
        "recommendations": [],
        "gtm_access_items": [],
    }

    # --- GTM Containers ---
    for gtm_id in pa.get("gtmIds", []):
        has_noscript = pa.get("noscriptGTM", False)
        findings["gtm_containers"].append({
            "id": gtm_id,
            "has_noscript_fallback": has_noscript,
        })

    # --- GA4 & UA IDs ---
    for ga_id in pa.get("gaIds", []):
        if ga_id.startswith("G-"):
            # Determine load source
            source = "GTM"
            for snippet in pa.get("inlineSnippets", []):
                if ga_id in snippet and "dap.digitalgov.gov" in snippet.lower():
                    source = "DAP (hardcoded)"
                    break
            for src in pa.get("allScriptSrcs", []):
                if ga_id in src and "dap.digitalgov.gov" in src:
                    source = "DAP (hardcoded)"
                    break

            # Check for DoubleClick signals
            has_doubleclick = any(
                ga_id in r["url"] and "doubleclick" in r["url"]
                for r in data["network_requests"]
            )

            # Extract custom dimensions from collect requests
            custom_dims = {}
            for r in data["network_requests"]:
                if "/g/collect" in r["url"] and f"tid={ga_id}" in r["url"]:
                    parsed = parse_qs(urlparse(r["url"]).query)
                    for k, v in parsed.items():
                        if k.startswith("ep.") or k.startswith("up."):
                            custom_dims[k] = v[0] if v else ""
                    break

            # Extract events fired for this stream
            events = extract_ga4_events(data["network_requests"], ga_id)

            # Cookie and session count
            suffix = ga_id.replace("G-", "")
            session_count = parse_cookie_session_count(pa.get("cookies", []), suffix)
            cookie_name = f"_ga_{suffix}" if any(
                c.strip().startswith(f"_ga_{suffix}") for c in pa.get("cookies", [])
            ) else None

            findings["ga4_streams"].append({
                "id": ga_id,
                "source": source,
                "has_doubleclick": has_doubleclick,
                "custom_dimensions": custom_dims,
                "events": events,
                "cookie": cookie_name,
                "session_count": session_count,
            })
        elif ga_id.startswith("UA-"):
            # Determine how UA is loaded
            ua_source = "Unknown"
            for src in pa.get("allScriptSrcs", []):
                if "dap.digitalgov.gov" in src:
                    ua_parsed = parse_qs(urlparse(src).query)
                    if ua_parsed.get("pua", [None])[0] == ga_id:
                        ua_source = "DAP script `pua=` parameter"
                        break
            findings["ua_properties"].append({
                "id": ga_id,
                "status": "DEPRECATED (UA sunset July 2023)",
                "source": ua_source,
            })

    # --- Detect duplicate GA4 streams ---
    ga4_via_gtm = [s for s in findings["ga4_streams"] if s["source"] == "GTM"]
    for i, s1 in enumerate(ga4_via_gtm):
        for s2 in ga4_via_gtm[i + 1:]:
            dims1 = set(s1["custom_dimensions"].keys())
            dims2 = set(s2["custom_dimensions"].keys())
            if dims1 and dims2 and dims1 == dims2:
                s1["duplicate_of"] = s2["id"]
                s2["duplicate_of"] = s1["id"]

    # --- Third-party tags ---
    all_network_urls = [r["url"] for r in data["network_requests"]]
    seen_vendors = set()
    for domain in pa.get("thirdPartyDomains", []):
        vendor = identify_vendor(domain)
        if vendor and vendor not in seen_vendors:
            seen_vendors.add(vendor)

            # Collect all URLs related to this vendor
            script_urls = [
                s for s in pa.get("allScriptSrcs", [])
                if domain in s or any(p in s for p in KNOWN_VENDORS if KNOWN_VENDORS.get(p) == vendor)
            ]
            vendor_network_urls = [u for u in all_network_urls if domain in u]

            # Extract vendor account ID
            all_vendor_urls = script_urls + vendor_network_urls
            vendor_id = extract_vendor_id(vendor, all_vendor_urls)

            # Get vendor context
            ctx = VENDOR_CONTEXT.get(vendor, {})

            findings["third_party_tags"].append({
                "vendor": vendor,
                "domain": domain,
                "script_urls": script_urls[:3],
                "vendor_id": vendor_id,
                "description": ctx.get("description", ""),
                "compliance_note": ctx.get("compliance_note", ""),
                "action": ctx.get("action", ""),
                "is_session_recording": ctx.get("is_session_recording", False),
            })

    # --- Cookies ---
    current_ga4_suffixes = {gid.replace("G-", "") for gid in pa.get("gaIds", []) if gid.startswith("G-")}

    for cookie in pa.get("cookies", []):
        if not cookie:
            continue
        name = cookie.split("=")[0].strip()
        if name.startswith("_ga") or name.startswith("_gid") or name.startswith("_gat") or \
           name.startswith("_gcl") or name.startswith("_fbp") or name.startswith("_hj") or \
           name.startswith("si_") or name.startswith("ce_") or name.startswith("_dc"):
            findings["cookies"]["analytics"].append(cookie)

            # Map cookie to purpose and status
            status = "Active"
            if name.startswith("_ga_"):
                suffix = name.replace("_ga_", "")
                session_count = parse_cookie_session_count(pa.get("cookies", []), suffix)
                # Find matching stream
                matching_stream = next(
                    (s["id"] for s in findings["ga4_streams"] if s["id"].replace("G-", "") == suffix),
                    None,
                )
                if suffix not in current_ga4_suffixes:
                    status = "Stale — no matching GA4 property"
                    findings["cookies"]["orphan_ga"].append({
                        "cookie": cookie,
                        "suffix": suffix,
                        "session_count": session_count,
                        "note": f"No active GA4 property with suffix {suffix}",
                    })
                findings["cookies"]["mapped"].append({
                    "name": name,
                    "purpose": f"GA4 session ({matching_stream or 'unknown'})",
                    "status": status,
                    "session_count": session_count,
                })
            elif name == "_ga":
                findings["cookies"]["mapped"].append({
                    "name": name,
                    "purpose": "Google Analytics cross-domain client ID",
                    "status": status,
                })
            elif name.startswith("_gid"):
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "GA session ID (24hr)", "status": status,
                })
            elif name.startswith("_gat"):
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "GA throttling", "status": status,
                })
            elif name.startswith("_gcl"):
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "Google Ads conversion", "status": status,
                })
            elif name.startswith("_fbp"):
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "Meta Pixel", "status": status,
                })
            elif name.startswith("_hj"):
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "Hotjar", "status": status,
                })
            else:
                findings["cookies"]["mapped"].append({
                    "name": name, "purpose": "Analytics", "status": status,
                })

    # --- Outbound beacons ---
    beacon_urls = set()
    for r in data["network_requests"]:
        url_lower = r["url"].lower()
        is_beacon = any(pattern in url_lower for pattern in [
            "/g/collect", "/collect", "/j/collect",
            "doubleclick.net", "facebook.com/tr", "bat.bing.com",
            "akstat.io", "siteimproveanalytics", "/beacon",
            "pixel", "analytics.tiktok.com",
        ])
        if is_beacon and r["method"] in ("POST", "GET"):
            base = r["url"].split("?")[0]
            if base not in beacon_urls:
                beacon_urls.add(base)
                # Attribute beacon to vendor
                vendor = identify_vendor(urlparse(r["url"]).hostname or "")
                findings["outbound_beacons"].append({
                    "url": base,
                    "method": r["method"],
                    "vendor": vendor or "",
                })

    # --- Compliance ---
    is_gov = ".gov" in data["url"]
    findings["compliance"]["is_gov"] = is_gov
    findings["compliance"]["has_consent_banner"] = pa.get("hasConsentBanner", False)
    findings["compliance"]["doubleclick_on_gov"] = (
        is_gov and
        any(b["url"] for b in findings["outbound_beacons"] if "doubleclick" in b["url"])
    )
    findings["compliance"]["doubleclick_streams"] = [
        s["id"] for s in findings["ga4_streams"] if s["has_doubleclick"]
    ]
    findings["compliance"]["non_doubleclick_streams"] = [
        s["id"] for s in findings["ga4_streams"] if not s["has_doubleclick"]
    ]
    findings["compliance"]["persistent_cookies"] = len([
        c for c in findings["cookies"]["analytics"] if c.startswith("_ga")
    ])
    findings["compliance"]["third_party_domains"] = pa.get("thirdPartyDomains", [])
    findings["compliance"]["has_session_recording"] = any(
        t["is_session_recording"] for t in findings["third_party_tags"]
    )
    findings["compliance"]["session_recording_vendors"] = [
        t["vendor"] for t in findings["third_party_tags"] if t["is_session_recording"]
    ]

    # --- DAP detection ---
    findings["has_dap"] = any(
        "dap.digitalgov.gov" in s for s in pa.get("allScriptSrcs", [])
    )

    # Extract DAP parameters
    if findings["has_dap"]:
        for src in pa.get("allScriptSrcs", []):
            if "dap.digitalgov.gov" in src and "?" in src:
                findings["dap_params"] = parse_qs(urlparse(src).query)
                break

    # --- GTM access follow-up items ---
    if findings["gtm_containers"]:
        for c in findings["gtm_containers"]:
            cid = c["id"]
            findings["gtm_access_items"] = [
                f"Audit all tags in {cid} container (paused tags, unused triggers, orphan variables)",
                f"Review tag firing triggers — confirm no tags fire on all pages unnecessarily",
                f"Check for tag sequencing issues or race conditions",
                f"Export container JSON for version-controlled documentation",
                f"Review user permissions in GTM — who has publish access?",
            ]

    # --- Recommendations ---
    _generate_recommendations(findings)

    return findings


def _generate_recommendations(f: dict):
    """Generate cleanup recommendations based on findings."""
    recs = f["recommendations"]

    # --- Priority 1: Security / Compliance ---

    # DoubleClick on .gov
    if f["compliance"]["doubleclick_on_gov"]:
        streams = ", ".join(f["compliance"]["doubleclick_streams"])
        recs.append({
            "priority": 1,
            "category": "Compliance",
            "title": "Disable Google Signals on GA4 properties",
            "detail": (
                f"Google Signals is enabled on {streams}, causing DoubleClick/advertising "
                f"beacons to fire on a government site. This enables Google to build "
                f"advertising audience profiles from .gov visitors. Disable in GA4 "
                f"Admin > Data Settings > Data Collection > Google Signals."
            ),
            "impact": "Stops advertising beacons on .gov",
            "effort": "Low — GA4 Admin toggle",
        })

    # Session recording on sensitive sites
    if f["compliance"]["has_session_recording"]:
        vendors = ", ".join(f["compliance"]["session_recording_vendors"])
        recs.append({
            "priority": 1,
            "category": "Compliance",
            "title": f"Investigate session recording ({vendors})",
            "detail": (
                f"{vendors} captures mouse movements, clicks, scrolls, and form "
                f"interactions. Confirm active use, and verify a privacy impact "
                f"assessment exists for session recording on this site."
            ),
            "impact": "Privacy risk mitigation",
            "effort": "Low — review and document",
        })

    # --- Priority 2: Cleanup / Redundancy ---

    # Duplicate GA4 streams
    ga4_via_gtm = [s for s in f["ga4_streams"] if s["source"] == "GTM"]
    if len(ga4_via_gtm) >= 2:
        dim_sets = [set(s["custom_dimensions"].keys()) for s in ga4_via_gtm]
        if len(dim_sets) >= 2 and dim_sets[0] == dim_sets[1]:
            ids = ", ".join(s["id"] for s in ga4_via_gtm)
            beacon_savings = len(ga4_via_gtm) - 1
            dc_savings = sum(1 for s in ga4_via_gtm[1:] if s["has_doubleclick"])
            recs.append({
                "priority": 2,
                "category": "Redundancy",
                "title": "Remove duplicate GA4 stream",
                "detail": (
                    f"GA4 streams {ids} fire via GTM with identical custom dimensions. "
                    f"Determine which is the intended property and remove the other from GTM."
                ),
                "impact": f"Eliminates ~{beacon_savings + dc_savings} outbound beacons/pageview, reduces cookies",
                "effort": "Medium — requires GTM access",
            })

    # Legacy UA
    if f["ua_properties"]:
        for u in f["ua_properties"]:
            source_note = f" (loaded via {u['source']})" if u.get("source", "Unknown") != "Unknown" else ""
            recs.append({
                "priority": 2,
                "category": "Cleanup",
                "title": f"Remove legacy UA property {u['id']}",
                "detail": (
                    f"UA property {u['id']}{source_note} is still configured. Universal Analytics "
                    f"stopped processing data in July 2023. All data sent to this property is discarded."
                ),
                "impact": "Removes dead tracking code",
                "effort": "Low — configuration change",
            })

    # No consent banner with persistent cookies
    if not f["compliance"]["has_consent_banner"] and f["compliance"]["persistent_cookies"] > 0:
        recs.append({
            "priority": 2,
            "category": "Compliance",
            "title": "No consent/opt-out mechanism detected",
            "detail": (
                f"The site sets {f['compliance']['persistent_cookies']} persistent "
                f"analytics cookies but has no visible consent banner or opt-out "
                f"mechanism."
            ),
            "impact": "Compliance gap",
            "effort": "High — requires consent platform implementation",
        })

    # --- Priority 3: Optimization ---

    # Orphan cookies
    if f["cookies"]["orphan_ga"]:
        for orphan in f["cookies"]["orphan_ga"]:
            recs.append({
                "priority": 3,
                "category": "Cleanup",
                "title": f"Orphan GA cookie: _ga_{orphan['suffix']}",
                "detail": (
                    f"Cookie _ga_{orphan['suffix']} exists but no active GA4 property "
                    f"matches. This is a remnant of a previously removed property. "
                    f"The cookie will eventually expire on its own."
                ),
                "impact": "Hygiene",
                "effort": "None (self-resolving)",
            })

    # No noscript GTM fallback
    for c in f["gtm_containers"]:
        if not c["has_noscript_fallback"]:
            recs.append({
                "priority": 3,
                "category": "Best Practice",
                "title": f"Add noscript fallback for {c['id']}",
                "detail": (
                    f"GTM container {c['id']} does not have a <noscript> iframe "
                    f"fallback. Minor issue affecting JS-disabled users only."
                ),
                "impact": "Minor — JS-disabled users only",
                "effort": "Low — add noscript tag",
            })

    # Broken custom dimensions (values = "Not Found")
    for stream in f["ga4_streams"]:
        not_found = [k for k, v in stream["custom_dimensions"].items() if v == "Not Found"]
        if not_found:
            recs.append({
                "priority": 3,
                "category": "Data Quality",
                "title": f"Custom dimensions sending 'Not Found' in {stream['id']}",
                "detail": (
                    f"{len(not_found)} custom dimensions are sending 'Not Found': "
                    f"{', '.join(not_found)}. Fix the data layer or remove unused dimensions."
                ),
                "impact": "Data quality",
                "effort": "Medium — data layer fix",
            })

    # Third-party vendor actions
    for tag in f["third_party_tags"]:
        if tag.get("action"):
            recs.append({
                "priority": 3,
                "category": "Review",
                "title": f"Review {tag['vendor']} usage",
                "detail": tag["action"],
                "impact": "Potential overhead reduction",
                "effort": "Low — verify with team",
            })

    # Sort by priority
    recs.sort(key=lambda r: r["priority"])


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_markdown(findings: dict) -> str:
    """Generate a markdown report from findings."""
    f = findings
    lines = []

    def add(text=""):
        lines.append(text)

    domain = urlparse(f["url"]).hostname

    add(f"# {domain} Tag & Tracking Audit")
    add()
    add(f"**Site:** {f['url']}  ")
    add(f"**Audit Date:** {f['audit_date']}  ")
    add(f"**Method:** Browser-side inspection (no GTM admin access)  ")
    add(f"**Page Title:** {f['page_title']}  ")
    if f["generator"]:
        add(f"**Platform:** {f['generator']}  ")
    add()
    add("---")
    add()

    # --- Executive Summary ---
    add("## Executive Summary")
    add()
    vendor_count = len(f["third_party_tags"])
    beacon_count = len(f["outbound_beacons"])
    ga4_count = len(f["ga4_streams"])
    gtm_count = len(f["gtm_containers"])
    rec_count = len(f["recommendations"])

    summary_parts = []
    summary_parts.append(
        f"{domain} loads **{vendor_count} third-party analytics/tracking vendors** generating "
        f"**{beacon_count} outbound data beacons** on a single homepage pageview."
    )

    issues = []
    # Check for duplicates
    dups = [s for s in f["ga4_streams"] if s.get("duplicate_of")]
    if dups:
        issues.append("duplicate GA4 streams sending identical data")
    if f["ua_properties"]:
        issues.append("legacy Universal Analytics still configured")
    if f["compliance"]["doubleclick_on_gov"]:
        issues.append("DoubleClick advertising signals firing on a .gov domain")
    if f["cookies"]["orphan_ga"]:
        issues.append("orphan cookie(s) from removed GA4 properties")
    if not f["compliance"]["has_consent_banner"] and f["compliance"]["persistent_cookies"] > 0:
        issues.append("no consent management in place")
    if f["compliance"]["has_session_recording"]:
        vendors = ", ".join(f["compliance"]["session_recording_vendors"])
        issues.append(f"session recording active ({vendors})")

    if issues:
        summary_parts.append("Key issues include " + ", ".join(issues) + ".")

    add(" ".join(summary_parts))
    add()
    add("---")
    add()

    # ===================================================================
    # 1. Complete Tag Inventory
    # ===================================================================
    add("## 1. Complete Tag Inventory")
    add()

    section_num = 1

    # --- 1.x GTM Containers ---
    if f["gtm_containers"]:
        for c in f["gtm_containers"]:
            add(f"### 1.{section_num} Google Tag Manager")
            add()
            add("| Property | Value |")
            add("|----------|-------|")
            add(f"| Container ID | **{c['id']}** |")
            add(f"| Load method | Hardcoded in page HTML (inline `<script>`) |")
            ns = "Present" if c["has_noscript_fallback"] else "**Not present** (minor issue)"
            add(f"| noscript fallback | {ns} |")
            add()
            # Context about GTM
            gtm_streams = [s["id"] for s in f["ga4_streams"] if s["source"] == "GTM"]
            if gtm_streams:
                add(f"{c['id']} is the GTM container. The following GA4 tags fire through it: {', '.join(gtm_streams)}.")
            add()
            add("---")
            add()
            section_num += 1

    # --- 1.x GA4 Streams ---
    if f["ga4_streams"]:
        add(f"### 1.{section_num} Google Analytics 4 — {len(f['ga4_streams'])} Stream{'s' if len(f['ga4_streams']) != 1 else ''}")
        add()
        section_num += 1

        for i, stream in enumerate(f["ga4_streams"], 1):
            # Determine a label
            label = stream["id"]
            if stream["source"].startswith("DAP"):
                label += " (Federal DAP — GSA)"
            elif stream.get("duplicate_of"):
                label += " (duplicate dimensions)"

            add(f"#### Stream {i}: {label}")
            add()
            add("| Property | Value |")
            add("|----------|-------|")
            add(f"| Measurement ID | **{stream['id']}** |")
            add(f"| Loaded via | {stream['source']} |")
            if stream["cookie"]:
                session_info = ""
                if stream["session_count"] is not None:
                    session_info = f" (session count: {stream['session_count']})"
                add(f"| Cookie | `{stream['cookie']}`{session_info} |")
            dc = "Yes" if stream["has_doubleclick"] else "No"
            dc_detail = ""
            if stream["has_doubleclick"]:
                dc_detail = " (`stats.g.doubleclick.net`)"
            add(f"| DoubleClick signal | {'**' + dc + '**' if stream['has_doubleclick'] else dc}{dc_detail} |")
            add()

            # Events
            if stream["events"]:
                add("**Events fired on page load:**")
                add()
                for evt in stream["events"]:
                    add(f"- `{evt}`")
                add()

            # Custom dimensions
            if stream["custom_dimensions"]:
                # Check if identical to another stream
                if stream.get("duplicate_of"):
                    other = next((s for s in f["ga4_streams"] if s["id"] == stream["duplicate_of"]), None)
                    if other and stream["custom_dimensions"] == other["custom_dimensions"]:
                        add(f"**Custom dimensions sent:** Identical to {stream['duplicate_of']} (same parameters, same values).")
                        add()
                        continue

                add("**Custom dimensions sent:**")
                add()
                add("| Parameter | Value |")
                add("|-----------|-------|")
                for k, v in sorted(stream["custom_dimensions"].items()):
                    display_v = v if v != "Not Found" else "**Not Found**"
                    add(f"| `{k}` | {display_v} |")
                add()

        add("---")
        add()

    # --- 1.x Legacy UA ---
    if f["ua_properties"]:
        add(f"### 1.{section_num} Legacy Universal Analytics")
        add()
        for u in f["ua_properties"]:
            add("| Property | Value |")
            add("|----------|-------|")
            add(f"| Property ID | **{u['id']}** |")
            if u.get("source", "Unknown") != "Unknown":
                add(f"| Loaded via | {u['source']} |")
            add(f"| Status | **Deprecated** — UA sunset July 1, 2023 |")
            add()
            add(f"Google stopped processing UA hits in July 2023, so any data sent to this property is discarded. "
                f"The UA configuration should be removed.")
        add()
        add("---")
        add()
        section_num += 1

    # --- 1.x Per-vendor sections ---
    # Skip vendors already covered above (GTM, GA, DoubleClick handled with GA4)
    covered_vendors = {"Google Tag Manager", "Google Analytics"}
    for tag in f["third_party_tags"]:
        if tag["vendor"] in covered_vendors:
            continue

        add(f"### 1.{section_num} {tag['vendor']}")
        add()

        add("| Property | Value |")
        add("|----------|-------|")

        if tag["vendor_id"]:
            label, vid = tag["vendor_id"]
            add(f"| {label} | **{vid}** |")

        if tag["script_urls"]:
            script_display = tag["script_urls"][0].split("?")[0]
            add(f"| Script URL | `{script_display}` |")

        # Load method
        if tag["vendor"] == "Federal DAP (GSA)":
            add("| Load method | Hardcoded script (not via GTM) |")
        elif f["gtm_containers"]:
            add(f"| Load method | Via GTM or hardcoded |")

        add(f"| Beacon domain | `{tag['domain']}` |")
        add()

        # Description
        if tag["description"]:
            add(tag["description"])
            add()

        # Compliance note
        if tag["compliance_note"]:
            add(f"**Compliance note:** {tag['compliance_note']}")
            add()

        # Action
        if tag["action"]:
            add(f"**Action:** {tag['action']}")
            add()

        # DAP parameters inline
        if tag["vendor"] == "Federal DAP (GSA)" and f.get("dap_params"):
            add("**DAP script parameters:**")
            add()
            add("| Parameter | Value |")
            add("|-----------|-------|")
            for k, v in sorted(f["dap_params"].items()):
                add(f"| `{k}` | {v[0]} |")
            add()

        add("---")
        add()
        section_num += 1
        covered_vendors.add(tag["vendor"])

    # --- DoubleClick section (if present) ---
    dc_streams = f["compliance"].get("doubleclick_streams", [])
    non_dc_streams = f["compliance"].get("non_doubleclick_streams", [])
    if dc_streams:
        dc_tag = next((t for t in f["third_party_tags"] if t["vendor"] == "DoubleClick (Google Ads)"), None)
        if dc_tag and "DoubleClick (Google Ads)" not in covered_vendors:
            add(f"### 1.{section_num} DoubleClick (Google Ads Signals)")
            add()
            add("| Property | Value |")
            add("|----------|-------|")
            if dc_tag["script_urls"]:
                add(f"| Endpoint | `{dc_tag['script_urls'][0].split('?')[0]}` |")
            else:
                add(f"| Endpoint | `stats.g.doubleclick.net/g/collect` |")
            add(f"| Triggered by | {', '.join(dc_streams)} |")
            if non_dc_streams:
                add(f"| Not triggered by | {', '.join(non_dc_streams)} |")
            add()
            add("**This is a significant finding.** DoubleClick collects data for Google Ads "
                "audience building and remarketing. This fires because **Google Signals is enabled** "
                "in the GA4 property settings for the streams listed above.")
            add()
            if f["compliance"]["is_gov"]:
                add("Government sites should not be enabling advertising features. This should be "
                    "**disabled immediately** in the GA4 property settings "
                    "(Admin > Data Settings > Data Collection > Google Signals).")
                add()
            add("---")
            add()
            section_num += 1
            covered_vendors.add("DoubleClick (Google Ads)")

    # ===================================================================
    # 2. Cookie Inventory
    # ===================================================================
    add("## 2. Cookie Inventory")
    add()
    mapped_cookies = f["cookies"]["mapped"]
    if mapped_cookies:
        add("| Cookie | Purpose | Status |")
        add("|--------|---------|--------|")
        for c in mapped_cookies:
            add(f"| `{c['name']}` | {c['purpose']} | {c['status']} |")
        add()
    else:
        add("No analytics cookies detected.")
        add()

    # Orphan cookie details
    if f["cookies"]["orphan_ga"]:
        for orphan in f["cookies"]["orphan_ga"]:
            add(f"### Orphan Cookie: `_ga_{orphan['suffix']}`")
            add()
            session_info = ""
            if orphan.get("session_count") is not None:
                session_info = f" (session count: {orphan['session_count']})"
            add(f"This cookie belongs to a GA4 property with measurement ID suffix "
                f"`{orphan['suffix']}` that is **no longer loading on the site**{session_info}. "
                f"This is a remnant of a previously removed GA4 stream. The cookie will "
                f"eventually expire, but it indicates a past GA4 property was removed without cleanup.")
            add()

    add("---")
    add()

    # ===================================================================
    # 3. Compliance & Privacy Concerns
    # ===================================================================
    add("## 3. Compliance & Privacy Concerns")
    add()
    comp = f["compliance"]

    # 3.1 Consent
    add("### 3.1 Consent / Opt-Out Mechanism")
    add()
    if comp["has_consent_banner"]:
        add("A consent banner or opt-out mechanism was detected on the page.")
    else:
        add(f"The site has **no cookie consent mechanism detected**.")
        if comp["is_gov"]:
            add()
            add("While U.S. federal sites are generally exempt from GDPR (serving U.S. persons), "
                "OMB guidance (M-10-22) categorizes web measurement technologies into tiers:")
            add("- **Tier 1** (single session): Allowed by default")
            add("- **Tier 2** (multi-session/persistent): Requires opt-out capability")
            add()
            add(f"GA4 cookies (`_ga`, `_ga_*`) are **Tier 2** persistent cookies (2-year expiry). "
                f"An opt-out mechanism may be required.")
        elif comp["persistent_cookies"] > 0:
            add()
            add(f"The site sets **{comp['persistent_cookies']} persistent analytics cookies** "
                f"with no visible opt-out mechanism.")
    add()

    # 3.2 DoubleClick
    if dc_streams:
        add("### 3.2 Google Signals / DoubleClick")
        add()
        add(f"DoubleClick beacons are firing from: {', '.join(dc_streams)}. "
            f"This enables Google to:")
        add("- Build advertising audience profiles from site visitors")
        add("- Link GA4 data with Google Ads data")
        add()
        if comp["is_gov"]:
            add("This is almost certainly unintentional on a government site and should be disabled.")
        else:
            add("Verify this is intentional. If not, disable Google Signals in GA4 Admin > Data Settings > Data Collection.")
        add()

    # 3.3 Session recording
    if comp["has_session_recording"]:
        vendors = ", ".join(comp["session_recording_vendors"])
        add(f"### 3.{'3' if dc_streams else '2'} Session Recording ({vendors})")
        add()
        add(f"Session recording captures mouse movements, clicks, scrolls, and form interactions. "
            f"Visitors may interact with sensitive content. A privacy impact assessment should "
            f"confirm this is acceptable.")
        add()

    # 3.x Third-party domains
    domains = comp["third_party_domains"]
    sub = 4 if dc_streams and comp["has_session_recording"] else (3 if dc_streams or comp["has_session_recording"] else 2)
    add(f"### 3.{sub} Data Sent to {len(domains)} Third-Party Domains")
    add()
    add("The page communicates with these external domains:")
    add()
    for i, d in enumerate(domains, 1):
        vendor = identify_vendor(d)
        label = f" ({vendor})" if vendor else ""
        add(f"{i}. `{d}`{label}")
    add()
    add("---")
    add()

    # ===================================================================
    # 4. Performance Impact
    # ===================================================================
    add("## 4. Performance Impact")
    add()
    perf = f["performance"]
    add("| Metric | Value |")
    add("|--------|-------|")
    if perf.get("fcp"):
        add(f"| First Contentful Paint | {perf['fcp']:,}ms |")
    if perf.get("domComplete"):
        add(f"| DOM Complete | {perf['domComplete']:,}ms |")
    if perf.get("loadComplete"):
        add(f"| Load Complete | {perf['loadComplete']:,}ms |")
    add(f"| Third-party domains | {len(domains)} |")
    add(f"| Outbound beacons | {len(f['outbound_beacons'])} per pageview |")
    add()

    # Beacon breakdown
    if f["outbound_beacons"]:
        add("### Outbound beacons per pageview:")
        add()
        for b in f["outbound_beacons"]:
            vendor_label = f" ({b['vendor']})" if b.get("vendor") else ""
            add(f"- `{b['url']}`{vendor_label} [{b['method']}]")
        add()

        # Impact note for duplicates
        dups = [s for s in f["ga4_streams"] if s.get("duplicate_of")]
        if dups:
            current = len(f["outbound_beacons"])
            savings = len(set(s["id"] for s in dups)) // 2  # approximate
            dc_savings = sum(1 for s in dups if s["has_doubleclick"]) // 2
            reduced = current - savings - dc_savings
            add(f"**If duplicate GA4 streams are removed, beacons drop to ~{reduced}.** "
                f"If DoubleClick is also disabled, it drops further.")
            add()

    add("---")
    add()

    # ===================================================================
    # 5. Recommendations
    # ===================================================================
    add("## 5. Recommendations")
    add()

    if f["recommendations"]:
        # Group by priority
        by_priority = {}
        for r in f["recommendations"]:
            by_priority.setdefault(r["priority"], []).append(r)

        priority_labels = {
            1: "Priority 1 — Immediate (Security/Compliance)",
            2: "Priority 2 — Cleanup (Redundancy/Hygiene)",
            3: "Priority 3 — Optimization",
        }

        rec_num = 0
        for priority in sorted(by_priority.keys()):
            add(f"### {priority_labels.get(priority, f'Priority {priority}')}")
            add()
            add("| # | Action | Impact | Effort |")
            add("|---|--------|--------|--------|")
            for r in by_priority[priority]:
                rec_num += 1
                add(f"| {priority}.{rec_num} | **{r['title']}** — {r['detail']} | "
                    f"{r.get('impact', '')} | {r.get('effort', '')} |")
            add()
            rec_num = 0
    else:
        add("No issues found.")
        add()

    # GTM access items
    if f.get("gtm_access_items"):
        add("### Requires GTM Access to Fully Assess")
        add()
        add("| # | Action |")
        add("|---|--------|")
        for i, item in enumerate(f["gtm_access_items"], 1):
            add(f"| {i} | {item} |")
        add()

    add("---")
    add()

    # ===================================================================
    # 6. Data Flow Diagram
    # ===================================================================
    add("## 6. Data Flow Diagram")
    add()
    add("```")
    add(f"  {domain} Page Load")
    add(f"  {'|':>20}")

    # Build tree branches
    branches = []

    # GTM branch
    for c in f["gtm_containers"]:
        gtm_children = []
        for s in f["ga4_streams"]:
            if s["source"] == "GTM":
                dc_note = " → doubleclick.net" if s["has_doubleclick"] else ""
                gtm_children.append(f"{s['id']} (GA4) → analytics.google.com{dc_note}")
        for t in f["third_party_tags"]:
            if t["vendor"] not in {"Google Tag Manager", "Google Analytics", "DoubleClick (Google Ads)",
                                    "Federal DAP (GSA)"}:
                vid = ""
                if t["vendor_id"]:
                    vid = f" ({t['vendor_id'][1]})"
                gtm_children.append(f"{t['vendor']}{vid} → {t['domain']}")
        branches.append((f"[{c['id']}] (GTM)", gtm_children))

    # DAP branch
    if f.get("has_dap"):
        dap_children = []
        for s in f["ga4_streams"]:
            if s["source"].startswith("DAP"):
                dap_children.append(f"{s['id']} (GA4/DAP) → google-analytics.com")
        for u in f["ua_properties"]:
            dap_children.append(f"{u['id']} (DEPRECATED)")
        branches.append(("[DAP Script] (hardcoded)", dap_children))

    # Other hardcoded vendors
    for t in f["third_party_tags"]:
        if t["vendor"] in {"Google Tag Manager", "Google Analytics", "DoubleClick (Google Ads)",
                            "Federal DAP (GSA)"}:
            continue
        # Already in GTM branch — skip (simplified)

    # Render tree
    for bi, (branch_label, children) in enumerate(branches):
        is_last_branch = bi == len(branches) - 1
        prefix = "  └── " if is_last_branch else "  ├── "
        add(f"{prefix}{branch_label}")
        for ci, child in enumerate(children):
            is_last_child = ci == len(children) - 1
            child_prefix = "      " if is_last_branch else "  │   "
            connector = "└── " if is_last_child else "├── "
            add(f"{child_prefix}{connector}{child}")

    add("```")
    add()
    add("---")
    add()

    # ===================================================================
    # 7. How to Verify
    # ===================================================================
    add("## Appendix: How to Verify These Findings")
    add()
    add("Anyone can reproduce this audit:")
    add()
    add(f"1. Open {f['url']} in Chrome")
    add("2. Open DevTools (F12) > Network tab")
    add("3. Reload the page")
    add('4. Filter by "collect" to see all analytics beacons')
    add("5. In Console, type `dataLayer` to see the GTM data layer")
    add("6. In Console, type `document.cookie` to see analytics cookies")
    add("7. Install [Google Tag Assistant](https://tagassistant.google.com/) for a visual tag breakdown")
    add()
    add("---")
    add()
    add("*Audit performed via browser-side inspection using Playwright. Findings represent the state of the "
        "live production site at the time of audit. GTM container contents (paused tags, draft versions, "
        "user permissions) require admin access to review.*")

    return "\n".join(lines)


def generate_pdf(markdown_text: str, output_path: str):
    """Convert markdown report to PDF."""
    try:
        import markdown as md_lib
        from weasyprint import HTML
    except ImportError:
        print("  PDF generation requires 'markdown' and 'weasyprint' packages.")
        print("  Install with: pip install markdown weasyprint")
        return False

    html_body = md_lib.markdown(markdown_text, extensions=["tables", "fenced_code"])

    html_full = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  @page {{ size: letter; margin: 0.75in; }}
  body {{ font-family: system-ui, -apple-system, Helvetica, Arial, sans-serif;
         font-size: 11pt; line-height: 1.5; color: #1a1a1a; }}
  h1 {{ font-size: 22pt; border-bottom: 3px solid #1a1a1a; padding-bottom: 6px; margin-top: 0; }}
  h2 {{ font-size: 16pt; border-bottom: 1px solid #999; padding-bottom: 4px;
        margin-top: 24px; page-break-after: avoid; }}
  h3 {{ font-size: 13pt; margin-top: 18px; page-break-after: avoid; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0;
           font-size: 10pt; page-break-inside: avoid; }}
  th, td {{ border: 1px solid #bbb; padding: 5px 8px; text-align: left; }}
  th {{ background: #e8e8e8; font-weight: 600; }}
  tr:nth-child(even) {{ background: #f5f5f5; }}
  code {{ background: #eee; padding: 1px 3px; border-radius: 2px;
          font-size: 9.5pt; font-family: Menlo, Monaco, monospace; }}
  pre {{ background: #f3f3f3; padding: 10px; border-radius: 4px;
         font-size: 9pt; overflow-x: auto; white-space: pre-wrap;
         font-family: Menlo, Monaco, monospace; }}
  strong {{ color: #b00; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 16px 0; }}
  p {{ margin: 6px 0; }}
  ul, ol {{ margin: 6px 0; padding-left: 24px; }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    HTML(string=html_full).write_pdf(output_path)
    return True


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def print_summary(findings: dict):
    """Print a concise summary to the terminal."""
    f = findings
    domain = urlparse(f["url"]).hostname

    print()
    print(f"  {'=' * 60}")
    print(f"  TAG AUDIT: {domain}")
    print(f"  {'=' * 60}")
    print()

    # GTM
    if f["gtm_containers"]:
        print("  GTM Containers:")
        for c in f["gtm_containers"]:
            ns = "yes" if c["has_noscript_fallback"] else "NO"
            print(f"    - {c['id']}  (noscript: {ns})")
        print()

    # GA4
    if f["ga4_streams"]:
        print("  GA4 Streams:")
        for s in f["ga4_streams"]:
            dc = " [DOUBLECLICK]" if s["has_doubleclick"] else ""
            print(f"    - {s['id']}  via {s['source']}{dc}")
        print()

    # UA
    if f["ua_properties"]:
        print("  Legacy UA (DEPRECATED):")
        for u in f["ua_properties"]:
            print(f"    - {u['id']}")
        print()

    # Third-party
    if f["third_party_tags"]:
        print("  Third-Party Tags:")
        for t in f["third_party_tags"]:
            print(f"    - {t['vendor']}  ({t['domain']})")
        print()

    # Beacons
    print(f"  Outbound Beacons: {len(f['outbound_beacons'])} per pageview")
    print(f"  Third-Party Domains: {len(f['compliance']['third_party_domains'])}")
    print(f"  Analytics Cookies: {len(f['cookies']['analytics'])}")
    if f["cookies"]["orphan_ga"]:
        print(f"  Orphan Cookies: {len(f['cookies']['orphan_ga'])}")
    print(f"  Consent Banner: {'Yes' if f['compliance']['has_consent_banner'] else 'NOT DETECTED'}")
    print()

    # Recommendations
    if f["recommendations"]:
        print("  Recommendations:")
        for r in f["recommendations"]:
            p_label = {1: "HIGH", 2: "MED", 3: "LOW"}.get(r["priority"], "?")
            print(f"    [{p_label}] {r['title']}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Audit a website for GTM containers, GA4/UA properties, and third-party tracking tags."
    )
    parser.add_argument("url", help="URL to audit (e.g. https://www.example.com)")
    parser.add_argument("--output", "-o", help="Output file path for markdown report (default: <domain>-tag-audit.md)")
    parser.add_argument("--pdf", action="store_true", help="Also generate a PDF report")
    parser.add_argument("--json", action="store_true", help="Also output raw findings as JSON")
    parser.add_argument("--timeout", type=int, default=30000, help="Page load timeout in ms (default: 30000)")
    parser.add_argument("--headless", action="store_true", help="Use headless browser (may be blocked by some sites)")

    args = parser.parse_args()

    # Normalize URL
    url = args.url
    if not url.startswith("http"):
        url = "https://" + url

    domain = urlparse(url).hostname
    base_name = args.output or f"{domain}-tag-audit.md"
    base_path = Path(base_name)

    print()
    print(f"  GTM & Tag Audit")
    print(f"  Target: {url}")
    print(f"  {'─' * 40}")

    # Collect
    data = collect_data(url, timeout_ms=args.timeout, headless=args.headless)

    # Analyze
    print("  Analyzing ...")
    findings = analyze(data)

    # Terminal summary
    print_summary(findings)

    # Markdown report
    md_report = generate_markdown(findings)
    base_path.write_text(md_report)
    print(f"  Markdown report: {base_path}")

    # PDF
    if args.pdf:
        pdf_path = base_path.with_suffix(".pdf")
        print(f"  Generating PDF ...")
        if generate_pdf(md_report, str(pdf_path)):
            print(f"  PDF report: {pdf_path}")

    # JSON
    if args.json:
        json_path = base_path.with_suffix(".json")
        json_path.write_text(json.dumps(findings, indent=2, default=str))
        print(f"  JSON data: {json_path}")

    print()
    print("  Done.")
    print()


if __name__ == "__main__":
    main()

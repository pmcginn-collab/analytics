#!/usr/bin/env python3
"""
HHS.gov Press Room Analytics Report
With Traffic Source Data and Enhanced Visuals via ReportLab
"""

import csv
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, Image
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.graphics.shapes import Drawing, Rect, String, Line
from reportlab.graphics.charts.barcharts import HorizontalBarChart
from reportlab.graphics.charts.piecharts import Pie
from reportlab.graphics.charts.legends import Legend
from reportlab.pdfbase.pdfmetrics import stringWidth

# HHS Logo path (relative to this script's directory)
import os as _os
HHS_LOGO_PATH = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), 'hhs_logo.png')

# HHS Brand Colors
HHS_DARKEST = colors.HexColor('#11181D')
HHS_DARK_NAVY = colors.HexColor('#162E51')
HHS_MEDIUM_BLUE = colors.HexColor('#1A4480')
HHS_LIGHT_BLUE = colors.HexColor('#2E6DB4')
HHS_ACCENT = colors.HexColor('#3A7CA5')
HHS_TEAL = colors.HexColor('#2E8B8B')
HHS_ORANGE = colors.HexColor('#E07C3E')
HHS_GREEN = colors.HexColor('#4A7C59')

# Channel colors for traffic sources
CHANNEL_COLORS = {
    'Organic Search': HHS_MEDIUM_BLUE,
    'Direct': HHS_LIGHT_BLUE,
    'Referral': HHS_TEAL,
    'Organic Social': HHS_ORANGE,
    'Other': HHS_GREEN,
}

# Known 404 URLs to exclude (pages removed after being tracked in GA4)
KNOWN_404_PATHS = {
    '/press-room/hhs-publishes-whistleblower-form-to-protect-kids.html',
}


def is_english_title(title):
    """Check if a title appears to be English (not CJK or Cyrillic)."""
    if not title:
        return False
    first_char = ord(title[0])
    # CJK ranges: Chinese, Japanese Hiragana/Katakana, Korean, Cyrillic
    if (0x4E00 <= first_char <= 0x9FFF or  # Chinese
        0x3040 <= first_char <= 0x30FF or  # Japanese
        0xAC00 <= first_char <= 0xD7AF or  # Korean
        0x0400 <= first_char <= 0x04FF):   # Cyrillic
        return False
    return True


def parse_traffic_csv(filename):
    """Parse the CSV file with traffic source data and titles."""
    metadata = {}
    # Use dict to deduplicate by page path
    data_by_path = {}
    # Grand total row values (deduplicated by GA4)
    grand_total = {'total_users': 0, 'total_views': 0}

    with open(filename, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        lines = list(reader)

    # Parse metadata from header comments
    for row in lines[:6]:
        if row and row[0].startswith('#'):
            line = row[0]
            if 'HHS.gov' in line:
                metadata['property'] = 'HHS.gov - Main - 360 Umbrella'

    # Find the date range line
    for row in lines:
        if row and row[0].startswith('# ') and '-' in row[0] and len(row[0]) < 30:
            date_str = row[0].replace('# ', '')
            if date_str.replace('-', '').isdigit():
                start = date_str[:8]
                end = date_str[9:]
                metadata['start_date'] = datetime.strptime(start, '%Y%m%d').strftime('%B %d, %Y')
                metadata['end_date'] = datetime.strptime(end, '%Y%m%d').strftime('%B %d, %Y')

    # Default dates if not found
    if 'start_date' not in metadata:
        metadata['start_date'] = 'January 01, 2025'
        metadata['end_date'] = 'December 15, 2025'

    # Find channel group header row and auto-detect column mapping
    channel_header_idx = None
    for i, row in enumerate(lines):
        if row and len(row) > 1 and 'Session default channel group' in ','.join(row):
            channel_header_idx = i
            break

    # Build column mapping from channel group header
    # Each channel appears twice (users col, views col)
    channel_cols = {}
    if channel_header_idx is not None:
        ch_row = lines[channel_header_idx]
        for col_idx in range(2, len(ch_row)):
            name = ch_row[col_idx].strip()
            if not name:
                continue
            if name not in channel_cols:
                channel_cols[name] = [col_idx]
            else:
                channel_cols[name].append(col_idx)

    # Map GA4 channel names to internal field names
    CHANNEL_FIELD_MAP = {
        'Organic Search': 'organic',
        'Direct': 'direct',
        'Referral': 'referral',
        'Organic Social': 'social',
        'Unassigned': 'email',
        'Email': 'email',
        'Totals': 'total',
    }

    def get_col(channel_name, idx):
        """Get column index for a channel. idx=0 for users, idx=1 for views."""
        if channel_name in channel_cols and idx < len(channel_cols[channel_name]):
            return channel_cols[channel_name][idx]
        return None

    # Build field-to-column mapping
    field_cols = {}
    for ga4_name, field_name in CHANNEL_FIELD_MAP.items():
        users_col = get_col(ga4_name, 0)
        views_col = get_col(ga4_name, 1)
        if users_col is not None:
            field_cols[f'{field_name}_users'] = users_col
            field_cols[f'{field_name}_views'] = views_col

    # Find header row (the row after channel groups with "Page path")
    header_idx = channel_header_idx + 1 if channel_header_idx is not None else None
    if header_idx is None:
        for i, row in enumerate(lines):
            if row and 'Page path' in ','.join(row):
                header_idx = i
                break
    if header_idx is None:
        for i, row in enumerate(lines):
            if len(row) >= 14 and 'Active users' in ','.join(row):
                header_idx = i
                break

    # Parse data rows
    for row in lines[header_idx + 1:]:
        if not row or len(row) < 14:
            continue

        title = row[0]
        page_path = row[1]

        # Capture grand total row, then skip it from press release data
        if 'Grand total' in str(row):
            # Extract grand total users and views from Totals columns
            total_users_col = field_cols.get('total_users')
            total_views_col = field_cols.get('total_views')
            if total_users_col is not None and total_users_col < len(row) and row[total_users_col]:
                grand_total['total_users'] = int(row[total_users_col])
            if total_views_col is not None and total_views_col < len(row) and row[total_views_col]:
                grand_total['total_views'] = int(row[total_views_col])
            continue

        # Skip empty rows
        if not page_path:
            continue

        # Skip if it's not a press-room page
        if not page_path.startswith('/press-room/'):
            continue
    
        # Skip known 404 URLs (pages removed after being tracked)
        if page_path in KNOWN_404_PATHS:
            continue

        # Skip non-English titles
        if not is_english_title(title):
            continue

        try:

            def safe_int(row, col):
                if col is not None and col < len(row) and row[col]:
                    return int(row[col])
                return 0

            traffic_data = {

                'organic_users': safe_int(row, field_cols.get('organic_users')),
                'organic_views': safe_int(row, field_cols.get('organic_views')),
                'direct_users': safe_int(row, field_cols.get('direct_users')),
                'direct_views': safe_int(row, field_cols.get('direct_views')),
                'social_users': safe_int(row, field_cols.get('social_users')),
                'social_views': safe_int(row, field_cols.get('social_views')),
                'referral_users': safe_int(row, field_cols.get('referral_users')),
                'referral_views': safe_int(row, field_cols.get('referral_views')),
                'email_users': safe_int(row, field_cols.get('email_users')),
                'email_views': safe_int(row, field_cols.get('email_views')),
                'total_users': safe_int(row, field_cols.get('total_users')),
                'total_views': safe_int(row, field_cols.get('total_views')),
            }

            clean_title = title.replace(' | HHS.gov', '')

            # Deduplicate by page path - aggregate traffic data
            if page_path in data_by_path:
                # Add traffic to existing entry (same path, different title variant)
                existing = data_by_path[page_path]
                for key in traffic_data:
                    if key in ['page_date_created', 'first_visit_date']:
                        # For dates, keep the best one (prefer page_date_created if not "Not Found")
                        if key == 'page_date_created':
                            if traffic_data[key] and traffic_data[key].strip().lower() != 'not found':
                                existing[key] = traffic_data[key]
                            elif not existing[key] or existing[key].strip().lower() == 'not found':
                                existing[key] = traffic_data[key]
                        elif key == 'first_visit_date':
                            # Keep first_visit_date if we don't have a good page_date_created
                            if not existing.get('page_date_created') or existing.get('page_date_created', '').strip().lower() == 'not found':
                                if traffic_data[key]:
                                    existing[key] = traffic_data[key]
                    else:
                        existing[key] += traffic_data[key]
            else:
                data_by_path[page_path] = {
                    'page': page_path,
                    'title': clean_title,
                    **traffic_data
                }

        except (ValueError, IndexError) as e:
            continue

    return metadata, list(data_by_path.values()), grand_total


def generate_title_from_url(url):
    """Generate a readable title from the URL path."""
    # Extract filename without extension
    filename = url.split('/')[-1].replace('.html', '')

    # Special cases
    if filename == 'index':
        return 'Press Room Landing Page'

    # Replace hyphens with spaces and title case
    title = filename.replace('-', ' ')

    # Capitalize appropriately
    words = title.split()
    capitalized = []
    lowercase_words = {'a', 'an', 'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
    acronyms = {'hhs', 'fda', 'cdc', 'cms', 'nih', 'acip', 'hipaa', 'maha', 'doge', 'usa', 'us', 'covid', 'mrna', 'ai'}

    for i, word in enumerate(words):
        if word.lower() in acronyms:
            capitalized.append(word.upper())
        elif i == 0 or word.lower() not in lowercase_words:
            capitalized.append(word.capitalize())
        else:
            capitalized.append(word.lower())

    return ' '.join(capitalized)


def format_number(num):
    """Format large numbers with commas."""
    return "{:,}".format(int(num))


def format_compact(num):
    """Format numbers compactly (e.g., 1.2M, 500K)."""
    if num >= 1000000:
        return f"{num/1000000:.1f}M"
    elif num >= 1000:
        return f"{num/1000:.0f}K"
    else:
        return str(int(num))


def parse_and_format_date(page_date_created, first_visit_date):
    """Parse date from CSV and return formatted date string (MM/DD/YYYY) and date object for comparison.
    Uses page_date_created if available, falls back to first_visit_date if page_date_created is 'Not Found'.
    Returns (formatted_date_string, date_object) or (None, None) if no valid date found.
    """
    
    date_str = None
    date_obj = None
    
    # Try page_date_created first
    if page_date_created and page_date_created.strip() and page_date_created.strip().lower() != 'not found':
        date_str = page_date_created.strip()
    # Fall back to first_visit_date
    elif first_visit_date and first_visit_date.strip():
        date_str = first_visit_date.strip()
    
    if not date_str:
        return None, None
    
    # Try parsing ISO format (2026-01-05T14:00:00-0500)
    try:
        if 'T' in date_str:
            date_obj = datetime.strptime(date_str.split('T')[0], '%Y-%m-%d')
        # Try YYYYMMDD format (20260106)
        elif len(date_str) == 8 and date_str.isdigit():
            date_obj = datetime.strptime(date_str, '%Y%m%d')
        # Try MM/DD/YYYY format
        elif '/' in date_str:
            date_obj = datetime.strptime(date_str, '%m/%d/%Y')
        else:
            return None, None
        
        # Format as MM/DD/YYYY
        formatted = date_obj.strftime('%m/%d/%Y')
        return formatted, date_obj
    except (ValueError, AttributeError):
        return None, None


def should_highlight_date(date_obj):
    """Check if date should be highlighted (published in April 2026)."""
    if not date_obj:
        return False
    return date_obj.year == 2026 and date_obj.month == 5


def create_traffic_source_pie(data, exclude_landing=True):
    """Create a pie chart showing traffic source distribution."""
    drawing = Drawing(350, 220)
    drawing.hAlign = 'CENTER'

    # Aggregate traffic sources across all pages
    if exclude_landing:
        filtered_data = [d for d in data if d['page'] != '/press-room/index.html']
    else:
        filtered_data = data

    organic_views = sum(d['organic_views'] for d in filtered_data)
    direct_views = sum(d['direct_views'] for d in filtered_data)
    referral_views = sum(d['referral_views'] for d in filtered_data)
    social_views = sum(d['social_views'] for d in filtered_data)
    email_views = sum(d['email_views'] for d in filtered_data)

    total = organic_views + direct_views + referral_views + social_views + email_views

    if total == 0:
        return drawing

    # Sortable segments
    segments = [
        {
            "label": "Organic Search",
            "value": organic_views,
            "pct": (organic_views / total * 100),
            "color": CHANNEL_COLORS['Organic Search']
        },
        {
            "label": "Direct",
            "value": direct_views,
            "pct": (direct_views / total * 100),
            "color": CHANNEL_COLORS['Direct']
        },
        {
            "label": "Referral",
            "value": referral_views,
            "pct": (referral_views / total * 100),
            "color": CHANNEL_COLORS['Referral']
        },
        {
            "label": "Organic Social",
            "value": social_views,
            "pct": (social_views / total * 100),
            "color": CHANNEL_COLORS['Organic Social']
        },
        {
            "label": "Other",
            "value": email_views,
            "pct": (email_views / total * 100),
            "color": CHANNEL_COLORS['Other']
        }
    ]

    # Sort descending by percentage
    segments.sort(key=lambda x: x["pct"], reverse=True)
    
    
    # Title
    drawing.add(
        String(
            175,
            225,
            'Traffic Sources (Press Releases)',
            fontSize=11,
            fontName='Helvetica-Bold',
            fillColor=HHS_DARK_NAVY,
            textAnchor='middle'
        )
    )

    pie = Pie()
    pie.x = 70
    pie.y = 30
    pie.width = 140
    pie.height = 140

    pie.data = [s["value"] for s in segments]

    pie.labels = [
        f'{s["pct"]:.0f}%' if s["pct"] > 5 else ''
        for s in segments
    ]

    pie.slices.strokeWidth = 2
    pie.slices.strokeColor = colors.white
    pie.slices.fontName = 'Helvetica-Bold'
    pie.slices.fontSize = 12
    pie.slices.fontColor = colors.white
    pie.sideLabels = False
    pie.slices.labelRadius = 0.65

    # Apply sorted colors
    for i, s in enumerate(segments):
        pie.slices[i].fillColor = s["color"]

    drawing.add(pie)

    # Manual legend matching previous charts
    legend_x = 230
    legend_y = 175
    line_height = 18
    square_size = 10

    for i, s in enumerate(segments):
        y = legend_y - (i * line_height)

        # Color square
        drawing.add(
            Rect(
                legend_x,
                y - square_size,
                square_size,
                square_size,
                fillColor=s["color"],
                strokeColor=s["color"]
            )
        )

        # Legend text aligned to square bottom
        drawing.add(
            String(
                legend_x + 15,
                y - 8,
                f'{s["label"]} ({s["pct"]:.1f}%)',
                fontName='Helvetica',
                fontSize=10
            )
        )
    
    return drawing


def create_views_distribution_pie(press_releases, landing_page, grand_total):
    """Create a pie chart showing views distribution: Landing Page vs Top 10 vs Rest."""
    
    drawing = Drawing(350, 220)
    drawing.hAlign = 'CENTER'

    top_10_views = sum(pr['total_views'] for pr in press_releases[:10])
    rest_views = sum(pr['total_views'] for pr in press_releases[10:])
    landing_views = landing_page['total_views'] if landing_page else 0
    total_views = grand_total['total_views']

    known_views = top_10_views + rest_views + landing_views
    other_views = max(total_views - known_views, 0)

    total = known_views + other_views

    if total == 0:
        return drawing

    # Build sortable dataset
    segments = [
        {
            "label": "Landing Page",
            "value": landing_views,
            "color": HHS_DARK_NAVY,
        },
        {
            "label": "Top 10 Releases",
            "value": top_10_views,
            "color": HHS_ORANGE,
        },
        {
            "label": "Other Releases",
            "value": rest_views,
            "color": HHS_TEAL,
        },
        {
            "label": "Other Press Room Pages",
            "value": other_views,
            "color": HHS_GREEN,
        },
    ]

    # Add percentages
    for s in segments:
        s["pct"] = (s["value"] / total) * 100 if total else 0

    # Sort descending by percentage/value
    segments.sort(key=lambda x: x["pct"], reverse=True)

    pie = Pie()
    pie.x = 70
    pie.y = 30
    pie.width = 140
    pie.height = 140

    # Apply sorted data
    pie.data = [s["value"] for s in segments]

    # Pie slice labels
    pie.labels = [
        f'{s["pct"]:.0f}%' if s["pct"] > 5 else ''
        for s in segments
    ]

    pie.slices.strokeWidth = 2
    pie.slices.strokeColor = colors.white
    pie.slices.fontName = 'Helvetica-Bold'
    pie.slices.fontSize = 12
    pie.slices.fontColor = colors.white
    pie.sideLabels = False
    pie.slices.labelRadius = 0.65

    # Apply sorted colors
    for i, s in enumerate(segments):
        pie.slices[i].fillColor = s["color"]

    drawing.add(pie)

    # Legend with larger font
    legend = Legend()
    legend.x = 230
    legend.y = 130
    legend.dx = 10
    legend.dy = 10
    legend.fontName = 'Helvetica'
    legend.fontSize = 10
    legend.boxAnchor = 'nw'
    legend.columnMaximum = 2
    legend.strokeWidth = 0.5
    legend.strokeColor = colors.HexColor('#e2e8f0')
    legend.deltax = 10
    legend.deltay = 14
    legend.autoXPadding = 5
    legend.dxTextSpace = 5
    legend.alignment = 'right'

    # OPTIONAL: Add sorted legend manually
    legend_x = 230
    legend_y = 130
    line_height = 14
    

    for i, s in enumerate(segments):
        y = legend_y - (i * line_height)

        # Color square
        drawing.add(
            Rect(
                legend_x,
                y - 8,
                10,
                10,
                fillColor=s["color"],
                strokeColor=s["color"]
            )
        )

        # Legend text
        drawing.add(
            String(
                legend_x + 15,
                y - 6,
                f'{s["label"]} ({s["pct"]:.1f}%)',
                fontName='Helvetica',
                fontSize=10
    )
)

    drawing.add(String(175, 200, 'Total Views: Landing Page vs Press Releases',
                       fontSize=11, fontName='Helvetica-Bold',
                       fillColor=HHS_DARK_NAVY, textAnchor='middle'))
    return drawing


def create_press_releases_distribution_pie(press_releases):
    """Create a pie chart showing views distribution: Top 10 vs Rest of press releases."""
    drawing = Drawing(350, 220)
    drawing.hAlign = 'CENTER'

    top_10_views = sum(pr['total_views'] for pr in press_releases[:10])
    rest_views = sum(pr['total_views'] for pr in press_releases[10:])

    total = top_10_views + rest_views

    if total == 0:
        return drawing

    top_10_pct = top_10_views / total * 100 if total > 0 else 0
    rest_pct = rest_views / total * 100 if total > 0 else 0
    rest_count = len(press_releases) - 10 if len(press_releases) > 10 else 0

    # Sortable segments
    segments = [
        {
            "label": "Top 10",
            "value": top_10_views,
            "pct": top_10_pct,
            "color": HHS_ORANGE,
            "views_text": format_compact(top_10_views)
        },
        {
            "label": f"Other {rest_count}",
            "value": rest_views,
            "pct": rest_pct,
            "color": HHS_TEAL,
            "views_text": format_compact(rest_views)
        }
    ]

    # Sort descending
    segments.sort(key=lambda x: x["pct"], reverse=True)

    # Title
    drawing.add(
        String(
            175,
            225,
            'Press Releases Views Distribution',
            fontSize=11,
            fontName='Helvetica-Bold',
            fillColor=HHS_DARK_NAVY,
            textAnchor='middle'
        )
    )

    pie = Pie()
    pie.x = 70
    pie.y = 30
    pie.width = 140
    pie.height = 140

    pie.data = [s["value"] for s in segments]

    pie.labels = [
        f'{s["pct"]:.0f}%'
        for s in segments
    ]
    
    pie = Pie()
    pie.x = 70
    pie.y = 30
    pie.width = 140
    pie.height = 140
    pie.data = [top_10_views, rest_views]
    pie.labels = [f'{top_10_pct:.0f}%', f'{rest_pct:.0f}%']
    pie.slices.strokeWidth = 2
    pie.slices.strokeColor = colors.white
    pie.slices.fontName = 'Helvetica-Bold'
    pie.slices.fontSize = 12
    pie.slices.fontColor = colors.white
    pie.sideLabels = False
    pie.slices.labelRadius = 0.65  # Position labels inside the slices
    # More contrasting colors: Orange and Teal
    pie.slices[0].fillColor = HHS_ORANGE
    pie.slices[1].fillColor = HHS_TEAL

    # Apply sorted colors
    for i, s in enumerate(segments):
        pie.slices[i].fillColor = s["color"]
        
    drawing.add(pie)

    # Updated legend
    legend_x = 230
    legend_y = 170
    line_height = 18
    square_size = 10

    for i, s in enumerate(segments):
        y = legend_y - (i * line_height)

        # Color square
        drawing.add(
            Rect(
                legend_x,
                y - square_size,
                square_size,
                square_size,
                fillColor=s["color"],
                strokeColor=s["color"]
            )
        )

        # Legend text aligned to square bottom
        drawing.add(
            String(
                legend_x + 15,
                y - 8,
                f'{s["label"]} ({s["pct"]:.1f}%)',
                fontName='Helvetica',
                fontSize=10
            )
        )


    return drawing


def create_landing_page_traffic_pie(landing_page):
    """Create a pie chart showing traffic source distribution for the landing page."""
    drawing = Drawing(350, 220)
    drawing.hAlign = 'CENTER'

    if not landing_page:
        return drawing

    organic_views = landing_page.get('organic_views', 0)
    direct_views = landing_page.get('direct_views', 0)
    referral_views = landing_page.get('referral_views', 0)
    social_views = landing_page.get('social_views', 0)
    email_views = landing_page.get('email_views', 0)

    total = organic_views + direct_views + referral_views + social_views + email_views

    if total == 0:
        return drawing

    # Sortable segments
    segments = [
        {
            "label": "Organic Search",
            "value": organic_views,
            "pct": (organic_views / total * 100),
            "color": CHANNEL_COLORS['Organic Search']
        },
        {
            "label": "Direct",
            "value": direct_views,
            "pct": (direct_views / total * 100),
            "color": CHANNEL_COLORS['Direct']
        },
        {
            "label": "Referral",
            "value": referral_views,
            "pct": (referral_views / total * 100),
            "color": CHANNEL_COLORS['Referral']
        },
        {
            "label": "Organic Social",
            "value": social_views,
            "pct": (social_views / total * 100),
            "color": CHANNEL_COLORS['Organic Social']
        },
        {
            "label": "Other",
            "value": email_views,
            "pct": (email_views / total * 100),
            "color": CHANNEL_COLORS['Other']
        }
    ]

    # Sort descending by percentage
    segments.sort(key=lambda x: x["pct"], reverse=True)

    # Title
    drawing.add(
        String(
            175,
            225,
            'Traffic Sources (Landing Page)',
            fontSize=11,
            fontName='Helvetica-Bold',
            fillColor=HHS_DARK_NAVY,
            textAnchor='middle'
        )
    )

    pie = Pie()
    pie.x = 70
    pie.y = 30
    pie.width = 140
    pie.height = 140

    pie.data = [s["value"] for s in segments]

    pie.labels = [
        f'{s["pct"]:.0f}%' if s["pct"] > 5 else ''
        for s in segments
    ]

    pie.slices.strokeWidth = 2
    pie.slices.strokeColor = colors.white
    pie.slices.fontName = 'Helvetica-Bold'
    pie.slices.fontSize = 12
    pie.slices.fontColor = colors.white
    pie.sideLabels = False
    pie.slices.labelRadius = 0.65

    # Apply sorted colors
    for i, s in enumerate(segments):
        pie.slices[i].fillColor = s["color"]

    drawing.add(pie)

    # Manual legend matching previous charts
    legend_x = 230
    legend_y = 175
    line_height = 18
    square_size = 10

    for i, s in enumerate(segments):
        y = legend_y - (i * line_height)

        # Color square
        drawing.add(
            Rect(
                legend_x,
                y - square_size,
                square_size,
                square_size,
                fillColor=s["color"],
                strokeColor=s["color"]
            )
        )

        # Legend text aligned to square bottom
        drawing.add(
            String(
                legend_x + 15,
                y - 8,
                f'{s["label"]} ({s["pct"]:.1f}%)',
                fontName='Helvetica',
                fontSize=10
            )
        )

    return drawing


def create_top_10_bar_chart(press_releases):
    """Create a horizontal bar chart of top 10 press releases with inline titles."""
    drawing = Drawing(540, 300)
    drawing.hAlign = 'CENTER'

    top_10 = press_releases[:10]
    views_data = [pr['total_views'] for pr in reversed(top_10)]

    # Append publish month to title
    titles = []
    for pr in reversed(top_10):

        title = pr['title']
        date_obj = pr.get('formatted_date')

        if date_obj:
            title = f"{title} ({date_obj.strftime('%b')})"

        titles.append(title)

    max_value = max(views_data) * 1.15

    # ── Chart title ──
    drawing.add(String(
        270, 290,
        'Top 10 Press Releases by Views',
        fontSize=11,
        fontName='Helvetica-Bold',
        fillColor=HHS_DARK_NAVY,
        textAnchor='middle'
    ))

    # ── Bar chart ──
    bar_x = 280
    bar_w = 220
    chart_y = 20
    chart_h = 255
    bar_height = 20
    label_area = bar_x - 5
    label_font = 8

    bc = HorizontalBarChart()
    bc.x = bar_x
    bc.y = chart_y
    bc.height = chart_h
    bc.width = bar_w
    bc.data = [views_data]
    bc.barWidth = bar_height
    bc.strokeColor = colors.white

    bc.valueAxis.valueMin = 0
    bc.valueAxis.valueMax = max_value
    bc.valueAxis.visible = 0
    bc.valueAxis.labels.visible = 0

    bc.categoryAxis.labels.visible = 0
    bc.categoryAxis.categoryNames = ['' for _ in titles]

    bc.bars[0].fillColor = HHS_MEDIUM_BLUE

    drawing.add(bc)

    # ── Labels + value annotations ──
    gap = (chart_h - 10 * bar_height) / 11

    for i, (value, title) in enumerate(zip(views_data, titles)):

        y_center = (
            chart_y
            + gap
            + i * (bar_height + gap)
            + bar_height / 2
        )

        # Check if title fits on one line
        w = stringWidth(title, 'Helvetica', label_font)

        if w <= label_area:
            # Single line
            drawing.add(String(
                bar_x - 5,
                y_center - 2,
                title,
                fontSize=label_font,
                fontName='Helvetica',
                fillColor=HHS_DARKEST,
                textAnchor='end'
            ))

        else:
            # Split into two lines
            best_break = len(title) // 2

            for pos in range(len(title) // 2, len(title)):
                if title[pos] == ' ':
                    line1 = title[:pos]

                    if stringWidth(line1, 'Helvetica', label_font) <= label_area:
                        best_break = pos
                    else:
                        break

            line1 = title[:best_break]
            line2 = title[best_break:].strip()

            drawing.add(String(
                bar_x - 5,
                y_center + 3,
                line1,
                fontSize=label_font,
                fontName='Helvetica',
                fillColor=HHS_DARKEST,
                textAnchor='end'
            ))

            drawing.add(String(
                bar_x - 5,
                y_center - 5,
                line2,
                fontSize=label_font,
                fontName='Helvetica',
                fillColor=HHS_DARKEST,
                textAnchor='end'
            ))

        # ── Value label to right of bar ──
        bar_px = (value / max_value) * bar_w

        # One decimal precision for easier comparison
        if value >= 1_000_000:
            value_label = f"{value / 1_000_000:.1f}M"
        elif value >= 1_000:
            value_label = f"{value / 1_000:.1f}K"
        else:
            value_label = f"{value:.1f}"

        drawing.add(String(
            bar_x + bar_px + 5,
            y_center - 3,
            value_label,
            fontSize=7,
            fontName='Helvetica-Bold',
            fillColor=HHS_DARK_NAVY,
            textAnchor='start'
        ))

    return drawing

def create_metrics_infographic(landing_page, press_releases, grand_total, prev_data):
    """Create a visual metrics infographic."""
    drawing = Drawing(540, 60)
    drawing.hAlign = 'CENTER'

    total_pr_views = sum(pr['total_views'] for pr in press_releases)

    metrics = [
        ('Total Page Views', format_compact(grand_total['total_views']), HHS_DARK_NAVY),
        ('Press Release Views', format_compact(total_pr_views), HHS_DARK_NAVY),
        ('Total Users', format_compact(grand_total['total_users']), HHS_DARK_NAVY),
        ('Press Releases', str(len(press_releases)), HHS_DARK_NAVY),
    ]

    box_width = 110
    box_height = 45
    gap = 16
    total_width = 4 * box_width + 3 * gap
    start_x = (540 - total_width) / 2

    for i, (label, value, color) in enumerate(metrics):
        x = start_x + i * (box_width + gap)

        drawing.add(Rect(x, 5, box_width, box_height,
                        fillColor=color, strokeColor=None, rx=4, ry=4))

        drawing.add(String(x + box_width/2, 28, value,
                          fontSize=16, fontName='Helvetica-Bold',
                          fillColor=colors.white, textAnchor='middle'))

        drawing.add(String(x + box_width/2, 14, label,
                          fontSize=7, fontName='Helvetica',
                          fillColor=colors.white, textAnchor='middle'))

    return drawing


def truncate_title(title, max_len=50):
    """Truncate title if too long."""
    if len(title) > max_len:
        return title[:max_len-3] + '...'
    return title


def create_pdf_report(csv_file, output_file, dates_file, min_views=0):
    """Generate a PDF report with HHS branding and traffic source data.

    Args:
        csv_file: Path to the current period CSV file.
        output_file: Path for the output PDF.
        min_views: Minimum views threshold (unused).
        prev_csv_file: Optional path to previous period CSV for month-over-month comparison.
        dates_file: Optional path to JSON file mapping page paths to publish dates.
    """

    metadata, data, grand_total = parse_traffic_csv(csv_file)

    # Load publish dates if provided
    publish_dates = {}
    if dates_file:
        import json
        with open(dates_file, 'r', encoding='utf-8') as f:
            publish_dates = json.load(f)

    # Parse previous period data if provided
    prev_metadata = None
    prev_data = None
    """if prev_csv_file:
        prev_metadata, prev_data, prev_grand_total = parse_traffic_csv(prev_csv_file)"""

    # Separate landing page from press releases
    landing_page = next((d for d in data if d['page'] == '/press-room/index.html'), None)

    # Filter press releases: exclude landing page (404s already filtered in parse function)
    press_releases = [d for d in data
                      if d['page'] != '/press-room/index.html']

    # Sort by total views
    press_releases.sort(key=lambda x: x['total_views'], reverse=True)

    # Remove entries with negligible views (mostly zeros in traffic breakdown)
    press_releases = [pr for pr in press_releases if pr['total_views'] >= 10]

    # Remove entries without a known publish date
    if publish_dates:
        press_releases = [pr for pr in press_releases if pr['page'] in publish_dates]

    total_pr_views = sum(pr['total_views'] for pr in press_releases)
    total_users = grand_total['total_users']

    doc = SimpleDocTemplate(
        output_file,
        pagesize=letter,
        rightMargin=0.5*inch,
        leftMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=22,
        spaceAfter=4,
        textColor=HHS_DARK_NAVY,
        alignment=TA_CENTER
    )

    subtitle_style = ParagraphStyle(
        'CustomSubtitle',
        parent=styles['Normal'],
        fontSize=11,
        spaceAfter=12,
        textColor=HHS_MEDIUM_BLUE,
        alignment=TA_CENTER
    )

    section_style = ParagraphStyle(
        'SectionHeader',
        parent=styles['Heading2'],
        fontSize=12,
        spaceBefore=8,
        spaceAfter=4,
        textColor=HHS_DARK_NAVY
    )

    body_style = ParagraphStyle(
        'BodyText',
        parent=styles['Normal'],
        fontSize=10,
        spaceAfter=6,
        textColor=HHS_DARKEST,
        leading=14
    )

    cell_style = ParagraphStyle(
        'CellText',
        parent=styles['Normal'],
        fontSize=7,
        textColor=HHS_DARKEST,
        leading=9
    )

    link_style = ParagraphStyle(
        'LinkText',
        parent=styles['Normal'],
        fontSize=7,
        textColor=HHS_MEDIUM_BLUE,
        leading=9
    )

    story = []

    # HHS Logo at top (optional)
    import os
    if os.path.exists(HHS_LOGO_PATH):
        logo = Image(HHS_LOGO_PATH, width=2.5*inch, height=0.5*inch)
        logo.hAlign = 'CENTER'
        story.append(logo)
        story.append(Spacer(1, 12))

    # Header
    story.append(Paragraph("Press Room Analytics Report", title_style))
    story.append(Spacer(1, 6))

    date_range = f"{metadata.get('start_date', 'N/A')} - {metadata.get('end_date', 'N/A')}"
    story.append(Paragraph(date_range, subtitle_style))
    story.append(Spacer(1, 10))

    # Executive Summary
    story.append(Paragraph("Executive Summary", section_style))

    landing_views = landing_page['total_views'] if landing_page else 0
    total_all_views = grand_total['total_views']
    top_10_views = sum(pr['total_views'] for pr in press_releases[:10])
    rest_views = total_pr_views - top_10_views
    rest_count = len(press_releases) - 10

    exec_summary = f"""
    <p>The HHS.gov Press Room generated <b>{format_number(total_all_views)}</b> total page views in May, up 27% from April and 11% YoY. Strong organic traffic for MAHA focused releases, along with above-average social entries for Lyme Disease post, generated above average volume. 
    <p>The press room landing page accounts for <b>{landing_views/total_all_views*100:.1f}%</b> of total traffic, reinforcing its role as a primary entry point for policy discovery and navigation. The remaining {rest_count} press releases shared <b>{rest_views/total_all_views*100:.1f}%</b> of total press room views.</p>
    """
    story.append(Paragraph(exec_summary, body_style))

    story.append(Spacer(1, 2))

    # Month-over-month comparison
    if prev_data is not None:
        prev_prs = [d for d in prev_data if d['page'] != '/press-room/index.html']
        prev_pr_views = sum(pr['total_views'] for pr in prev_prs)
        prev_all_views = prev_grand_total['total_views']
        prev_all_users = prev_grand_total['total_users']

        def pct_change(current, previous):
            if previous == 0:
                return 0
            return ((current - previous) / previous) * 100

        def format_change(val):
            if val > 0:
                return f'<font color="green">+{val:.1f}%</font>'
            elif val < 0:
                return f'<font color="red">{val:.1f}%</font>'
            return '0%'

        views_change = pct_change(total_all_views, prev_all_views)
        users_change = pct_change(total_users, prev_all_users)
        pr_views_change = pct_change(total_pr_views, prev_pr_views)
        pr_count_change = len(press_releases) - len(prev_prs)

        prev_period = f"{prev_metadata.get('start_date', 'N/A')} - {prev_metadata.get('end_date', 'N/A')}"
        mom_text = f"""
        <b>Month-over-Month Change</b> (vs. {prev_period}):<br/>
        &bull; Total Page Views: {format_number(prev_all_views)} &rarr; {format_number(total_all_views)} ({format_change(views_change)})<br/>
        &bull; Total Users: {format_number(prev_all_users)} &rarr; {format_number(total_users)} ({format_change(users_change)})<br/>
        &bull; Press Release Views: {format_number(prev_pr_views)} &rarr; {format_number(total_pr_views)} ({format_change(pr_views_change)})<br/>
        &bull; Press Releases Published: {len(prev_prs)} &rarr; {len(press_releases)} ({'+' if pr_count_change >= 0 else ''}{pr_count_change})
        """
        story.append(Paragraph(mom_text, body_style))

    story.append(Spacer(1, 8))

    # Metrics Infographic
    story.append(Paragraph("Overview", section_style))
    story.append(create_metrics_infographic(landing_page, press_releases, grand_total, prev_data))
    story.append(Spacer(1, 12))

    # Visual Analytics Section - keep header with first chart
    visual_analytics_header = [
        Paragraph("Visual Analytics", section_style),
        Spacer(1, 4),
        create_top_10_bar_chart(press_releases),
        Spacer(1, 15)
    ]
    story.append(KeepTogether(visual_analytics_header))

    # Side-by-side pie charts
    story.append(create_views_distribution_pie(press_releases, landing_page, grand_total))
    story.append(Spacer(1, 15))

    # Press Releases Distribution (Top 10 vs Rest)
    story.append(create_press_releases_distribution_pie(press_releases))
    story.append(Spacer(1, 15))

    # Traffic Sources Pie Chart
    story.append(create_traffic_source_pie(data))
    story.append(Spacer(1, 15))

    # Press Room Landing Page Metrics
    story.append(Paragraph("Press Room Landing Page", section_style))
    story.append(Spacer(1, 6))

    # Landing Page Traffic Source Pie Chart
    if landing_page:
        story.append(create_landing_page_traffic_pie(landing_page))
        story.append(Spacer(1, 10))

    if landing_page:
        metrics_data = [
            ['Views', 'Users', 'Organic', 'Direct', 'Referral', 'Social'],
            [format_number(landing_page['total_views']),
             format_number(landing_page['total_users']),
             format_number(landing_page['organic_views']),
             format_number(landing_page['direct_views']),
             format_number(landing_page['referral_views']),
             format_number(landing_page['social_views'])]
        ]

        metrics_table = Table(metrics_data, colWidths=[1.1*inch, 1.1*inch, 1.0*inch, 1.1*inch, 1.0*inch, 0.9*inch])
        metrics_table.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HHS_DARK_NAVY),
            ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
            ('TOPPADDING', (0, 0), (-1, 0), 8),
            ('BACKGROUND', (0, 1), (-1, 1), colors.HexColor('#f7fafc')),
            ('FONTNAME', (0, 1), (-1, 1), 'Helvetica-Bold'),
            ('FONTSIZE', (0, 1), (-1, 1), 11),
            ('BOTTOMPADDING', (0, 1), (-1, 1), 10),
            ('TOPPADDING', (0, 1), (-1, 1), 10),
            ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
        ]))
        story.append(KeepTogether([metrics_table]))

    story.append(PageBreak())

    # All Press Releases Table with Traffic Sources
    story.append(Paragraph(f"All Press Releases by Views ({len(press_releases)} total)", section_style))
    
    # Add footnote about highlighting under the table title
    footnote_style = ParagraphStyle(
        'Footnote',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#666666'),
        alignment=TA_LEFT,
        leftIndent=0
    )
    story.append(Spacer(1, 8))

    table_data = [['#', 'Press Release Title', 'Published', 'Views', 'Users', 'Organic', 'Direct', 'Referral', 'Social']]

    BASE_URL = 'https://www.hhs.gov'

    # Track which rows should be highlighted
    highlight_rows = []

    for i, pr in enumerate(press_releases, 1):
        # Create clickable link for the title
        full_url = BASE_URL + pr['page']
        title_text = pr['title']

        # Get publish date from scraped dates JSON (preferred) or CSV fields
        pub_date_str = ''
        date_obj = None
        raw_date = publish_dates.get(pr['page'], '')
        if raw_date:
            # Parse ISO datetime (e.g. 2026-01-07T15:29:52Z) or text date
            try:
                if 'T' in raw_date:
                    date_obj = datetime.strptime(raw_date.split('T')[0], '%Y-%m-%d')
                elif len(raw_date) == 10 and '-' in raw_date:
                    date_obj = datetime.strptime(raw_date, '%Y-%m-%d')
                else:
                    date_obj = datetime.strptime(raw_date, '%B %d, %Y')
                pub_date_str = date_obj.strftime('%m/%d/%Y')
            except (ValueError, AttributeError):
                pub_date_str = raw_date[:10]

        # Check if this row should be highlighted (published during report period)
        if should_highlight_date(date_obj):
            highlight_rows.append(i)

        title_para = Paragraph(f'<a href="{full_url}" color="blue">{title_text}</a>', link_style)
        table_data.append([
            str(i),
            title_para,
            pub_date_str,
            format_number(pr['total_views']),
            format_number(pr['total_users']),
            format_number(pr['organic_views']),
            format_number(pr['direct_views']),
            format_number(pr['referral_views']),
            format_number(pr['social_views']),
        ])

    # Column widths with Published date column
    pr_table = Table(table_data, colWidths=[0.25*inch, 2.9*inch, 0.6*inch, 0.5*inch, 0.45*inch, 0.5*inch, 0.45*inch, 0.5*inch, 0.45*inch], repeatRows=1)
    
    # Build table style with alternating row colors and highlighting
    table_style = [
        ('BACKGROUND', (0, 0), (-1, 0), HHS_DARK_NAVY),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 8),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('TOPPADDING', (0, 0), (-1, 0), 6),
        ('FONTNAME', (0, 1), (-1, -1), 'Helvetica'),
        ('FONTNAME', (2, 1), (2, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 1), (-1, -1), 7),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'CENTER'),
        ('ALIGN', (1, 1), (1, -1), 'LEFT'),
        ('VALIGN', (0, 1), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 4),
        ('TOPPADDING', (0, 1), (-1, -1), 4),
        ('BOX', (0, 0), (-1, -1), 1, colors.HexColor('#e2e8f0')),
        ('LINEBELOW', (0, 0), (-1, -2), 0.5, colors.HexColor('#e2e8f0')),
    ]
    
    # Add alternating row colors (skip header row, start from row 1)
    for i in range(1, len(table_data)):
        if i in highlight_rows:
            # Highlight rows with dates >= 12/16/2025 in bright yellow
            table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#FFF59D')))
        elif i % 2 == 0:
            # Even rows (alternating gray)
            table_style.append(('BACKGROUND', (0, i), (-1, i), colors.HexColor('#f0f4f8')))
    
    pr_table.setStyle(TableStyle(table_style))
    story.append(pr_table)

    story.append(Spacer(1, 20))

    # Key Metrics Definitions at the bottom
    story.append(Paragraph("Key Metrics Definitions", section_style))
    definitions_list = [
        "<b>Views</b>: Total page loads (one person visiting 3 times = 3 views).",
        "<b>Users</b>: Unique visitors (one person visiting 3 times = 1 user).",
        "<b>Organic</b>: Traffic from search engines (Google, Bing).",
        "<b>Direct</b>: Visitors who typed the URL or used bookmarks.",
        "<b>Referral</b>: Traffic from links on other websites.",
        "<b>Social</b>: Traffic from social media platforms.",
    ]
    for definition in definitions_list:
        bullet_text = f"&bull; {definition}"
        story.append(Paragraph(bullet_text, body_style))
    story.append(Spacer(1, 20))

    # Footer
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontSize=8,
        textColor=colors.HexColor('#718096'),
        alignment=TA_CENTER
    )

    story.append(Paragraph("―" * 60, footer_style))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Report generated on {datetime.now().strftime('%B %d, %Y at %I:%M %p')}", footer_style))
    story.append(Paragraph(f"Data source: {metadata.get('property', 'HHS.gov Analytics')}", footer_style))

    doc.build(story)
    print(f"Report successfully generated: {output_file}")


if __name__ == '__main__':
    csv_file = '/Users/smofidishirazi/Downloads/pressrelease_1.26_dates.csv'
    output_file = '/Users/smofidishirazi/Downloads/HHS_Press_Releases_Traffic_Report_v1.pdf'
    create_pdf_report(csv_file, output_file)

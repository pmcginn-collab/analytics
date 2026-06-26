#!/bin/bash
# ──────────────────────────────────────────────────────────
# GTM & Tag Audit — double-click to run
# ──────────────────────────────────────────────────────────

# Go to the folder where this script lives (next to gtm_audit.py)
cd "$(dirname "$0")"

# ── Prompt for URL via a native macOS dialog ──
URL=$(osascript -e 'display dialog "Enter the website URL to audit:" default answer "https://" with title "GTM & Tag Audit" buttons {"Cancel","Audit"} default button "Audit"' -e 'text returned of result' 2>/dev/null)

if [ -z "$URL" ]; then
    echo "No URL entered. Exiting."
    exit 0
fi

# ── Check for Python 3 ──
if ! command -v python3 &>/dev/null; then
    osascript -e 'display alert "Python 3 not found" message "This tool requires Python 3. Install it from python.org or run: brew install python3" as critical'
    exit 1
fi

# ── Set up virtual environment on first run ──
if [ ! -d ".venv" ]; then
    echo "First run — setting up (this may take a minute)..."
    echo ""
    python3 -m venv .venv
    source .venv/bin/activate
    pip install --quiet playwright
    python -m playwright install chromium
    echo ""
    echo "Setup complete!"
    echo ""
else
    source .venv/bin/activate
fi

# ── Ensure PDF dependencies are installed ──
pip install --quiet markdown weasyprint 2>/dev/null

# ── Run the audit ──
echo "============================================"
echo "  Auditing: $URL"
echo "============================================"
echo ""

python gtm_audit.py "$URL" --pdf

# ── Open the report ──
DOMAIN=$(python3 -c "from urllib.parse import urlparse; print(urlparse('$URL').hostname)")
REPORT="${DOMAIN}-tag-audit.md"

if [ -f "$REPORT" ]; then
    echo ""
    echo "Opening report..."
    open "$REPORT"
fi

echo ""
echo "Done! You can close this window."
echo ""

# Keep terminal open so they can read output
read -n 1 -s -r -p "Press any key to close..."

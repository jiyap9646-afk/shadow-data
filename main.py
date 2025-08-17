import os
import re
from flask import Flask, render_template, request, url_for
from bs4 import BeautifulSoup, Tag
from collections import Counter
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import math
from datetime import datetime

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# ------------------- Small helpers -------------------
def parse_datetime(text):
    """Try parsing Google Takeout-style timestamps like 'January 1, 2025 at 10:30'."""
    try:
        return datetime.strptime(text, "%B %d, %Y at %H:%M")
    except Exception:
        return None

def clean_text(s: str) -> str:
        if not s:
            return ""
        s = re.sub(r"<[^>]+>", "", s)  # remove HTML tags
        s = re.sub(r"https?://\S+", "", s)  # remove URLs
        s = re.sub(r"www\.\S+", "", s)  # remove www links
        s = re.sub(r"[^A-Za-z0-9\s]", " ", s)  # remove special chars
        s = re.sub(r"\s+", " ", s).strip()
        return s.lower()

def clean_top5_item(s: str) -> str:
        junk_words = {"here", "click", "login", "home", "ok"}
        words = [w for w in s.split() if w not in junk_words and len(w) > 2]
        return " ".join(words).strip()

def top5_from_list(items):
        cleaned_items = [clean_top5_item(clean_text(x)) for x in items if clean_text(x)]
        cleaned_items = [x for x in cleaned_items if x]
        if not cleaned_items:
            return [("No data found", 0)]
        return Counter(cleaned_items).most_common(5)

# ------------------- Generic analyzer -------------------
def analyze_generic_file(filepath):
    categories = {"Search":0,"YouTube":0,"Maps":0,"Shopping":0,"Discover":0,"Other":0}
    activity_times = []
    items_for_top5 = []  # generic catch-all

    with open(filepath, 'r', encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')

    for div in soup.find_all('div'):
        text = div.get_text(separator=' ', strip=True)
        low = text.lower()

        # very loose categorization
        if "searched" in low or "search" in low:
            categories["Search"] += 1
        elif "youtube" in low or "watched" in low:
            categories["YouTube"] += 1
        elif "maps" in low or "location" in low or "place" in low:
            categories["Maps"] += 1
        elif "shopping" in low or "product" in low:
            categories["Shopping"] += 1
        elif "discover" in low:
            categories["Discover"] += 1
        else:
            categories["Other"] += 1

        # collect something readable for Top 5 (best-effort)
        a_tag = div.find('a')
        if a_tag and isinstance(a_tag, Tag):
            items_for_top5.append(a_tag.get_text(strip=True))
        else:
            # fallback to the first sentence-ish chunk
            chunk = clean_text(text.split(" • ")[0])
            if chunk:
                items_for_top5.append(chunk)

        # timestamp extraction
        span = div.find('span')
        if span:
            dt = parse_datetime(span.get_text(strip=True))
            if dt:
                activity_times.append(dt)

    top5_data = top5_from_list(items_for_top5)
    return categories, top5_data, activity_times

# ------------------- Search analyzer -------------------
def analyze_search_file(filepath):
    search_terms = []
    categories = {"Search":0,"Other":0}
    activity_times = []

    with open(filepath,'r',encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(),'html.parser')

    for div in soup.find_all('div'):
        text = div.get_text(separator=' ', strip=True)
        found = False
        for phrase in ["Searched for", "You searched for", "Searched on Google for"]:
            if phrase in text:
                categories["Search"] += 1
                # take the part after the phrase
                term = text.split(phrase, 1)[1]
                term = clean_text(term)
                # remove leading connecting words
                term = re.sub(r"^(for|on|about)\s+", "", term, flags=re.I)
                # drop obvious junk timestamps if stuck on same line
                term = re.sub(r"\bat \d{1,2}:\d{2}\b.*$", "", term)
                if term:
                    search_terms.append(term)
                found = True
                break
        if not found:
            categories["Other"] += 1

        # timestamp extraction
        span = div.find('span')
        if span:
            dt = parse_datetime(span.get_text(strip=True))
            if dt:
                activity_times.append(dt)

    top5_data = top5_from_list(search_terms)
    return categories, top5_data, activity_times

# ------------------- YouTube analyzer -------------------
def analyze_youtube_file(filepath):
    video_titles = []
    search_queries = []
    categories = {"YouTube":0,"Search":0,"Maps":0,"Shopping":0,"Discover":0,"Other":0}
    activity_times = []

    with open(filepath,'r',encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(),'html.parser')

    for div in soup.find_all('div'):
        text = div.get_text(separator=' ', strip=True)

        # capture YT searches
        for phrase in ["Searched for", "Search for", "Searched on YouTube for"]:
            if phrase in text:
                q = clean_text(text.split(phrase,1)[1])
                q = re.sub(r"^(for|on|about)\s+", "", q, flags=re.I)
                if q:
                    search_queries.append(q)

        # capture watched titles via links
        a_tag = div.find('a')
        if a_tag and isinstance(a_tag, Tag):
            href = a_tag.get('href','')
            if 'youtube.com' in href or 'youtu.be' in href:
                title = clean_text(a_tag.get_text(strip=True))
                if title and not title.lower().startswith("watched a video that has been removed"):
                    video_titles.append(title)

        # timestamps
        span = div.find('span')
        if span:
            dt = parse_datetime(span.get_text(strip=True))
            if dt:
                activity_times.append(dt)

    combined = search_queries + video_titles
    categories["YouTube"] = len(video_titles)
    categories["Search"] = len(search_queries)

    top5_data = top5_from_list(combined)
    return categories, top5_data, activity_times

# ------------------- Discover analyzer -------------------
def analyze_discover_file(filepath):
    items = []
    categories = {"Discover":0}
    activity_times = []

    with open(filepath,'r',encoding='utf-8') as f:
        soup = BeautifulSoup(f.read(),'html.parser')

    for div in soup.find_all('div'):
        text = div.get_text(separator='\n',strip=True)
        if not text:
            continue
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            continue

        first_line = lines[0].lower()
        block_text_lower = "\n".join(lines).lower()
        is_discover = ('discover' in first_line) or ('products:' in block_text_lower and 'discover' in block_text_lower)
        if not is_discover:
            continue

        start_index = 1
        for i, ln in enumerate(lines):
            if ln.lower().startswith('details'):
                start_index = i+1
                break

        for ln in lines[start_index:]:
            if ln.lower().startswith('why is this here'):
                break
            clean = re.sub(r'\s*-\s*viewed$','',ln,flags=re.I).strip()
            clean = re.sub(r'viewed$','',clean,flags=re.I).strip()
            if re.search(r'\bcard(s)?\b|\bin your feed\b',clean,flags=re.I):
                continue
            clean = clean_text(clean)
            if clean:
                items.append(clean)

        # timestamp
        span = div.find('span')
        if span:
            dt = parse_datetime(span.get_text(strip=True))
            if dt:
                activity_times.append(dt)

    counter = Counter(items)
    top5_data = counter.most_common(5) if items else [("No data found", 0)]
    categories["Discover"] = sum(counter.values())
    return categories, top5_data, activity_times

# ------------------- Risk + Suggestions (returns percent) -------------------
def calculate_risk(categories, recent_activities=None):
    weights = {
        "Search": 1,
        "YouTube": 2,
        "Maps": 3,
        "Shopping": 2,
        "Discover": 1,
        "Other": 1
    }

    # Base risk from total activity volume
    base_risk = sum(weights.get(cat, 1) * math.log(1 + count) for cat, count in categories.items())

    # Recent activity adds more weight (7-day decay)
    recent_risk = 0
    if recent_activities:
        now = datetime.now()
        for act_time in recent_activities:
            days_ago = (now - act_time).days
            recent_risk += math.exp(-days_ago / 7)

    total_risk = base_risk + recent_risk

    # Convert total_risk to a sensible 0-100 percent for frontend meter
    # We assume most meaningful range is around 0..15; clamp to 100
    risk_percent = int(max(0, min(100, (total_risk / 15.0) * 100)))

    if total_risk <= 3:
        return (
            "Low",
            "green",
            "Low tracking detected.",
            [
                "Keep privacy settings reviewed monthly.",
                "Use incognito for sensitive searches.",
                "Periodically clear browsing history."
            ],
            risk_percent
        )
    elif total_risk <= 10:
        return (
            "Medium",
            "#ffcc00",  # yellow that’s readable on white
            "Moderate tracking detected.",
            [
                "Turn off Location History in Google settings.",
                "Review connected apps and revoke unused permissions.",
                "Use a privacy-focused browser (Brave/Firefox) for routine browsing."
            ],
            risk_percent
        )
    else:
        return (
            "High",
            "red",
            "Heavy tracking detected recently.",
            [
                "Pause Web & App Activity (Google Account ▶ Data & privacy).",
                "Delete recent activity from My Activity.",
                "Consider a VPN for browsing and disable ad personalization."
            ],
            risk_percent
        )

# ------------------- Routes -------------------
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        chart_url = None
        top5_chart_url = None
        display_top5 = []

        if 'file' not in request.files:
            return 'No file part'
        file = request.files['file']
        if file.filename == '':
            return 'No selected file'
        filename = file.filename
        filename_lower = filename.lower()

        if not os.path.exists(app.config['UPLOAD_FOLDER']):
            os.makedirs(app.config['UPLOAD_FOLDER'])
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(filepath)

        # Choose analyzer
        if "search" in filename_lower:
            categories, top5_data, activity_times = analyze_search_file(filepath)
        elif "youtube" in filename_lower or "watch" in filename_lower:
            categories, top5_data, activity_times = analyze_youtube_file(filepath)
        elif "discover" in filename_lower or "myactivity" in filename_lower:
            categories, top5_data, activity_times = analyze_discover_file(filepath)
        else:
            categories, top5_data, activity_times = analyze_generic_file(filepath)

        # Risk Meter (+ suggestions + percent)
        risk_level, risk_color, risk_message, risk_suggestions, risk_percent = calculate_risk(categories, recent_activities=activity_times)

        # Pie Chart (only non-zero categories)
        filtered_categories = {k: v for k, v in categories.items() if v > 0}
        if filtered_categories:
            plt.figure(figsize=(6, 6))
            plt.pie(list(filtered_categories.values()), labels=list(filtered_categories.keys()),
                    autopct='%1.1f%%', startangle=90)
            plt.title('Activity Breakdown')
            if not os.path.exists('static'):
                os.makedirs('static')
            chart_url = 'activity_chart.png'
            plt.savefig(os.path.join('static', chart_url))
            plt.close()

        # Top 5 Chart (now enabled for ALL types when data exists)
        top5_plot_data = [(label, count) for label, count in (top5_data or []) if label and count and label != "No data found"]
        display_top5 = top5_plot_data
        if top5_plot_data:
            top_labels, top_counts = zip(*top5_plot_data)
            display_labels = [label[:40] + '…' if len(label) > 40 else label for label in top_labels]

            plt.figure(figsize=(10, 5))
            plt.barh(display_labels, top_counts)
            plt.xlabel('Frequency')
            plt.title('Top 5 Interests / Items')
            plt.tight_layout()
            if not os.path.exists('static'):
                os.makedirs('static')
            safe_filename = re.sub(r'\W+', '_', filename_lower)
            top5_chart_url = f"top5_{safe_filename}.png"
            plt.savefig(os.path.join('static', top5_chart_url))
            plt.close()

        return render_template(
            'index.html',
            filename=filename,
            categories=categories,
            top_5_data=display_top5,
            chart_url=chart_url,
            top5_chart_url=top5_chart_url,
            risk_level=risk_level,
            risk_color=risk_color,
            suggestion=risk_message,          # keep your existing var name
            risk_suggestions=risk_suggestions, # NEW: list for the suggestion box
            risk_percent=risk_percent        # NEW: percent for frontend meter
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Internal Server Error: {e}"

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=3000)
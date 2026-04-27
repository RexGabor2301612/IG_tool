# S&R Extract - Production Social Media Scraper & Analytics System

**Version**: 1.0.0  
**Status**: Production-Ready (Beta)  

---

## 🎯 Overview

S&R Extract is a **production-grade dual-platform scraper** for Instagram and Facebook with:

- **Real-time logging** via WebSocket
- **Event-backed state machine** (10+ formal states)
- **High-accuracy data extraction** (3x retry logic)
- **Automatic deduplication** (SQLite + Pandas)
- **Incremental saving** (per-post persistence)
- **Persistent browser sessions** (Playwright)
- **Excel/CSV export** (full data pipeline)

---

## 🏗️ Architecture

```
s:/IG_analyzer/
│
├── core/                           # Core production systems
│   ├── logging/                    # Real-time logging + WebSocket streaming
│   │   ├── __init__.py
│   │   ├── logger.py               # ProductionLogger class
│   │   └── streaming.py            # LogStreamBroadcaster (WebSocket)
│   │
│   ├── state/                      # Formalized state machine
│   │   ├── __init__.py
│   │   └── machine.py              # ScrapeState enum + ScrapeJobState class
│   │
│   ├── session/                    # Playwright session manager
│   │   ├── __init__.py
│   │   └── manager.py              # PlaywrightSessionManager
│   │
│   ├── extraction/                 # High-accuracy data extraction
│   │   ├── __init__.py
│   │   ├── selectors.py            # Platform selectors (IG/FB)
│   │   └── extractor.py            # DataExtractor with retry logic
│   │
│   └── etl/                        # ETL pipeline
│       ├── __init__.py
│       └── etl_engine.py           # ETLPipeline (Pandas + SQLite)
│
├── backend/                        # Flask application
│   └── app.py                      # Main Flask app (orchestrator)
│
├── scrapers/                       # Scraper implementations
│   ├── instagram/
│   └── facebook/
│
├── templates/                      # HTML templates
│   └── dashboard.html              # Main UI (4-panel layout)
│
├── static/                         # Frontend assets
│   ├── css/
│   │   └── dashboard.css
│   ├── js/
│   │   └── dashboard.js
│   └── img/
│       ├── instagram.png
│       └── facebook.png
│
├── data/                           # Data output directory
│   └── (generated .xlsx files)
│
├── storage_states/                 # Persistent Playwright sessions
│   ├── instagram_auth.json
│   └── facebook_auth.json
│
├── logs/                           # SQLite log database
│   └── logs.db
│
└── requirements.txt                # Python dependencies
```

---

## 🚀 Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Start Flask Backend

```bash
python backend/app.py
```

Server runs on `http://localhost:5000`

### 3. Open Dashboard

Navigate to:
- **Instagram**: http://localhost:5000/
- **Facebook**: http://localhost:5000/facebook

### 4. Extract

1. Paste URL (Instagram profile or Facebook page)
2. Set dates (optional) and scroll rounds
3. Click **"Review Setup"** → **"Start Extraction"**
4. When prompted, log in manually in the browser
5. Click **"GO (Start Collection)"** when ready
6. Monitor logs in real-time (right panel)
7. Download Excel export when complete

---

## 📊 System Pipeline (State Machine)

### Phase 1: **Setup & Validation**
- Validate inputs (URL, dates, etc.)
- Reject invalid configs

### Phase 2: **Browser Init & Session**
- Launch Playwright browser
- Load persistent session (if exists)
- Save session after auth

### Phase 3: **Login & CAPTCHA Gate**
- Detect login form
- Allow manual user login
- Handle CAPTCHA with retry (max 3 attempts)

### Phase 4: **Page Readiness Gate** ⭐ CRITICAL
- Wait for posts container visible
- Verify ≥1 post loaded
- DOM must be stable
- **Fallback**: Force-ready after 10s timeout

### Phase 5: **User Confirmation**
- System state = `PAGE_READY`
- Display "GO (Start Collection)" button
- Wait for user click (max 10 min timeout)

### Phase 6: **Collection Engine** 🔄 MAIN LOOP
```
for each scroll round:
  - Scroll down
  - Explicit wait (no time.sleep)
  - Detect new posts
  - Extract data (3x retry if fail)
  - Add to buffer
  - Check stagnation (0 new posts after round)
```

### Phase 7: **ETL Pipeline**
- Load posts into Pandas DataFrame
- Full deduplication (by URL)
- Data validation
- Fill missing → "N/A"
- Export to Excel

### Phase 8: **Export & Download**
- Generate `.xlsx` file
- Provide download link
- Display stats (total posts, likes, comments, shares)

---

## 🔌 API Endpoints

### Core Endpoints

| Method | Endpoint | Purpose |
|--------|----------|---------|
| POST | `/api/validate` | Validate inputs before job start |
| POST | `/api/start` | Start new scraping job |
| POST | `/api/go` | Send GO signal (user confirms) |
| POST | `/api/cancel` | Cancel running job |
| POST | `/api/pause` | Pause collection |
| POST | `/api/resume` | Resume from pause |
| GET | `/api/status` | Get current job status |
| POST | `/api/clear-logs` | Clear log buffer |
| GET | `/api/logs` | Get recent logs (JSON) |
| GET | `/api/download` | Download export file |

### WebSocket

| Endpoint | Purpose |
|----------|---------|
| `ws://localhost:5000/ws/dashboard` | Real-time UI updates (logs, progress, status) |

---

## 🔐 Real-Time Logging System

### Log Levels
- **INFO**: Normal operation steps
- **SUCCESS**: Completed actions (✓)
- **WARN**: Partial issues or fallbacks (⚠)
- **ERROR**: Failures that stop progress (✗)

### Example Logs
```
[08:15:32] INFO  | Initializing browser | Platform: instagram
[08:15:35] SUCCESS | Browser created | Session: abc12345
[08:15:40] WARN  | Login required | Waiting for manual login
[08:15:50] SUCCESS | Login completed | User logged in successfully
[08:16:00] INFO  | Scroll round 1 | Scrolling down to load more posts
[08:16:02] SUCCESS | Scroll 1 | +4 new posts
[08:16:04] SUCCESS | Post extracted | URL: https://instagram.com/p/ABC123
```

### WebSocket Message Format

```json
{
  "type": "log",
  "data": {
    "timestamp": "2025-04-27T08:15:32Z",
    "level": "INFO",
    "action": "Job started",
    "details": "Extraction job has been queued"
  }
}
```

---

## 📈 Data Extraction Rules

### High-Accuracy Extraction

Each post extraction includes **3 retries** on failure:

1. **Extract URL** (required)
   - Format: Absolute URL (https://...)
   - Retry if missing

2. **Extract Timestamp** (required)
   - Format: ISO 8601 (YYYY-MM-DDTHH:MM:SSZ)
   - Auto-convert from DOM format
   - Retry 2–3x if parse fails

3. **Extract Metrics** (with defaults)
   - Likes: Integer (default 0)
   - Comments: Integer (default 0)
   - Shares: Integer (default 0)
   - Normalize: 1.2k → 1200, 500M → 500,000,000

### Inline Processing

- **Number Normalization**: "1.2k" → 1200
- **Timestamp Conversion**: "2 days ago" → ISO format
- **Emoji Removal**: Strip from text
- **Immediate Dedup**: Skip if URL exists in buffer

---

## 💾 Data Safety & Persistence

### Incremental Saving

Each post is **saved immediately** to SQLite:

```python
etl.save_post({
    "url": "https://instagram.com/p/ABC123",
    "timestamp": "2025-04-27T08:15:32Z",
    "likes": 1200,
    "comments": 45,
    "shares": 12,
})
```

### Session Persistence

Playwright browser sessions are **saved automatically**:
- Location: `storage_states/{platform}_auth.json`
- Includes: Cookies, local storage, authentication
- Reused on next run (skip login if still valid)

### Database Storage

Posts stored in SQLite with automatic dedup:

```sql
CREATE TABLE posts (
    id INTEGER PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    timestamp TEXT,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    platform TEXT,
    imported_at DATETIME
)
```

---

## 🎛️ Platform Switching (IG ↔ FB)

Click platform logo to switch:

```html
<!-- Header -->
<div class="platform-switch">
  <button class="switch-btn active" data-platform="instagram">Instagram</button>
  <button class="switch-btn" data-platform="facebook">Facebook</button>
</div>
```

### What Changes on Switch

| Component | Instagram | Facebook |
|-----------|-----------|----------|
| **Login Selector** | `[aria-label="Log in"]` | `form[aria-label="Log in"]` |
| **Posts Container** | `main` | `div[role="main"]` |
| **Post Item** | `article` | `div[data-testid="post"]` |
| **Metrics Selector** | `[aria-label*="like"]` | `span:has-text("people")` |
| **Session File** | `instagram_auth.json` | `facebook_auth.json` |
| **DB File** | `instagram_posts.db` | `facebook_posts.db` |

**Note**: UI **stays identical**; only selectors and logic change

---

## 🎨 UI/UX Structure

### 4-Panel Dashboard

#### LEFT PANEL: Input & Configuration
- Target URL / Username
- Start Date / End Date
- Scroll Rounds
- Output Filename
- "Review Setup" button
- "Start Extraction" button

#### CENTER PANEL: Extraction Status
- Status indicator + label
- Progress bar (%)
- Current post info
- Output file status
- Control buttons (GO, Cancel, Pause, Resume)

#### RIGHT PANEL: Real-Time Logs
- Terminal-style logs
- Time | Level | Action | Details
- Newest entries first
- Up to 500 visible logs
- Clear button

#### BOTTOM PANEL: Metrics
- Posts Found
- Progress %
- Success Rate %
- Error Count

---

## 🐛 Error Handling

### Automatic Retry Logic

| Error Type | Retries | Action |
|-----------|---------|--------|
| Login timeout | 1 | Allow manual login (10 min) |
| CAPTCHA | 3 | Manual solve + polling |
| Page not ready | Auto-fallback | Force-ready after 10s |
| Extraction fail | 3 | Skip post, log WARN |
| Stagnation (0 posts) | Detected | Stop scrolling |

### No Silent Failures

Every error is **logged**:
```
[08:16:15] ERROR | Extraction failed | Could not find likes selector after 3 retries (skipping post)
[08:16:20] WARN | Stagnation detected | 3 scroll rounds with 0 new posts (stopping collection)
[08:16:25] WARN | Session invalid | Reloading login page
```

---

## 📋 Configuration

### Environment Variables

```bash
PORT=5000                    # Flask server port
FLASK_ENV=production         # Flask environment
PLAYWRIGHT_HEADLESS=false    # Show browser window
```

### settings.py (Not created yet, for future)

```python
# Timeouts
READINESS_TIMEOUT = 10000  # ms
LOGIN_TIMEOUT = 600         # seconds (10 min)
CAPTCHA_RETRY_LIMIT = 3
SCROLL_TIMEOUT = 5000       # ms

# Collection
MAX_SCROLL_ROUNDS = 100
STAGNATION_THRESHOLD = 3    # rounds with 0 posts
RETRY_EXTRACTION_COUNT = 3

# Data
MAX_LOG_BUFFER = 1000
MAX_DATA_BUFFER = 1000
DEDUP_WINDOW = 0.5  # seconds (prevent duplicate logs)
```

---

## 🔧 Extending the System

### Adding a New Platform (e.g., TikTok)

1. **Create selector class** (`core/extraction/selectors.py`):
```python
class TikTokSelectors(PlatformSelectors):
    def __init__(self):
        super().__init__(
            login_form="[data-testid='login_form']",
            posts_container="[data-testid='main-feed']",
            # ... etc
        )
```

2. **Register in SelectorFactory**:
```python
_SELECTORS = {
    Platform.TIKTOK: TikTokSelectors,
}
```

3. **Add Flask route** (`backend/app.py`):
```python
@app.route("/tiktok")
def tiktok_home():
    return render_template("dashboard.html", platform="tiktok")
```

4. **Create scraper** (`scrapers/tiktok/scraper.py`)

---

## 📦 Dependencies

### Python
```
Flask==2.3.0
Flask-Sock==0.2.9
Playwright==1.40.0
Pandas==2.0.0
openpyxl==3.10.0
```

### Browser
- Chromium (installed via Playwright)

---

## 🧪 Testing

### Unit Tests (Future)

```bash
pytest tests/
```

### Manual Testing Checklist

- [ ] Login flow (IG & FB)
- [ ] CAPTCHA handling
- [ ] Real-time logs display
- [ ] Go signal handoff
- [ ] Data extraction (3+ posts)
- [ ] Deduplication
- [ ] Excel export
- [ ] Session reuse
- [ ] Pause/resume
- [ ] Cancellation

---

## 📝 Logs & Debugging

### View Logs
```bash
# Recent 100 logs
curl http://localhost:5000/api/logs | jq

# SQLite logs
sqlite3 logs/logs.db "SELECT * FROM logs LIMIT 50;"
```

### Enable Debug Logging
```python
# In backend/app.py
app.run(debug=True)
```

---

## 🚀 Deployment

### Local Development
```bash
python backend/app.py
```

### Production (ngrok tunnel)
```bash
./ngrok.exe http 5000
# Share link: https://xxxx-xx-xxx-xxx.ngrok.io
```

### Docker (Future)
```dockerfile
FROM python:3.10
COPY . /app
WORKDIR /app
RUN pip install -r requirements.txt
CMD ["python", "backend/app.py"]
```

---

## 📊 Performance Notes

- **No time.sleep()**: Uses Playwright explicit waits only
- **Stagnation detection**: Stops after 3 rounds with 0 new posts
- **Connection pooling**: SQLite + Pandas optimize batch inserts
- **WebSocket streaming**: Real-time updates without polling
- **Session reuse**: Skips login if session valid (saves 1–2 min per run)

---

## 🎯 Accuracy Target

- **Extraction success rate**: >95% (3x retry)
- **Deduplication accuracy**: 100% (URL-based)
- **Data validity**: 100% (required fields enforced)
- **Uptime**: 99%+ (error handling + recovery)

---

## 🔮 Future Enhancements

- [ ] TikTok support
- [ ] Comment collection
- [ ] AI-powered content analysis
- [ ] Scheduled jobs (Celery)
- [ ] API key authentication
- [ ] User accounts + saved jobs
- [ ] Data visualization dashboard
- [ ] Export to Google Sheets
- [ ] Docker deployment
- [ ] Unit test suite

---

## 💬 Support

For issues or questions, refer to:
- Logs panel (real-time)
- Backend logs (console output)
- SQLite logs database (`logs/logs.db`)

---

**Last Updated**: April 27, 2025  
**Maintainer**: Engineering Team  
**License**: Proprietary

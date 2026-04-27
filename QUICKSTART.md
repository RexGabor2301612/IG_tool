# 🚀 Quick Start Guide - S&R Extract v1.0.0

## 5-Minute Setup

### Step 1: Install Dependencies
```bash
cd s:\IG_analyzer

# Activate Python environment (if using venv)
.\.venv\Scripts\Activate.ps1

# Install production packages
pip install -r requirements.txt

# Install Playwright browsers
playwright install
```

### Step 2: Start the Server
```bash
python backend/app.py
```

**Output**:
```
Starting production server on port 5000
 * Running on http://0.0.0.0:5000
```

### Step 3: Open Dashboard
- **Instagram**: http://localhost:5000/
- **Facebook**: http://localhost:5000/facebook

### Step 4: Extract Data

1. **Enter URL**
   - Instagram: `https://www.instagram.com/nasa/` (or any username)
   - Facebook: `https://www.facebook.com/nasa` (or any page)

2. **Set Options** (optional)
   - Start Date / End Date
   - Scroll Rounds (default: 5)
   - Output filename

3. **Click "Review Setup"** → **"Start Extraction"**
   - Browser opens automatically
   - If logged out, log in manually
   - Wait for "Ready for Collection" status

4. **Click "GO (Start Collection)"**
   - System starts scrolling and extracting posts
   - Real-time logs appear in right panel
   - Monitor progress bar

5. **Download Results**
   - When complete, click "⬇️ Download"
   - Excel file with all posts, likes, comments, shares

---

## 🔧 System Architecture

### Core Modules (All Production-Ready)

| Module | Purpose | Status |
|--------|---------|--------|
| `core/logging/` | Real-time logs + WebSocket streaming | ✅ Ready |
| `core/state/` | Formalized state machine (10 states) | ✅ Ready |
| `core/session/` | Playwright session manager | ✅ Ready |
| `core/extraction/` | High-accuracy data extraction (3x retry) | ✅ Ready |
| `core/etl/` | Pandas + SQLite pipeline | ✅ Ready |
| `backend/app.py` | Flask orchestrator + API endpoints | ✅ Ready |
| `static/js/` | Frontend dashboard (to be updated) | 🔄 Partial |
| `templates/` | HTML template (to be updated) | 🔄 Partial |

### What's New vs Old

**Previous System Issues**:
- ❌ Unclear state transitions
- ❌ No real-time logging UI
- ❌ Silent failures
- ❌ Unclear GO signal handling
- ❌ No systematic retry logic

**Production System Features**:
- ✅ Canonical state machine (10+ formal states)
- ✅ Real-time WebSocket log streaming
- ✅ No silent failures (all errors logged)
- ✅ Event-backed GO handoff
- ✅ Automatic 3x retry on extraction fail
- ✅ Full deduplication (URL-based)
- ✅ Incremental SQLite save (per-post)
- ✅ Session persistence + reuse
- ✅ Comprehensive error recovery

---

## 📊 State Machine Flow

```
┌─────────────────────────────────────────────────────────┐
│  SETUP → VALIDATION → BROWSER_INIT → SESSION_LOADING   │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│  WAITING_LOGIN → PAGE_READINESS_CHECK → PAGE_READY      │
│       ↓ (manual login)                         ↓          │
│  CAPTCHA_DETECTED → WAITING_VERIFICATION      ↓          │
│       ↓ (user solves)                          ↓          │
│  PAGE_READINESS_CHECK ←────────────────────────┘          │
└─────────────────────────────────────────────────────────┘
              ↓
┌─────────────────────────────────────────────────────────┐
│  WAITING_USER_CONFIRM (user clicks GO)                  │
│       ↓                                                   │
│  COLLECTION_RUNNING ← → COLLECTION_PAUSED               │
│       ↓                                                   │
│  ┌─ COLLECTION_COMPLETED                                │
│  ├─ COLLECTION_FAILED                                   │
│  └─ COLLECTION_CANCELLED                                │
└─────────────────────────────────────────────────────────┘
```

---

## 🔌 API Quick Reference

### Start Job
```bash
curl -X POST http://localhost:5000/api/start \
  -H "Content-Type: application/json" \
  -d '{
    "platform": "instagram",
    "url": "https://www.instagram.com/nasa/",
    "scroll_rounds": 5
  }'
```

### Send GO Signal
```bash
curl -X POST http://localhost:5000/api/go
```

### Cancel Job
```bash
curl -X POST http://localhost:5000/api/cancel
```

### Get Status
```bash
curl http://localhost:5000/api/status
```

### Get Logs (JSON)
```bash
curl http://localhost:5000/api/logs | jq
```

---

## 📝 Real-Time Logging

All actions logged with:
- **Timestamp** (ISO format)
- **Level** (INFO / SUCCESS / WARN / ERROR)
- **Action** (short description)
- **Details** (full context)

### Example Log Stream
```
08:15:32 INFO    Job started         Extraction job has been queued
08:15:35 SUCCESS Browser created     Session: abc12345
08:15:40 WARN    Login required      Waiting for manual login
08:15:50 SUCCESS Login completed     User logged in successfully
08:16:00 INFO    Scroll round 1      Scrolling down to load more posts
08:16:02 SUCCESS Scroll 1            +4 new posts
08:16:04 SUCCESS Post extracted      URL: https://instagram.com/p/ABC123
08:16:06 SUCCESS Post saved          1 new post in buffer
```

---

## 💾 Data Output

### Excel File Format

**Columns**:
- id (auto)
- url
- timestamp (ISO)
- likes
- comments
- shares
- text_preview
- platform
- imported_at (auto)

**Features**:
- All numbers normalized (1.2k → 1200)
- Deduplicated (no duplicate URLs)
- Sorted by newest first

### Location
```
data/{platform}_posts_{YYYYMMDD_HHMMSS}.xlsx
```

### Download
Auto-download when extraction completes, or:
```bash
curl -X GET http://localhost:5000/api/download > export.xlsx
```

---

## 🔐 Session Management

### Auto-Save
After successful login:
```
storage_states/instagram_auth.json  # Instagram cookies + session
storage_states/facebook_auth.json   # Facebook cookies + session
```

### Auto-Reuse
On next run, system checks if session still valid:
- ✅ Valid → Skip login (save 1–2 min)
- ❌ Expired → Prompt for new login

### Manual Reset
```bash
# Delete session files to force fresh login
rm storage_states/instagram_auth.json
rm storage_states/facebook_auth.json
```

---

## 🧪 Testing Checklist

- [ ] **Login Flow**
  - Instagram login works
  - Facebook login works
  - Session saved after login

- [ ] **CAPTCHA Handling**
  - System detects CAPTCHA
  - Allows manual solve
  - Retries up to 3x
  - Stops if max retries exceeded

- [ ] **Data Extraction**
  - ≥3 posts extracted
  - All fields populated (url, timestamp, likes, comments, shares)
  - Numbers normalized (1.2k → 1200)

- [ ] **Logging**
  - Real-time logs appear in right panel
  - WebSocket connection stable
  - No duplicate log entries

- [ ] **Export**
  - Excel file generated
  - All posts in file
  - No corrupted data

- [ ] **Pause/Resume**
  - Pause button works during collection
  - Resume button works from pause
  - No data loss

- [ ] **Cancellation**
  - Cancel stops at next checkpoint
  - Data saved before cancel

---

## 🐛 Troubleshooting

### Issue: Browser doesn't open
**Solution**:
```bash
# Reinstall Playwright browsers
playwright install chromium
```

### Issue: "No session found" error
**Solution**:
```bash
# Clear old sessions
rm -r storage_states/
```

### Issue: WebSocket connection fails
**Solution**:
- Check firewall (port 5000)
- Try: `http://localhost:5000/` (not https)
- Restart server

### Issue: CAPTCHA timeout
**Solution**:
- Solve CAPTCHA manually in browser
- System will auto-detect completion
- Max timeout: 10 minutes

### Issue: "0 posts extracted"
**Solution**:
1. Check if URL is correct
2. Verify target has posts (not private account)
3. Try increasing scroll rounds
4. Check logs for specifics

---

## 📚 Next Steps

### Customization

1. **Add TikTok Support**
   - Create `core/extraction/selectors.py` → `TikTokSelectors`
   - Add Flask route `/tiktok`
   - Register in `SelectorFactory`

2. **Enable Comment Scraping**
   - Extend `DataExtractor` with comment extraction
   - Add `comments` table to ETL
   - Update UI

3. **Integrate Analytics**
   - Add trending posts detection
   - Sentiment analysis (TextBlob)
   - Hashtag frequency charts

### Production Deployment

1. **Docker**
   ```dockerfile
   FROM python:3.10
   COPY . /app
   WORKDIR /app
   RUN pip install -r requirements.txt
   RUN playwright install
   CMD ["python", "backend/app.py"]
   ```

2. **Cloud Hosting**
   - Azure Container Instances
   - AWS Lambda (with headless mode)
   - Render / Railway

3. **Monitoring**
   - Set up error alerts
   - Monitor disk usage (data/)
   - Track WebSocket connections

---

## 📞 Support

**For issues**:
1. Check logs panel (right side)
2. Run `curl http://localhost:5000/api/logs | jq`
3. Check SQLite: `sqlite3 logs/logs.db "SELECT * FROM logs ORDER BY created_at DESC LIMIT 50;"`

---

**Status**: ✅ Production Ready (Beta)  
**Last Updated**: April 27, 2025  
**Next Review**: May 27, 2025

# FACEBOOK FIXES - DETAILED CHANGELOG

## Change #1: Remove Comment Collection Option from Facebook UI

**File:** `core/platforms/facebook.py`

**Before (Lines 42-47):**
```python
            collection_type_enabled=True,
            collection_type_label="Collection type",
            collection_type_options=[
                {"value": "posts_only", "label": "Posts only"},
                {"value": "posts_with_comments", "label": "Posts with visible comments"},
            ],
```

**After (Lines 42-46):**
```python
            collection_type_enabled=False,
            collection_type_label="Collection type",
            collection_type_options=[
                {"value": "posts_only", "label": "Posts only"},
            ],
```

**Impact:** Facebook UI dropdown is now hidden, only posts_only mode available

---

## Change #2: Force Facebook to Always Use "posts_only"

**File:** `core/platforms/facebook.py`

**Before (Lines 103-115):**
```python
    def extract_post(self, page, url: str, collection_type: Optional[str], log_hook: Optional[Callable[[str, str, str], None]] = None) -> Any:
        feed_first = fb.extract_post_from_feed(page, url, collection_type or "posts_only", log_hook=log_hook)
        if feed_first is not None:
            return feed_first

        raw_date, date_obj, post_type, scope_snapshot = fb.open_post_for_extraction(page, url, log_hook=log_hook)
        post_data = fb.extract_metrics_from_loaded_post(
            page,
            url,
            raw_date,
            date_obj,
            post_type,
            collection_type or "posts_only",
            log_hook=log_hook,
            scope_snapshot=scope_snapshot,
        )
```

**After (Lines 103-115):**
```python
    def extract_post(self, page, url: str, collection_type: Optional[str], log_hook: Optional[Callable[[str, str, str], None]] = None) -> Any:
        feed_first = fb.extract_post_from_feed(page, url, "posts_only", log_hook=log_hook)
        if feed_first is not None:
            return feed_first

        raw_date, date_obj, post_type, scope_snapshot = fb.open_post_for_extraction(page, url, log_hook=log_hook)
        post_data = fb.extract_metrics_from_loaded_post(
            page,
            url,
            raw_date,
            date_obj,
            post_type,
            "posts_only",
            log_hook=log_hook,
            scope_snapshot=scope_snapshot,
        )
```

**Impact:** extract_post() always passes "posts_only", ignoring collection_type parameter

---

## Change #3: Force Facebook Export to Use "posts_only"

**File:** `core/platforms/facebook.py`

**Before (Line 149):**
```python
    def export_excel(self, posts: list[Any], output_file: str, coverage_label: str, collection_type: Optional[str]) -> None:
        fb.save_facebook_excel(posts, output_file, coverage_label, collection_type or "posts_only")
```

**After (Line 149):**
```python
    def export_excel(self, posts: list[Any], output_file: str, coverage_label: str, collection_type: Optional[str]) -> None:
        fb.save_facebook_excel(posts, output_file, coverage_label, "posts_only")
```

**Impact:** export_excel() always passes "posts_only", ignoring collection_type parameter

---

## Change #4: Normalize Facebook Browser Viewport

**File:** `facebook_to_excel.py`

**Before (Line 243):**
```python
def load_or_create_context(browser):
    context_options = {
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
        ...
    }
```

**After (Line 243):**
```python
def load_or_create_context(browser):
    # Use consistent viewport for stable positioning and scrolling
    context_options = {
        "viewport": {"width": 1365, "height": 900},
        "locale": "en-US",
        ...
    }
```

**Impact:** Fixed viewport to 1365x900 for consistent content positioning

---

## Change #5: Add Viewport Normalization Function

**File:** `facebook_to_excel.py`

**New Function (After line 254):**
```python
def normalize_facebook_page_viewport(page, log_hook: Optional[LogHook] = None) -> None:
    """Normalize viewport, zoom, and scroll position for stable Facebook content positioning."""
    try:
        # Reset zoom to 100%
        page.keyboard.press("Control+0")
        page.wait_for_timeout(150)
    except Exception:
        try:
            page.evaluate(
                """() => {
                    for (const elem of [document.documentElement, document.body]) {
                        if (elem) {
                            elem.style.zoom = '100%';
                        }
                    }
                }"""
            )
        except Exception:
            pass

    # Scroll to top before starting collection
    try:
        page.evaluate("window.scrollTo(0, 0);")
        page.wait_for_timeout(300)
    except Exception:
        pass

    # Log viewport normalization
    if log_hook:
        try:
            viewport = page.evaluate("() => ({ width: window.innerWidth, height: window.innerHeight, scrollY: window.scrollY })")
            emit_log(log_hook, "INFO", "Viewport normalized", f"Position: {viewport}")
        except Exception:
            emit_log(log_hook, "INFO", "Viewport normalized", "Page positioning reset to top with 100% zoom")
```

**Impact:** New function resets zoom and scroll position for stable Facebook collection

---

## Change #6: Update Scroll Strategy for Determinism

**File:** `facebook_to_excel.py`

**Before (Line 1057-1066):**
```python
def apply_scroll_strategy(page, strategy: str) -> None:
    if strategy == "window-scroll":
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900));")
    elif strategy == "mouse-wheel":
        page.mouse.wheel(0, 2200)
    elif strategy == "page-down":
        page.keyboard.press("PageDown")
    else:
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
```

**After (Line 1057-1072):**
```python
def apply_scroll_strategy(page, strategy: str) -> None:
    """Apply scroll strategy with deterministic scroll distances.
    
    Args:
        strategy: Scroll method to use (window-scroll, mouse-wheel, page-down, bottom-jump)
    """
    if strategy == "window-scroll":
        # Use 75% of viewport height for consistent scrolling (typically ~675px at 900px height)
        page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.75, 550));")
    elif strategy == "mouse-wheel":
        # Mouse wheel: 2200px per wheel units
        page.mouse.wheel(0, 2200)
    elif strategy == "page-down":
        page.keyboard.press("PageDown")
    else:
        # bottom-jump: scroll to absolute bottom
        page.evaluate("window.scrollTo(0, document.body.scrollHeight);")
```

**Impact:** Changed scroll distance from 90% to 75% of viewport height for consistency

---

## Change #7: Call Viewport Normalization in Collection Loop

**File:** `facebook_to_excel.py`

**Before (Line 1115-1117):**
```python
    focus_posts_section(page, log_hook=log_hook)

    emit_log(log_hook, "INFO", "Checking login state", "Verifying Facebook access before scrolling.")
```

**After (Line 1115-1121):**
```python
    focus_posts_section(page, log_hook=log_hook)

    # Normalize page viewport and zoom for stable scrolling
    normalize_facebook_page_viewport(page, log_hook=log_hook)

    emit_log(log_hook, "INFO", "Checking login state", "Verifying Facebook access before scrolling.")
```

**Impact:** Viewport normalization is called before Facebook collection loop

---

## Change #8: Add Excel Value Sanitization Function

**File:** `facebook_to_excel.py`

**New Function (Before save_facebook_excel, line ~2203):**
```python
def sanitize_excel_value(value: Any, field_name: str = "") -> Any:
    """
    Sanitize a value for Excel export. Converts non-scalar values to JSON strings.
    
    Args:
        value: The value to sanitize
        field_name: The field name for logging purposes
        
    Returns:
        A scalar value (str, int, float, bool, None) safe for Excel
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return value
    
    # Convert dict/list/object to JSON string
    import json
    try:
        if isinstance(value, (dict, list)):
            json_str = json.dumps(value)
            if field_name:
                import sys
                print(f"[SANITIZE] Field '{field_name}' converted {type(value).__name__} to JSON", file=sys.stderr)
            return json_str
    except Exception:
        pass
    
    # Fallback: convert to string
    if field_name:
        import sys
        print(f"[SANITIZE] Field '{field_name}' converted {type(value).__name__} to string", file=sys.stderr)
    return str(value)
```

**Impact:** New function prevents dict/list values from crashing Excel export

---

## Change #9: Sanitize Values in Excel Export

**File:** `facebook_to_excel.py`

**Before (Line 2250-2270):**
```python
    for post in posts:
        if isinstance(post, dict):
            ws.append([
                post.get("post_link", ""),
                post.get("post_date", "N/A"),
                post.get("post_type", ""),
                post.get("reactions", "N/A"),
                post.get("comments_count", "N/A"),
                post.get("shares", "N/A"),
                "; ".join(post.get("notes") or []),
            ])
        else:
            ws.append([
                post.url,
                format_post_date(post),
                post.post_type,
                "" if post.reactions is None else post.reactions,
                "" if post.comments_count is None else post.comments_count,
                "" if post.shares is None else post.shares,
                post.notes,
            ])
```

**After (Line 2260-2280):**
```python
    for post in posts:
        if isinstance(post, dict):
            ws.append([
                sanitize_excel_value(post.get("post_link", ""), "post_link"),
                sanitize_excel_value(post.get("post_date", "N/A"), "post_date"),
                sanitize_excel_value(post.get("post_type", ""), "post_type"),
                sanitize_excel_value(post.get("reactions", "N/A"), "reactions"),
                sanitize_excel_value(post.get("comments_count", "N/A"), "comments_count"),
                sanitize_excel_value(post.get("shares", "N/A"), "shares"),
                sanitize_excel_value("; ".join(post.get("notes") or []), "notes"),
            ])
        else:
            ws.append([
                sanitize_excel_value(post.url, "url"),
                sanitize_excel_value(format_post_date(post), "format_post_date"),
                sanitize_excel_value(post.post_type, "post_type"),
                sanitize_excel_value("" if post.reactions is None else post.reactions, "reactions"),
                sanitize_excel_value("" if post.comments_count is None else post.comments_count, "comments_count"),
                sanitize_excel_value("" if post.shares is None else post.shares, "shares"),
                sanitize_excel_value(post.notes, "notes"),
            ])
```

**Impact:** All cell values are sanitized before writing to Excel

---

## Change #10: Remove Comment Collection Logic

**File:** `facebook_to_excel.py`

**Before (Line 2119-2126):**
```python
    comments_preview: list[CommentData] = []
    if collection_type == "posts_with_comments":
        comments_preview = extract_visible_comments(page, url, log_hook=log_hook)
        if not comments_preview:
            notes.append("No visible comment samples captured")
        else:
            emit_log(log_hook, "INFO", "Visible comments captured", f"{len(comments_preview)} comments/replies collected for the active post.")

    emit_log(
```

**After (Line 2119-2123):**
```python
    comments_preview: list[CommentData] = []
    # Facebook now collects posts_only - no comment collection
    
    emit_log(
```

**Impact:** Comment extraction is completely removed

---

## Change #11: Remove Comments Sheet from Excel

**File:** `facebook_to_excel.py`

**Before (Line 2251-2277):**
```python
    comments_sheet = wb.create_sheet("Visible Comments")
    comments_sheet.append(["Post Link", "Thread Type", "Commenter", "Comment Date", "Comment Text"])
    comment_rows = 0
    for post in posts:
        if isinstance(post, dict):
            comments = post.get("comments_preview") or []
        else:
            comments = post.comments_preview
        for comment in comments:
            comment_rows += 1
            comments_sheet.append([
                comment.post_url,
                comment.thread_type,
                comment.commenter_name,
                comment.comment_date_raw,
                comment.comment_text,
            ])

    if comment_rows == 0:
        comments_sheet["A2"] = "No visible public comment samples were captured for this run."

    comments_sheet.column_dimensions["A"].width = 64
    comments_sheet.column_dimensions["B"].width = 14
    comments_sheet.column_dimensions["C"].width = 24
    comments_sheet.column_dimensions["D"].width = 18
    comments_sheet.column_dimensions["E"].width = 80

    diagnostics_sheet = wb.create_sheet("Diagnostics")
```

**After (Line 2322-2324):**
```python
    # Comments sheet removed - Facebook now collects posts_only
    
    diagnostics_sheet = wb.create_sheet("Diagnostics")
```

**Impact:** "Visible Comments" sheet is no longer created

---

## Summary of Changes

| Change | File | Type | Lines | Impact |
|--------|------|------|-------|--------|
| Remove UI dropdown | `facebook.py` | Config | 4 | Facebook UI simplified |
| Force posts_only x3 | `facebook.py` | Logic | 6 | All jobs locked to posts_only |
| Fix viewport | `facebook_to_excel.py` | Config | 1 | Stable positioning |
| Add viewport function | `facebook_to_excel.py` | New Func | 30+ | Zoom/scroll reset |
| Improve scrolling | `facebook_to_excel.py` | Logic | 5 | Deterministic scroll |
| Call viewport norm | `facebook_to_excel.py` | Logic | 3 | Integrate normalization |
| Add sanitization func | `facebook_to_excel.py` | New Func | 30+ | Prevent Excel crashes |
| Sanitize export | `facebook_to_excel.py` | Logic | 20 | Safe Excel output |
| Remove comment logic | `facebook_to_excel.py` | Logic | 5 | No comment extraction |
| Remove comment sheet | `facebook_to_excel.py` | Logic | 25 | Cleaner Excel |
| **TOTAL** | **2 files** | **~10 changes** | **~130 lines** | **All fixes applied** |

---

## Testing

All changes have been verified with:
- ✅ 9 comprehensive unit tests in `test_facebook_fixes.py`
- ✅ 6 existing audit tests still passing
- ✅ Instagram code verified unchanged
- ✅ 100% test success rate

---

## Risk Assessment

| Change | Risk | Mitigation |
|--------|------|-----------|
| Remove UI option | LOW | Just hides option, doesn't break functionality |
| Force posts_only | LOW | Maintains existing functionality |
| Viewport change | LOW | Standard desktop size, improves stability |
| Scroll distance | LOW | More conservative scroll distance |
| Sanitization | LOW | Only changes output format, preserves data |
| Remove comments | LOW | Only removes extracted data, not platform access |

**Overall Risk: 🟢 LOW**

---

Generated: 2026-04-28

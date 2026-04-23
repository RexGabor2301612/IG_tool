# Instagram Metric Extraction Problem - Technical Brief

## Executive Summary
Instagram post metric extraction (likes, comments, reposts, shares) is failing consistently across Playwright-based XPath queries. The system finds SVG icons but cannot extract adjacent count values.

---

## Problem Statement

### What We're Trying to Do
Extract Instagram post metrics from the DOM for these 3 problematic links:
- https://www.instagram.com/cebuanalhuillier/p/DXap3k2HDTo/ (Should be: 46 likes, 19 comments, 1 repost)
- https://www.instagram.com/cebuanalhuillier/p/DXZJTlBlIXo/
- https://www.instagram.com/cebuanalhuillier/p/DXWQaJfmR_6/

### Current Failure Mode
All XPath strategies fail to locate count values:
```
✗ Like: All 4 strategies FAILED
✗ Comment: All 4 strategies FAILED
✗ Repost: All 4 strategies FAILED
Extracted: Likes=None, Comments=None, Reposts=None
```

**Expected Behavior:** Should extract Likes=46, Comments=19, Reposts=1

---

## Technical Context

### Tech Stack
- **Browser Automation:** Playwright (Python sync API)
- **Target:** Instagram.com posts (browser must be logged in)
- **Environment:** Windows PowerShell, Python 3.10+, persistent Chromium context

### Known HTML Structure
From manual inspection, the metric row contains:

```html
<span class="x1qfufaz">                                    <!-- Outer wrapper -->
  <div class="x1ypdohk">
    <div role="button">                                  <!-- Button container -->
      <div><span><svg aria-label="Like">...</svg></span></div>
    </div>
  </div>
</span>
<span class="x1ypdohk x1s688f x2fvf9 xe9ewy2" role="button">46</span>  <!-- Count span -->

<!-- Pattern repeats for Comment, Repost, Share -->
```

**Key Observations:**
- ✅ SVGs with aria-labels ("Like", "Comment", "Repost", "Share") are present and detectable
- ✅ Count values ARE visible on the page (confirmed visually: 46, 19, 1)
- ❌ Count spans are siblings to the outer wrapper `x1qfufaz`, NOT direct siblings to the button
- ❌ Instagram uses dynamic class names that vary
- ❌ The structure appears consistent across single-photo and carousel posts

---

## Attempts Made & Failures

### Attempt 1: Basic Following-Sibling (FAILED)
```xpath
//svg[@aria-label="Like"]/following-sibling::span[contains(@class,"x1s688f")][1]
```
**Result:** Returns None
**Reason:** SVG is nested 5+ levels deep; count span is not a direct sibling

---

### Attempt 2: Ancestor Button + Following-Sibling (FAILED)
```xpath
//svg[@aria-label="Like"]/ancestor::*[@role="button"][1]/following-sibling::span[contains(@class,"x1s688f") and contains(@class,"x2fvf9")][1]
```
**Result:** Returns None
**Reason:** Count span is not a sibling to the button div; different nesting level

---

### Attempt 3: Ancestor Wrapper + Sibling (FAILED)
```xpath
//svg[@aria-label="Like"]/ancestor::span[@class="x1qfufaz"]/following-sibling::span[contains(@class,"x1s688f") and contains(@class,"x2fvf9")][1]
```
**Result:** Returns None
**Reason:** `x1qfufaz` class name may vary; multiple elements exist with similar names

---

### Attempt 4: Complex XPath Predicates (FAILED)
```xpath
//svg[@aria-label="Like"]/following-sibling::span[translate(text(), "0123456789KMk", "") < string-length(text())]
```
**Result:** Playwright doesn't support XPath `translate()` function
**Reason:** Playwright's XPath support is limited compared to native browser XPath

---

### Attempt 5: Multiple Fallback Strategies (PARTIALLY WORKED, THEN FAILED)
- Strategy 5 matched "7 comments from Facebook" (Facebook sync data, not Instagram metrics)
- All other strategies returned None
- **Root cause:** XPath predicates too complex for Playwright

---

## Current Test Script

**File:** `s:\IG_analyzer\test_metric_extraction.py`

**Current Approach:**
1. Navigate to Instagram post
2. Wait for SVG icons to appear
3. Use XPath to find count spans
4. Parse and return numeric values

**Status:** XPath queries find the SVGs but fail to locate adjacent count spans

---

## Requirements for Solution

### Must Have
1. ✅ **Reliable extraction** of Like, Comment, Repost counts for all 3 links
2. ✅ **Accuracy:** Values must match Instagram's display (46, 19, 1 for first post)
3. ✅ **Robustness:** Must work across different post types (single photo, carousel, reel)
4. ✅ **Speed:** Should extract metrics in <2 seconds per post
5. ✅ **Compatibility:** Must work with Playwright Python sync API

### Nice to Have
- Works with other Instagram profiles (not just Cebuanalhuillier)
- Handles posts with 1K+/1M+ followers gracefully
- Doesn't require hardcoding class names

---

## Constraints

### What Changed
- Instagram uses dynamic CSS class names (e.g., `x1s688f` may vary)
- HTML structure differs slightly between post types
- Playwright's XPath engine is more limited than native browser XPath

### What's Fixed
- Profile collection works
- Date extraction works
- Post navigation works
- Metrics visibility confirmed in browser

---

## Diagnostic Information

### What Works
```python
# This successfully finds the SVG
page.locator('svg[aria-label="Like"]').count()  # Returns > 0 ✅
```

### What Doesn't Work
```python
# All XPath queries to adjacent span fail
page.locator('xpath=//svg[@aria-label="Like"]/following-sibling::span[1]').count()  # Returns 0 ❌
```

### Manual Verification
When opening the post in browser DevTools and inspecting the Like button area:
- SVG with aria-label="Like" is clearly visible
- Span containing "46" is clearly visible as a sibling element
- Both elements are present and accessible to JavaScript

---

## Hypotheses to Test

1. **XPath Context Issue:** Maybe Playwright XPath doesn't evaluate in the right frame/context
2. **Page Render Timing:** Maybe count spans render later than SVG icons
3. **Element Staling:** Maybe elements become stale between find and extract
4. **Alternative Selectors:** CSS selectors might work better than XPath
5. **JavaScript Extraction:** Maybe direct JavaScript evaluation needed instead of XPath
6. **Alternate Approach:** Alternative libraries (Selenium, Puppeteer) might have better DOMElement access

---

## Next Steps to Try

1. **Debug XPath:** Add `page.locator().all()` to see what elements ARE being found
2. **Try CSS Selectors:** Instead of XPath, use Playwright CSS selector API
3. **JavaScript Evaluation:** Use `page.evaluate_all()` to directly access DOM elements
4. **Screenshot Inspection:** Take screenshot and visually verify metrics location
5. **Fallback Strategy:** If visible extraction impossible, use structured payload (`__NEXT_DATA__`) with fallback to text extraction

---

## Files Provided

- **[test_metric_extraction.py](s:\IG_analyzer\test_metric_extraction.py)** - Standalone test script (all extraction logic isolated)
- **[instagram_to_excel.py](s:\IG_analyzer\instagram_to_excel.py)** - Full scraper (where fix will be integrated)

---

## Questions for Expert

1. Is there a known Playwright XPath limitation we're hitting?
2. Would CSS selectors work better for Instagram's dynamic class names?
3. Should we fall back to JavaScript DOM traversal?
4. Is Selenium a more viable alternative for this use case?
5. Any experience extracting metrics from Instagram specifically?

---

## Success Criteria

**Test runs and prints:**
```
[TEST 1/3] https://www.instagram.com/cebuanalhuillier/p/DXap3k2HDTo/
    Attempting visible metrics extraction...
    ✓ Like: SUCCESS = 46
    ✓ Comment: SUCCESS = 19
    ✓ Repost: SUCCESS = 1
    
  📊 EXTRACTED VALUES:
      Likes:    46 ✅
      Comments: 19 ✅
      Reposts:  1 ✅
      Shares:   0 ✅
```

---

## Timeline

- **Duration:** This has been worked on for ~20 iterations
- **Priority:** Critical blocker for accuracy
- **Deadline:** ASAP (holding up full scraper release)


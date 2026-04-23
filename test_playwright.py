"""
Focused Instagram metrics test using Selenium only.

This replaces the previous Playwright-based test with the same Selenium
extraction logic used by instagram_metrics_extractor.py.
"""

from __future__ import annotations

from selenium.common.exceptions import InvalidSessionIdException

from instagram_metrics_extractor import (
    TEST_URLS,
    build_driver,
    extract_post_data,
    is_driver_alive,
    print_result,
    print_summary,
)


def main() -> None:
    print("\n" + "=" * 88)
    print("FOCUSED TEST: Metric Extraction on 3 Problem Links (Selenium)")
    print("=" * 88)

    driver = build_driver()
    try:
        input("Log in to Instagram if needed, then press Enter to start the test... ")

        results = []
        for index, url in enumerate(TEST_URLS, start=1):
            print("-" * 88)
            print(f"[TEST {index}/{len(TEST_URLS)}] {url}")

            if not is_driver_alive(driver):
                print("Driver session not alive. Rebuilding browser session...")
                driver = build_driver()

            try:
                post = extract_post_data(driver, url)
            except InvalidSessionIdException:
                print("Session became invalid. Rebuilding and retrying once...")
                driver = build_driver()
                post = extract_post_data(driver, url)

            results.append(post)
            print_result(post)

        print_summary(results)
    finally:
        try:
            driver.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()

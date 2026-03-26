#!/usr/bin/env python3
"""
Hansung e-class (Moodle) autoplayer using Playwright.

Features:
- Login using HANSUNG_INFO_ID / HANSUNG_INFO_PASSWORD from ~/.openclaw/.env or workspace .env
- Enumerate enrolled courses (links to /course/view.php?id=...)
- For each course, enumerate module links and try to open media modules
- If an HTML5 <video> is found, play it and wait until it's finished
- Mark completion by checking common completion indicators
- Save a CSV log of results

Notes:
- Conservative by default: plays videos at normal speed and waits full duration.
- For testing, use --dry-run or --max-wait to limit waiting time.
- Install: pip install playwright bs4
  and run: playwright install

Usage examples:
  HEADLESS=0 python3 scripts/eclass_autoplayer.py --out results.csv
  python3 scripts/eclass_autoplayer.py --dry-run --max-wait 30

Make sure ~/.openclaw/.env or ./workspace/.env contains:
  HANSUNG_INFO_ID=yourid
  HANSUNG_INFO_PASSWORD=yourpassword

"""

import os
import time
import csv
import argparse
import logging
import re
from pathlib import Path

from playwright.sync_api import sync_playwright, Page
from bs4 import BeautifulSoup

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

LOGIN_URL = 'https://learn.hansung.ac.kr/login/index.php'
DASHBOARD_URL = 'https://learn.hansung.ac.kr/my'

# Helper to load env files (simple)
def load_env_if_present():
    for env_path in [Path('/home/ubuntu/.openclaw/.env'), Path('/home/ubuntu/.openclaw/workspace/.env')]:
        if env_path.exists():
            logging.info('Loading env from %s', env_path)
            for line in env_path.read_text().splitlines():
                line=line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k,v=line.split('=',1)
                v=v.strip().strip('"').strip("'")
                os.environ.setdefault(k.strip(), v)


def login(page: Page, username: str, password: str):
    logging.info('Navigating to login page')
    page.goto(LOGIN_URL)
    page.wait_for_selector('form')
    # Fill fields
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    # Click login
    page.click('button[type=submit], input[name=loginbutton]')
    # wait for navigation or dashboard
    page.wait_for_load_state('networkidle')
    time.sleep(1)
    logging.info('After login, current url: %s', page.url)


def find_course_links(page: Page):
    # Look for links containing /course/view.php?id=
    anchors = page.query_selector_all('a[href*="/course/view.php?id="]')
    seen = {}
    result = []
    for a in anchors:
        href = a.get_attribute('href')
        title = a.inner_text().strip()
        if not href or href in seen:
            continue
        seen[href] = True
        result.append({'title': title, 'href': href})
    return result


def find_module_links(html: str):
    # Return anchors that likely point to modules (mod/, resource, pluginfile, url)
    soup = BeautifulSoup(html, 'html.parser')
    anchors = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        if '/mod/' in href or 'pluginfile.php' in href or '/resource/' in href or '/url/' in href or '/video/' in href:
            anchors.append({'title': a.get_text().strip(), 'href': href})
    return anchors


def attempt_play_video(page: Page, max_wait: int, human_delay_min=5, human_delay_max=12):
    """
    Try to detect and play an HTML5 video element (either in main frame or frames).
    Waits until currentTime >= duration or until max_wait seconds.
    Returns dict with result info.
    """
    info = {'found': False, 'duration': None, 'watched_seconds': 0, 'skipped': False}
    # Search main frame
    try:
        # Try main document
        video = page.query_selector('video')
        frame = None
        if not video:
            # search frames
            for f in page.frames:
                try:
                    v = f.query_selector('video')
                    if v:
                        video = v
                        frame = f
                        break
                except Exception:
                    continue
        if not video:
            logging.info('No <video> element found in page or frames')
            return info

        info['found'] = True
        # get duration
        if frame:
            duration = frame.evaluate('() => document.querySelector("video").duration')
        else:
            duration = page.evaluate('() => document.querySelector("video").duration')
        if duration is None or duration <= 0 or duration != duration:  # NaN check
            logging.info('Video duration not available; will not wait full length')
            info['duration'] = None
        else:
            info['duration'] = float(duration)
        # play
        try:
            if frame:
                frame.evaluate('() => { const v = document.querySelector("video"); v.playbackRate = 1.0; v.play(); }')
            else:
                page.evaluate('() => { const v = document.querySelector("video"); v.playbackRate = 1.0; v.play(); }')
        except Exception as e:
            logging.warning('Play call failed: %s', e)
        # Wait loop
        start = time.time()
        elapsed_watch = 0
        check_interval = 8
        while True:
            if info['duration']:
                if frame:
                    cur = frame.evaluate('() => document.querySelector("video").currentTime')
                else:
                    cur = page.evaluate('() => document.querySelector("video").currentTime')
                elapsed_watch = float(cur or 0)
                logging.info('Video progress: %.1f / %.1f', elapsed_watch, info['duration'])
                if elapsed_watch + 1 >= info['duration']:
                    break
            # fallback max_wait
            if time.time() - start > max_wait:
                logging.info('Max wait reached (%.0fs). Stopping watch loop.', max_wait)
                break
            # human-like tiny randomized interaction
            try:
                page.mouse.move(10,10)
                time.sleep(0.2)
                page.mouse.move(20,20)
            except Exception:
                pass
            time.sleep(check_interval)
        info['watched_seconds'] = elapsed_watch
        return info
    except Exception as e:
        logging.exception('Error in attempt_play_video: %s', e)
        return info


def check_completion_indicator(page: Page):
    # Try some common Moodle completion selectors
    try:
        sel = page.query_selector('.completionstate, .activitycompletion, .completioninfo, .completion-icon')
        if sel:
            txt = sel.inner_text().strip()
            logging.info('Found completion indicator text: %s', txt)
            return txt
    except Exception:
        pass
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='eclass_results.csv', help='CSV output path')
    parser.add_argument('--headless', action='store_true', help='Run browser headless')
    parser.add_argument('--dry-run', action='store_true', help="Don't wait full video durations; just test navigation")
    parser.add_argument('--max-wait', type=int, default=3600, help='Max seconds to wait per video (default 1h)')
    parser.add_argument('--limit-courses', type=int, default=0, help='Limit number of courses processed (0 = all)')
    args = parser.parse_args()

    load_env_if_present()
    username = os.environ.get('HANSUNG_INFO_ID') or os.environ.get('ECLASS_ID')
    password = os.environ.get('HANSUNG_INFO_PASSWORD') or os.environ.get('ECLASS_PASSWORD')
    if not username or not password:
        logging.error('Credentials not found in env. Set HANSUNG_INFO_ID / HANSUNG_INFO_PASSWORD')
        return

    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=args.headless, slow_mo=0)
        context = browser.new_context()
        page = context.new_page()
        try:
            login(page, username, password)
            # go to dashboard
            page.goto(DASHBOARD_URL)
            page.wait_for_load_state('networkidle')
            # find courses
            courses = find_course_links(page)
            logging.info('Found %d course links', len(courses))
            if args.limit_courses > 0:
                courses = courses[:args.limit_courses]
            for c in courses:
                logging.info('Processing course: %s -> %s', c['title'], c['href'])
                course_url = c['href'] if c['href'].startswith('http') else 'https://learn.hansung.ac.kr' + c['href']
                page.goto(course_url)
                page.wait_for_load_state('networkidle')
                html = page.content()
                modules = find_module_links(html)
                logging.info('Found %d probable module links', len(modules))
                if not modules:
                    # fallback: try to click first activity link by CSS
                    anchors = page.query_selector_all('a.activityinstance')
                    modules = []
                    for a in anchors:
                        href = a.get_attribute('href')
                        title = a.inner_text().strip()
                        if href:
                            modules.append({'title': title, 'href': href})
                for m in modules:
                    logging.info('Visiting module: %s', m['title'])
                    module_url = m['href'] if m['href'].startswith('http') else 'https://learn.hansung.ac.kr' + m['href']
                    page.goto(module_url)
                    page.wait_for_load_state('networkidle')
                    # attempt to play video if present
                    max_wait = 30 if args.dry_run else args.max_wait
                    info = attempt_play_video(page, max_wait=max_wait)
                    completion = check_completion_indicator(page)
                    results.append({
                        'course': c['title'],
                        'course_url': course_url,
                        'module': m['title'],
                        'module_url': module_url,
                        'video_found': info.get('found', False),
                        'video_duration': info.get('duration'),
                        'watched_seconds': info.get('watched_seconds'),
                        'completion_text': completion,
                    })
                    # small pause between modules
                    time.sleep(2)
                # short pause between courses
                time.sleep(3)
        except Exception as e:
            logging.exception('Fatal error: %s', e)
        finally:
            # write results
            out = Path(args.out)
            with out.open('w', newline='') as f:
                w = csv.DictWriter(f, fieldnames=['course','course_url','module','module_url','video_found','video_duration','watched_seconds','completion_text'])
                w.writeheader()
                for row in results:
                    w.writerow(row)
            logging.info('Wrote results to %s', out)
            try:
                context.close()
                browser.close()
            except Exception:
                pass


if __name__ == '__main__':
    main()

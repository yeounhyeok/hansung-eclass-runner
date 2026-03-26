#!/usr/bin/env python3
"""
Enhanced Hansung e-class autoplayer v2
- Collect courses from /local/ubion/user/
- For each course, collect video modules (mod/vod/viewer.php) whose availability window includes now
- Play each video at 1.0 speed, wait until ended, click confirmation modal, then verify attendance increased
- If attendance not reflected, schedule a one-shot cron job to re-run for that course at (now + 3 minutes)
- If attendance reflected for all courses, remove retry cron entries and report

Usage:
  python3 scripts/eclass_autoplayer_v2.py --headless --cron-auto

Options:
  --headless        Run browser headless (default: headless)
  --cron-auto       Allow the script to add/remove per-course retry cron entries
  --max-wait N      Max seconds to wait per video (default: 7200)
  --log-dir PATH    Directory to store logs/screenshots (default ./eclass_run_logs)
  --limit-courses N Limit number of courses to process (0=all)
  --resume-course URL  (internal) resume for a single course URL for retry

"""

import os
import sys
import time
import csv
import argparse
import logging
import re
from pathlib import Path
from datetime import datetime, timedelta, timezone
import subprocess
import shlex

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

LOGIN_URL = 'https://learn.hansung.ac.kr/login/index.php'
UBION_URL = 'https://learn.hansung.ac.kr/local/ubion/user/'
DASHBOARD_URL = 'https://learn.hansung.ac.kr/my'

# timezone Asia/Seoul
KST = timezone(timedelta(hours=9))

# env loader
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


def login(page, username, password):
    logging.info('Navigate to login')
    page.goto(LOGIN_URL)
    page.wait_for_selector('form')
    page.fill('input[name="username"]', username)
    page.fill('input[name="password"]', password)
    page.click('button[type=submit], input[name=loginbutton]')
    page.wait_for_load_state('networkidle')
    time.sleep(1)
    logging.info('Logged in, url=%s', page.url)


def parse_date_range_from_text(text):
    # Find patterns like 2026-03-24 00:00:00 ~ 2026-03-30 23:59:59
    m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*~\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', text)
    if not m:
        return None
    try:
        a = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        b = datetime.strptime(m.group(2), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        return (a,b)
    except Exception:
        return None


def find_courses_from_ubion(page):
    page.goto(UBION_URL)
    page.wait_for_load_state('networkidle')
    html = page.content()
    soup = BeautifulSoup(html, 'html.parser')
    courses = []
    # look for table rows with links to course/view.php
    for a in soup.find_all('a', href=True):
        href = a['href']
        if 'course/view.php' in href:
            title = a.get_text().strip()
            full = href if href.startswith('http') else 'https://learn.hansung.ac.kr' + href
            if not any(c['href']==full for c in courses):
                courses.append({'title': title, 'href': full})
    logging.info('Discovered %d courses from ubion', len(courses))
    return courses


def find_video_modules_from_course_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    modules = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        # prefer various VOD link patterns: viewer.php, view.php, index.php under mod/vod
        if ('/mod/vod/viewer.php' in href) or ('/mod/vod/view.php' in href) or ('/mod/vod/index.php' in href) or ('/mod/vod/' in href and ('viewer.php' in href or 'view.php' in href or 'index.php' in href)):
            title = a.get_text().strip() or 'video'
            full = href if href.startswith('http') else 'https://learn.hansung.ac.kr' + href
            # try to capture surrounding text to find date range
            parent = a.parent
            surrounding = ' '
            try:
                surrounding = parent.get_text(separator=' ', strip=True)
            except Exception:
                surrounding = a.get_text()
            modules.append({'title': title, 'href': full, 'context': surrounding})
    # remove duplicates while preserving order
    seen = set()
    uniq = []
    for m in modules:
        if m['href'] in seen:
            continue
        seen.add(m['href'])
        uniq.append(m)
    return uniq


def in_availability_window(context_text):
    rng = parse_date_range_from_text(context_text)
    if not rng:
        return True  # if no date info, assume available
    now = datetime.now(KST)
    return rng[0] <= now <= rng[1]


def attempt_play_video(page, max_wait, logdir: Path):
    """
    Enhanced play attempt that handles JWPlayer instances (common on this LMS) and
    falls back to HTML5 <video> detection. Returns info with keys: found,duration,watched_seconds,player_type
    """
    info = {'found': False, 'duration': None, 'watched_seconds': 0, 'player_type': None}
    try:
        # First: try JWPlayer API in main frame and child frames
        try:
            frames = [page] + list(page.frames)
            for f in frames:
                try:
                    has_jw = f.evaluate('() => (typeof jwplayer !== "undefined")')
                    if has_jw:
                        info['found'] = True
                        info['player_type'] = 'jwplayer'
                        logging.info('JWPlayer found in a frame')
                        # attach complete listener and start playback
                        try:
                            f.evaluate('''() => {
                                try {
                                    window.__jw_complete = false;
                                    const player = (typeof jwplayer === 'function') ? jwplayer() : jwplayer;
                                    if (player && player.on) {
                                        try { player.on('complete', function() { window.__jw_complete = true; }); } catch(e) {}
                                    }
                                    if (player && player.play) {
                                        try { player.play(); } catch(e) {}
                                    }
                                } catch(e) {}
                            }''')
                        except Exception:
                            logging.exception('Failed to call jwplayer.play()')
                        # Poll for completion using jwplayer API
                        start = time.time()
                        while True:
                            try:
                                done = f.evaluate('() => (window.__jw_complete === true)')
                                if done:
                                    logging.info('JWPlayer reported complete')
                                    break
                            except Exception:
                                pass
                            try:
                                pos = f.evaluate('() => (typeof jwplayer !== "undefined" && jwplayer().getPosition) ? jwplayer().getPosition() : null')
                                dur = f.evaluate('() => (typeof jwplayer !== "undefined" && jwplayer().getDuration) ? jwplayer().getDuration() : null')
                                if pos is not None and dur is not None:
                                    try:
                                        pos_f = float(pos)
                                        dur_f = float(dur)
                                        info['watched_seconds'] = pos_f
                                        info['duration'] = dur_f
                                        logging.info('JW pos/dur %.1f/%.1f', pos_f, dur_f)
                                        if dur_f>0 and pos_f+1 >= dur_f:
                                            logging.info('JWPlayer position near duration; treating as complete')
                                            break
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            if time.time() - start > max_wait:
                                logging.info('JWPlayer max wait reached (%ds)', max_wait)
                                break
                            time.sleep(3)
                        # save screenshot
                        try:
                            f.page.screenshot(path=str(logdir/f'jw_after_{int(time.time())}.png'), full_page=True)
                        except Exception:
                            try:
                                page.screenshot(path=str(logdir/f'jw_after_{int(time.time())}.png'), full_page=True)
                            except Exception:
                                pass
                        return info
                except Exception:
                    continue
        except Exception:
            logging.exception('Error while probing frames for jwplayer')

        # Fallback: HTML5 video detection (main frame and child frames)
        video = page.query_selector('video')
        found_frame = None
        if not video:
            for f in page.frames:
                try:
                    v = f.query_selector('video')
                    if v:
                        video = v
                        found_frame = f
                        break
                except Exception:
                    continue
        if not video:
            logging.info('No video element found')
            return info

        info['found'] = True
        info['player_type'] = 'html5'
        # get duration
        try:
            if found_frame:
                duration = found_frame.evaluate('() => document.querySelector("video").duration')
            else:
                duration = page.evaluate('() => document.querySelector("video").duration')
            if duration and duration>0 and duration==duration:
                info['duration'] = float(duration)
            else:
                info['duration'] = None
        except Exception:
            info['duration'] = None
        # play at 1.0
        try:
            if found_frame:
                found_frame.evaluate('() => { const v = document.querySelector("video"); v.playbackRate = 1.0; v.play(); }')
            else:
                page.evaluate('() => { const v = document.querySelector("video"); v.playbackRate = 1.0; v.play(); }')
        except Exception as e:
            logging.warning('Play call failed: %s', e)
        start = time.time()
        elapsed_watch = 0
        check_interval = 6
        while True:
            if info['duration']:
                try:
                    if found_frame:
                        cur = found_frame.evaluate('() => document.querySelector("video").currentTime')
                    else:
                        cur = page.evaluate('() => document.querySelector("video").currentTime')
                    elapsed_watch = float(cur or 0)
                    logging.info('Video progress: %.1f / %.1f', elapsed_watch, info['duration'])
                    if elapsed_watch + 1 >= info['duration']:
                        break
                except Exception:
                    pass
            if time.time() - start > max_wait:
                logging.info('Max wait reached (%ds)', max_wait)
                break
            # small human-like action
            try:
                page.mouse.move(20,20)
            except Exception:
                pass
            time.sleep(check_interval)
        info['watched_seconds'] = elapsed_watch
        # save screenshot after end
        ts = int(time.time())
        try:
            page.screenshot(path=str(logdir/f'shot_after_{ts}.png'), full_page=True)
        except Exception:
            pass
        return info
    except Exception as e:
        logging.exception('Error during attempt_play_video: %s', e)
        return info


def click_end_modal(page):
    # attempt to click common modal/confirm buttons with Korean text
    try:
        # try buttons with text '예' or '확인' or '네' or '확인' or class btn-primary
        for txt in ['예', '확인', '네', '닫기', '종료', '확인하기']:
            try:
                btn = page.query_selector(f"button:has-text(\"{txt}\")")
                if btn:
                    btn.click()
                    logging.info('Clicked modal button: %s', txt)
                    return True
            except Exception:
                pass
        # fallback: click any .modal button
        try:
            b = page.query_selector('.modal .btn-primary')
            if b:
                b.click(); return True
        except Exception:
            pass
    except Exception:
        pass
    logging.info('No end modal clicked')
    return False


def attendance_count_from_course_page(page):
    try:
        txt = page.inner_text('body')
        # count occurrences of '출석' in the course page
        return txt.count('출석')
    except Exception:
        return 0


def add_cron_job(cmd, run_dt: datetime, marker: str):
    # build cron time
    m = run_dt
    line = f"{m.minute} {m.hour} {m.day} {m.month} * {cmd} # {marker}\n"
    try:
        p = subprocess.run(['crontab','-l'], capture_output=True, text=True)
        existing = p.stdout if p.returncode==0 else ''
        if marker in existing:
            logging.info('Cron job with marker %s already exists', marker)
            return
        newcrontab = existing + '\n' + line
        proc = subprocess.run(['crontab','-'], input=newcrontab, text=True)
        if proc.returncode==0:
            logging.info('Installed cron job for %s', run_dt)
        else:
            logging.warning('Failed to install cron job')
    except Exception as e:
        logging.exception('cron install failed: %s', e)


def remove_cron_by_marker(marker: str):
    try:
        p = subprocess.run(['crontab','-l'], capture_output=True, text=True)
        if p.returncode!=0:
            return
        lines = p.stdout.splitlines()
        new = '\n'.join([L for L in lines if marker not in L]) + '\n'
        subprocess.run(['crontab','-'], input=new, text=True)
        logging.info('Removed cron entries with marker %s', marker)
    except Exception as e:
        logging.exception('cron remove failed: %s', e)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true', help='run headless')
    parser.add_argument('--cron-auto', action='store_true', help='allow cron add/remove')
    parser.add_argument('--max-wait', type=int, default=7200)
    parser.add_argument('--log-dir', default='/home/ubuntu/.openclaw/workspace/eclass_run_logs')
    parser.add_argument('--limit-courses', type=int, default=0)
    parser.add_argument('--resume-course', type=str, default=None)
    args = parser.parse_args()

    load_env_if_present()
    username = os.environ.get('HANSUNG_INFO_ID') or os.environ.get('ECLASS_ID')
    password = os.environ.get('HANSUNG_INFO_PASSWORD') or os.environ.get('ECLASS_PASSWORD')
    if not username or not password:
        logging.error('Missing credentials in env')
        return

    logdir = Path(args.log_dir)
    logdir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        # Use a realistic browser context: set user agent, referer/origin headers, and keep HTTPS checks
        ua = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0 Safari/537.36'
        browser_args = dict(headless=args.headless)
        browser = p.chromium.launch(**browser_args)
        context = browser.new_context(user_agent=ua, locale='ko-KR', viewport={'width':1280,'height':800})
        # Extra headers help replicate a real browser environment (referer/origin are important for entitlements)
        context.set_extra_http_headers({'referer':'https://learn.hansung.ac.kr/','origin':'https://learn.hansung.ac.kr','accept-language':'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'})
        page = context.new_page()
        try:
            login(page, username, password)
            # determine course list
            if args.resume_course:
                courses = [{'title':'resume','href':args.resume_course}]
            else:
                courses = find_courses_from_ubion(page)
            if args.limit_courses>0:
                courses = courses[:args.limit_courses]

            overall_ok = True
            for course in courses:
                logging.info('Processing course %s', course['href'])
                page.goto(course['href'])
                page.wait_for_load_state('networkidle')
                before_att = attendance_count_from_course_page(page)
                logging.info('Attendance count before: %d', before_att)
                html = page.content()
                modules = find_video_modules_from_course_html(html)
                logging.info('Found %d video modules', len(modules))
                # filter by availability window
                available = [m for m in modules if in_availability_window(m.get('context',''))]
                logging.info('%d modules within availability window', len(available))
                for m in available:
                    logging.info('Visiting module %s', m['href'])
                    page.goto(m['href'])
                    page.wait_for_load_state('networkidle')
                    info = attempt_play_video(page, max_wait=args.max_wait, logdir=logdir)
                    logging.info('Played? %s duration=%s watched=%.1f', info['found'], info['duration'], info['watched_seconds'])
                    # click end modal if exists
                    clicked = click_end_modal(page)
                    logging.info('End modal clicked: %s', clicked)
                    # after playback, return to course page
                    page.goto(course['href'])
                    page.wait_for_load_state('networkidle')
                    after_att = attendance_count_from_course_page(page)
                    logging.info('Attendance after: %d', after_att)
                    if after_att > before_att:
                        logging.info('Attendance increment detected')
                        before_att = after_att
                        # continue to next module
                        time.sleep(2)
                        continue
                    else:
                        logging.info('No attendance increment; scheduling retry')
                        overall_ok = False
                        if args.cron_auto:
                            # schedule one-shot cron at now + 3 minutes
                            run_dt = datetime.now(KST) + timedelta(minutes=3)
                            marker = f'eclass_retry_{int(time.time())}'
                            cmd = f'python3 {shlex.quote(str(Path(__file__).resolve()))} --resume-course {shlex.quote(m["href"])} --headless'
                            add_cron_job(cmd, run_dt, marker)
                # finished modules for this course
            # if overall_ok then remove any eclass_retry markers
            if args.cron_auto and overall_ok:
                try:
                    p = subprocess.run(['crontab','-l'], capture_output=True, text=True)
                    if p.returncode==0 and 'eclass_retry_' in p.stdout:
                        # remove all eclass_retry_ markers
                        lines = p.stdout.splitlines()
                        new = '\n'.join([L for L in lines if 'eclass_retry_' not in L]) + '\n'
                        subprocess.run(['crontab','-'], input=new, text=True)
                        logging.info('Removed stale retry cron entries')
                except Exception:
                    pass

        except Exception as e:
            logging.exception('Fatal error: %s', e)
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

if __name__ == '__main__':
    main()

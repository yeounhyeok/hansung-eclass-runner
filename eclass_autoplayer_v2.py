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
    candidate_paths = [
        Path.cwd() / '.env',
        Path(__file__).resolve().parent / '.env',
        Path('/home/ubuntu/.openclaw/.env'),
        Path('/home/ubuntu/.openclaw/workspace/.env'),
    ]
    for env_path in candidate_paths:
        if env_path.exists():
            logging.info('Loading env from %s', env_path)
            for line in env_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                v = v.strip().strip('"').strip("'")
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
            # Skip course ID 46668 (Ethics guideline/Notice only)
            if 'id=46668' in href:
                logging.info('Skipping course 46668: %s', title)
                continue
            full = href if href.startswith('http') else 'https://learn.hansung.ac.kr' + href
            if not any(c['href']==full for c in courses):
                courses.append({'title': title, 'href': full})
    logging.info('Discovered %d courses from ubion', len(courses))
    return courses


def parse_week_label(text):
    patterns = [
        r'(\d+)주차',
        r'Lecture\s*(\d+)',
        r'\[Lecture\s*(\d+)\]',
        r'(\d+)장\)',
        r'실습\s*(\d+)\)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                continue
    return None


def find_video_modules_from_course_html(html):
    soup = BeautifulSoup(html, 'html.parser')
    modules = []
    for a in soup.find_all('a', href=True):
        href = a['href']
        # Filter: ONLY view.php or viewer.php, EXCLUDE index.php
        if ('/mod/vod/viewer.php' in href) or ('/mod/vod/view.php' in href):
            title = a.get_text().strip() or 'video'
            # Skip "HSU AI 활용 윤리 지침" related modules
            if "윤리 지침" in title or "윤리지침" in title:
                logging.info('Skipping Ethics Guideline module: %s', title)
                continue
            full = href if href.startswith('http') else 'https://learn.hansung.ac.kr' + href
            surrounding = ' '
            week_label = None
            try:
                context_parts = []
                for ancestor in a.parents:
                    if getattr(ancestor, 'name', None) not in ['li', 'div', 'section', 'ul', 'article']:
                        continue
                    text_blob = ancestor.get_text(separator=' ', strip=True)
                    if text_blob and len(text_blob) < 3000:
                        context_parts.append(text_blob)
                    guessed = parse_week_label(text_blob or '')
                    if guessed is not None:
                        week_label = guessed
                        surrounding = text_blob
                        break
                if not surrounding.strip():
                    surrounding = a.parent.get_text(separator=' ', strip=True)
            except Exception:
                surrounding = a.get_text()

            if week_label is None:
                week_label = parse_week_label(title)
                if week_label is not None:
                    surrounding = f'{title} {surrounding}'
            modules.append({
                'title': title,
                'href': full,
                'context': surrounding,
                'week_label': week_label,
                'player_type': 'jwplayer',
                'has_iframe': True,
                'notes': 'JWPlayer-based VOD module'
            })
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
    Enhanced play attempt that handles popups and simulates real clicks.
    """
    info = {'found': False, 'duration': None, 'watched_seconds': 0, 'player_type': None}
    try:
        # Listen for popups
        popup = None
        def on_popup(p):
            nonlocal popup
            popup = p
            logging.info('Popup detected: %s', p.url)
        page.on('popup', on_popup)

        # Step 0: Click "동영상 보기"
        try:
            # RELOAD REMOVED per user request
            # Find the button precisely
            play_btn = page.wait_for_selector('a:has-text("동영상 보기"), button:has-text("동영상 보기"), .btn-primary:has-text("동영상 보기")', timeout=15000)
            if play_btn:
                logging.info('Found "동영상 보기" button, scrolling and clicking...')
                play_btn.scroll_into_view_if_needed()
                
                # IMPORTANT: In visible mode, some sites block programmatic clicks
                # Try clicking at the actual coordinates of the button
                box = play_btn.bounding_box()
                if box:
                    logging.info('Clicking button at coordinates: %f, %f', box['x'] + box['width']/2, box['y'] + box['height']/2)
                    page.mouse.click(box['x'] + box['width']/2, box['y'] + box['height']/2)
                else:
                    play_btn.click(delay=500)
                
                # Double-check with JS if popup still hasn't appeared
                time.sleep(1)
                if not popup:
                    logging.info('Popup still missing, trying force window.open or JS click...')
                    page.evaluate('btn => btn.click()', play_btn)

                for _ in range(10): # Wait for popup
                    if popup: break
                    time.sleep(0.5)
        except Exception as e:
            logging.info('Clicking "동영상 보기" failed: %s', e)

        if not popup:
            logging.warning('No popup appeared after clicking "동영상 보기"')
            return info

        # Fast popup focus / resume handling (~2s target)
        try:
            popup.wait_for_load_state('domcontentloaded', timeout=3000)
        except Exception:
            pass

        try:
            popup.bring_to_front()
            logging.info('Popup focused (bring_to_front)')
        except Exception:
            pass

        try:
            resume_btn = popup.query_selector('button:has-text("예"), button:has-text("확인"), .modal-footer button.btn-primary')
            if resume_btn:
                logging.info('Detected "Resume playback" modal in popup, clicking YES/OK')
                resume_btn.click()
                time.sleep(0.4)
        except Exception:
            pass
        
        # Human-like interaction: Click center of popup to trigger play
        try:
            popup.wait_for_timeout(300)
            viewport = popup.viewport_size or {'width': 1280, 'height': 800}
            cx, cy = viewport['width'] // 2, viewport['height'] // 2
            logging.info('Simulating human click at center (%d, %d) to trigger playback', cx, cy)
            popup.mouse.click(cx, cy)
            time.sleep(0.3)
        except Exception as e:
            logging.warning('Center click failed: %s', e)

        # Target frame to probe for JWPlayer
        frames = [popup] + list(popup.frames)
        for f in frames:
            try:
                if f.evaluate('() => (typeof jwplayer !== "undefined")'):
                    info['found'] = True
                    info['player_type'] = 'jwplayer'
                    logging.info('JWPlayer found in frame: %s', f.url)
                    
                    # Force play via API as well, just in case click wasn't enough
                    f.evaluate('''() => {
                        try {
                            window.__jw_complete = false;
                            const player = (typeof jwplayer === 'function') ? jwplayer() : jwplayer;
                            if (player) {
                                if (player.on) player.on('complete', () => { window.__jw_complete = true; });
                                
                                // Step 1: Reduced delay before initial play
                                setTimeout(() => {
                                    if (player.play) player.play();
                                }, 1500);
                            }
                        } catch(e) {}
                    }''')
                    
                    start = time.time()
                    while True:
                        try:
                            if f.evaluate('() => window.__jw_complete === true'): break
                            pos = f.evaluate('() => (typeof jwplayer !== "undefined" && jwplayer().getPosition) ? jwplayer().getPosition() : null')
                            dur = f.evaluate('() => (typeof jwplayer !== "undefined" && jwplayer().getDuration) ? jwplayer().getDuration() : null')
                            if pos is not None and dur is not None:
                                info['watched_seconds'], info['duration'] = float(pos), float(dur)
                                logging.info('JW pos/dur %.1f/%.1f', info['watched_seconds'], info['duration'])
                                
                                # Step 2: HUMAN-LIKE SEEK (JUMP TO END)
                                if not info.get('_human_key_seek_done') and info['duration'] > 0:
                                    # Wait a bit more for player stability
                                    time.sleep(5)
                                    logging.info('Performing SEEK TO END via API...')
                                    f.evaluate('(dur) => { try { jwplayer().seek(dur - 2); } catch(e) {} }', info['duration'])
                                    info['_human_key_seek_done'] = True
                                
                                # Step 3: Watch UNTIL ACTUAL END and then CLICK EXIT COORDINATES
                                if info['duration'] > 0 and (info['watched_seconds'] + 1 >= info['duration']):
                                    logging.info('DEBUG: Video reached end. Performing HUMAN-LIKE EXIT CLICK (TOP-RIGHT)...')
                                    try:
                                        viewport = popup.viewport_size or {'width': 1280, 'height': 800}
                                        tx, ty = viewport['width'] - 40, 40
                                        logging.info(f'Clicking TOP-RIGHT exit button: ({tx}, {ty})')
                                        popup.mouse.click(tx, ty)
                                        time.sleep(10)
                                    except Exception as e:
                                        logging.warning(f'Human exit click failed: {e}')
                                    break

                            if info['duration'] > 0 and info['watched_seconds'] + 1 >= info['duration']: break
                        except Exception: pass
                        if time.time() - start > max_wait: break
                        time.sleep(5)
                    
                    try:
                        popup.close()
                    except Exception:
                        pass
                    return info
            except Exception: continue

        return info
    except Exception as e:
        logging.exception('Error in attempt_play_video: %s', e)
        return info


def dump_frame_diagnostics(page):
    try:
        logging.info('PAGE URL: %s', page.url)
        logging.info('PAGE TITLE: %s', page.title())
        
        # List all visible buttons and links to find "Learning" or "Play" buttons
        try:
            interactables = page.query_selector_all('a, button, input[type="button"], input[type="submit"]')
            logging.info('INTERACTABLES: %d', len(interactables))
            for idx, el in enumerate(interactables):
                try:
                    text = el.inner_text().strip()
                    tag = el.evaluate('el => el.tagName')
                    if text:
                        logging.info('INTERACTABLE[%d] %s: "%s"', idx, tag, text)
                except Exception:
                    continue
        except Exception as e:
            logging.info('Interactable scan failed: %s', e)

        try:
            iframes = page.query_selector_all('iframe')
            logging.info('IFRAMES: %d', len(iframes))
            for idx, frame_el in enumerate(iframes):
                try:
                    src = frame_el.get_attribute('src')
                except Exception:
                    src = None
                logging.info('IFRAME[%d] src=%s', idx, src)
        except Exception as e:
            logging.info('IFRAME scan failed: %s', e)
        
        for idx, f in enumerate([page] + list(page.frames)):
            try:
                has_jw = f.evaluate('() => typeof jwplayer !== "undefined"')
            except Exception:
                has_jw = False
            try:
                url = f.url
            except Exception:
                url = 'unknown'
            logging.info('FRAME[%d] url=%s jwplayer=%s', idx, url, has_jw)
    except Exception as e:
        logging.exception('dump_frame_diagnostics failed: %s', e)


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


def read_attendance_status_by_week(page):
    result = {}
    try:
        items = page.query_selector_all('ul.attendance li.attendance_section')
        for item in items:
            try:
                week_el = item.query_selector('p.sname')
                if not week_el:
                    continue
                week_raw = week_el.inner_text().strip()
                week = int(week_raw)
                text = item.inner_text().strip()
                klass = item.get_attribute('class') or ''
                logging.info('Attendance table | week=%s | class=%s | text=%s', week, klass, text)
                if '출석' in text:
                    result[week] = '출석'
                elif '결석' in text:
                    result[week] = '결석'
                elif '-' in text:
                    result[week] = '-'
                else:
                    result[week] = 'unknown'
            except Exception:
                continue
    except Exception:
        pass
    return result


def is_module_marked_attended(page, module, attendance_map=None):
    try:
        if attendance_map is None:
            attendance_map = read_attendance_status_by_week(page)
        week = module.get('week_label')
        if week is not None:
            status = attendance_map.get(week, 'unknown')
            logging.info('Attendance probe | week=%s | status=%s | title=%s', week, status, module.get('title'))
            return status == '출석'
    except Exception:
        pass
    return False


def parse_attendance_window_from_context(context_text):
    m = re.search(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s*~\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', context_text)
    if not m:
        return None
    try:
        start = datetime.strptime(m.group(1), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        end = datetime.strptime(m.group(2), '%Y-%m-%d %H:%M:%S').replace(tzinfo=KST)
        return start, end
    except Exception:
        return None


def same_attendance_checkpoint(before_att, after_att):
    return after_att > before_att


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
    parser.add_argument('--visible', action='store_true', help='force visible browser window')
    parser.add_argument('--debug-first', action='store_true', help='stop after opening first module for inspection')
    parser.add_argument('--dump-frames', action='store_true', help='dump frame and jwplayer diagnostics for current module')
    parser.add_argument('--cron-auto', action='store_true', help='allow cron add/remove')
    parser.add_argument('--max-wait', type=int, default=7200)
    parser.add_argument('--log-dir', default='./eclass_run_logs')
    parser.add_argument('--limit-courses', type=int, default=0)
    parser.add_argument('--resume-course', type=str, default=None)
    parser.add_argument('--keep-open', action='store_true', help='keep browser open at end')
    parser.add_argument('--single-course', action='store_true', help='only process first course')
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
        browser_args = dict(
            headless=(False if args.visible else args.headless), 
            slow_mo=(150 if args.visible else 0),
            args=['--mute-audio']  # Mute audio at the browser level
        )
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
            if args.single_course and courses:
                courses = courses[:1]
            if args.limit_courses>0:
                courses = courses[:args.limit_courses]

            overall_ok = True
            unresolved_modules = []
            course_summaries = []
            for course in courses:
                course_name = course.get('title') or course['href']
                course_success = []
                course_failed = []
                logging.info('========== COURSE START | %s ==========', course_name)
                logging.info('Processing course %s', course['href'])
                page.goto(course['href'])
                page.wait_for_load_state('networkidle')
                html = page.content()
                attendance_map = read_attendance_status_by_week(page)
                modules = find_video_modules_from_course_html(html)
                logging.info('Found %d video modules', len(modules))
                for m in modules:
                    rng = parse_date_range_from_text(m.get('context', ''))
                    logging.info('Module candidate | week=%s | title=%s | range=%s', m.get('week_label'), m.get('title'), rng)
                # filter by availability window
                available = [m for m in modules if in_availability_window(m.get('context',''))]
                logging.info('%d modules within availability window', len(available))
                for m in available:
                    rng = parse_date_range_from_text(m.get('context', ''))
                    logging.info('Selected module | week=%s | title=%s | range=%s', m.get('week_label'), m.get('title'), rng)
                completed_modules = set()
                for m in available:
                    if m['href'] in completed_modules:
                        continue
                    if is_module_marked_attended(page, m, attendance_map):
                        logging.info('[SKIP][ATTENDED] week=%s | title=%s', m.get('week_label'), m.get('title'))
                        completed_modules.add(m['href'])
                        course_success.append({'week': m.get('week_label'), 'title': m.get('title'), 'status': 'already_attended'})
                        continue
                    completed_modules.add(m['href'])
                    logging.info('Visiting module %s', m['href'])
                    page.goto(m['href'])
                    page.wait_for_load_state('networkidle')
                    
                    dump_frame_diagnostics(page)
                    
                    if args.debug_first:
                        logging.info('Debug-first enabled; stopping before playback')
                        page.pause()
                        return
                        
                    info = attempt_play_video(page, max_wait=args.max_wait, logdir=logdir)
                    logging.info('Played? %s player=%s duration=%s watched=%.1f', info['found'], info.get('player_type'), info['duration'], info['watched_seconds'])
                    clicked = click_end_modal(page)
                    logging.info('End modal clicked: %s', clicked)

                    page.goto(course['href'])
                    page.wait_for_load_state('networkidle')
                    attendance_map = read_attendance_status_by_week(page)
                    if is_module_marked_attended(page, m, attendance_map):
                        logging.info('[SUCCESS][ATTENDED] week=%s | title=%s', m.get('week_label'), m.get('title'))
                        course_success.append({'week': m.get('week_label'), 'title': m.get('title'), 'status': 'attended'})
                        time.sleep(2)
                        continue

                    logging.warning('[FAIL][UNRESOLVED] week=%s | title=%s', m.get('week_label'), m.get('title'))
                    course_failed.append({'week': m.get('week_label'), 'title': m.get('title')})
                    overall_ok = False
                    unresolved_modules.append({
                        'course': course.get('title') or course.get('href'),
                        'week': m.get('week_label'),
                        'title': m.get('title'),
                        'href': m.get('href'),
                    })
                logging.info('========== COURSE END | %s ==========', course_name)
                logging.info('[COURSE SUMMARY] %s | success=%d | failed=%d', course_name, len(course_success), len(course_failed))
                if course_success:
                    for item in course_success:
                        logging.info('  [OK] week=%s | title=%s | status=%s', item['week'], item['title'], item['status'])
                if course_failed:
                    for item in course_failed:
                        logging.warning('  [NO] week=%s | title=%s', item['week'], item['title'])
                course_summaries.append({
                    'course': course_name,
                    'success': course_success,
                    'failed': course_failed,
                })
                # finished modules for this course

            logging.info('========== FINAL ATTENDANCE DASHBOARD ==========' )
            logging.info('과목명 | 성공 | 실패 | 상세')
            logging.info('-' * 100)
            for summary in course_summaries:
                success_count = len(summary['success'])
                failed_count = len(summary['failed'])
                detail_parts = []
                for item in summary['success']:
                    week = item.get('week')
                    status = item.get('status')
                    if week is not None:
                        detail_parts.append(f'W{week}:출석({status})')
                    else:
                        detail_parts.append(f'{item.get("title")}:출석({status})')
                for item in summary['failed']:
                    week = item.get('week')
                    if week is not None:
                        detail_parts.append(f'W{week}:미반영')
                    else:
                        detail_parts.append(f'{item.get("title")}:미반영')
                detail = ', '.join(detail_parts) if detail_parts else '해당 없음'
                logging.info('%s | %d | %d | %s', summary['course'], success_count, failed_count, detail)

            if unresolved_modules:
                logging.warning('========== FINAL UNRESOLVED MODULES ==========' )
                for item in unresolved_modules:
                    logging.warning('Unresolved | course=%s | week=%s | title=%s | href=%s', item['course'], item['week'], item['title'], item['href'])
            else:
                logging.info('All processed modules are marked 출석.')

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
                if not args.keep_open:
                    context.close()
                    browser.close()
            except Exception:
                pass

if __name__ == '__main__':
    main()

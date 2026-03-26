# eclass_pc_bundle

PC용 한성 e-class 자동수강 작업 폴더.

## 포함 파일
- `eclass_autoplayer_v2.py` : 현재 메인 스크립트
- `eclass_autoplayer.py` : 이전 버전 참고
- `course_44888_acts.json` : 강의 액션/모듈 레퍼런스
- `course_44888_headful.html` : headful DOM 스냅샷
- `course_44888.html` : 일반 DOM 스냅샷

## 현재 작업 방향
- 서버 headless/headful 실패 → 로컬 PC에서 실행
- cron/재시도 자동화는 일단 제외
- 브라우저는 `headless=False`로 띄우는 형태로 정리
- 이 강좌의 VOD는 `JWPlayer` 기반으로 취급


## Requirements
- Python 3.10+
- beautifulsoup4
- playwright

## Setup
```powershell
py -3 -m pip install -r requirements.txt
py -3 -m playwright install chromium
```

# Hansung E-class Auto Runner (PC Edition)

한성대학교 e-class(유비온 기반) 자동 수강 도구입니다. 이 버전은 **맥북(macOS) 및 Windows 로컬 PC 환경**에서 직접 브라우저를 띄워(visible mode) 안정적으로 출석을 완료하는 데 최적화되어 있습니다.

## 주요 특징 및 안정성

- **정속 주행 기반의 안정성:** 첫 수강 시 배속 재생이 제한되는 한성대 e-class 시스템의 특성을 고려하여, **배속 없이 1.0배속으로 실제 재생**을 수행합니다. 이를 통해 서버에서 정상적인 시청 데이터로 기록되며, 현재까지 한성대의 봇 차단 시스템에 감지되지 않고 안정적으로 출석이 반영됨을 확인했습니다.
- **자동 로그인:** 환경 변수(`.env`) 기반 자동 로그인 지원.
- **JWPlayer 자동화:** 유비온 플레이어(JWPlayer)를 인식하여 팝업 열기 → 재생 유도 → seek → 종료 클릭 흐름을 자동화합니다.
- **주차 자동 감지:** 과목 페이지의 상위 DOM 문맥과 제목 패턴(`4주차`, `Lecture4`, `4장)`, `실습 4)` 등)을 함께 사용해 현재 주차를 추론합니다.
- **출석 판정 개선:** 제목 주변 문자열이 아니라 **진도현황(attendance table)** 의 `data-target` 주차값을 기준으로 해당 주차가 `출석/결석/-` 상태인지 확인합니다.
- **다중 과목 처리:** 수강 중인 모든 과목을 순회하며, **현재 열려 있는 주차만** 선별해 처리합니다.
- **가독성 좋은 최종 요약:** 실행 종료 후 과목별 최종 결과를 한눈에 볼 수 있는 출석 대시보드 로그를 출력합니다.

## 빠른 시작

### macOS

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python3 eclass_pc_runner.py
```

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python eclass_pc_runner.py
```

## 환경 변수 설정

프로젝트 루트에 `.env` 파일을 생성하고 아래 정보를 입력합니다.

```env
HANSUNG_INFO_ID=your_id
HANSUNG_INFO_PASSWORD=your_password
```

## 로그에서 볼 포인트

실행 중에는 다음 로그를 중심으로 보면 됩니다.

- `Module candidate | week=...` → 모듈/주차 파싱 결과
- `Selected module | week=...` → 현재 날짜 기준으로 실제 선택된 주차
- `Attendance probe | week=...` → 진도현황 표 기반 출석 판정 결과
- `[SKIP][ATTENDED]` → 이미 출석 처리된 주차라 스킵
- `[SUCCESS][ATTENDED]` → 재생 후 출석 반영 확인
- `[FAIL][UNRESOLVED]` → 재생했지만 출석 반영이 확인되지 않음
- 마지막 `FINAL ATTENDANCE DASHBOARD` → 과목별 최종 결과 요약

## 주의 사항

- **개인적 용도 전용:** 이 도구는 학습 편의를 위해 제작되었습니다. 과도한 사용이나 부정 시청은 학칙에 따라 제재를 받을 수 있습니다.
- **화면 유지:** 실행 중에는 맥북/Windows PC의 절전 모드나 화면 보호기가 작동하지 않도록 주의해 주세요.
- **네트워크 안정성:** VPN(WireGuard 등) 환경에서 실행 시 지연 시간이 발생할 수 있으므로 충분한 타임아웃을 권장합니다.
- **학교 페이지 구조 의존:** 주차 파싱과 출석 판정은 현재 한성대 e-class의 HTML 구조를 기준으로 하므로, UI가 바뀌면 selector 보정이 필요할 수 있습니다.

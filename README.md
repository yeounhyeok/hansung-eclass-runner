# Hansung E-class Auto Runner (PC Edition)

한성대학교 e-class(유비온 기반) 자동 수강 도구입니다. 이 버전은 **맥북(macOS) 및 로컬 PC 환경**에서 직접 브라우저를 띄워(visible mode) 안정적으로 출석을 완료하는 데 최적화되어 있습니다.

## 주요 기능

- **자동 로그인:** 환경 변수(`.env`) 기반 자동 로그인 지원.
- **JWPlayer 자동화:** 유비온 플레이어(JWPlayer)를 인식하여 영상 종료 시점까지 대기 또는 종료 지점으로 이동 후 출석 처리.
- **정밀 종료 로직:** 영상 시청 완료 후 우측 상단의 닫기('X') 좌표를 정밀하게 클릭하여 서버 기록 누락 방지.
- **다중 과목 처리:** 수강 중인 모든 과목의 미시청 영상을 순차적으로 탐색 및 수강.

## 시작하기

### 1. 환경 설정

`.env` 파일을 생성하고 아래 정보를 입력합니다.

```env
HANSUNG_INFO_ID=your_id
HANSUNG_INFO_PASSWORD=your_password
```

### 2. 패키지 설치

```bash
pip install -r requirements.txt
playwright install chromium
```

### 3. 실행

```bash
python3 eclass_pc_runner.py
```

## 주의 사항

- **개인적 용도 전용:** 이 도구는 학습 편의를 위해 제작되었습니다. 과도한 사용이나 부정 시청은 학칙에 따라 제재를 받을 수 있습니다.
- **화면 유지:** 실행 중에는 맥북의 화면 보호기나 절전 모드가 작동하지 않도록 주의해 주세요.
- **네트워크 안정성:** VPN(WireGuard 등) 환경에서 실행 시 지연 시간이 발생할 수 있으므로 충분한 타임아웃을 권장합니다.

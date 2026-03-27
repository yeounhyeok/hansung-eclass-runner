# Hansung E-class Auto Runner (PC Edition)

한성대학교 e-class(유비온 기반) 자동 수강 도구입니다. **수강 중인 과목을 전부 순회하면서, 해당 주차에 아직 출석 처리되지 않은 영상을 자동으로 찾아 수강**하도록 설계되어 있습니다. macOS와 Windows 로컬 PC에서 바로 실행하는 용도에 맞춰 정리했습니다.

## 1. 빠른 시작

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
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python eclass_pc_runner.py
```

> PowerShell에서 venv 활성화가 막히면 `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` 를 먼저 한 번 실행하면 됩니다. 현재 세션에만 적용됩니다.

## 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일을 만들고 아래 값을 넣어주세요.

```env
HANSUNG_INFO_ID=your_id
HANSUNG_INFO_PASSWORD=your_password
```

### 가장 쉬운 방법

- **macOS / Linux**
  ```bash
  nano .env
  ```

- **Windows (메모장)**
  ```powershell
  notepad .env
  ```

파일이 열리면 아래처럼 저장하면 됩니다.

```env
HANSUNG_INFO_ID=2024xxxx
HANSUNG_INFO_PASSWORD=your_password
```

### 파일 없이 현재 세션에서만 바로 넣기

- **macOS / Linux**
  ```bash
  export HANSUNG_INFO_ID=2024xxxx
  export HANSUNG_INFO_PASSWORD='your_password'
  python3 eclass_pc_runner.py
  ```

- **Windows (PowerShell)**
  ```powershell
  $env:HANSUNG_INFO_ID="2024xxxx"
  $env:HANSUNG_INFO_PASSWORD="your_password"
  python eclass_pc_runner.py
  ```

## 3. 이 도구가 하는 일

- e-class 과목 전체 순회
- 현재 열려 있고 아직 출석 처리되지 않은 주차 탐색
- JWPlayer 팝업 자동 재생
- 진도현황 표 기준 출석 여부 확인
- 과목별 최종 결과 요약 출력

## 4. 로그에서 볼 포인트

- `Selected module | week=...` → 실제 수강 대상으로 선택된 주차
- `Attendance table | week=...` → 진도현황 표 기준 출석 상태 읽기
- `[SKIP][ATTENDED]` → 이미 출석 처리된 주차라 스킵
- `[SUCCESS][ATTENDED]` → 재생 후 출석 반영 확인
- `[FAIL][UNRESOLVED]` → 재생했지만 출석 반영이 확인되지 않음
- `🏁 COURSE END | 과목명 | ✅/❌` → 과목 단위 결과
- `🎯 FINAL ATTENDANCE DASHBOARD` → 전체 과목 최종 요약

## 5. 주의 사항

- **개인적 용도 전용**입니다.
- 실행 중에는 절전 모드/화면 보호기가 꺼지지 않도록 주의하세요.
- 학교 페이지 구조가 바뀌면 selector 보정이 필요할 수 있습니다.

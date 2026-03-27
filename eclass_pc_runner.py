#!/usr/bin/env python3
"""PC 전용 e-class 자동수강 런처.

- headless=False 고정
- cron/재시도 기능 기본 비활성화
- 로컬 PC에서 바로 실행하기 위한 진입점
"""
from pathlib import Path
import os
import runpy
import sys

BASE = Path(__file__).resolve().parent
SCRIPT = BASE / 'eclass_autoplayer_v2.py'


def main():
    os.chdir(BASE)
    sys.argv = [str(SCRIPT), '--visible', '--keep-open']
    runpy.run_path(str(SCRIPT), run_name='__main__')


if __name__ == '__main__':
    main()

"""글로벌 ID 이관 확인용 일회성 스크립트. init_db() 가 이미 멱등 이관을 수행하므로
이 스크립트는 결과 건수를 눈으로 확인하기 위한 것이다(D5)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 리포 루트를 import 경로에 추가

from src.db import init_db, migrate_global_ids


def main() -> None:
    init_db()
    print(json.dumps(migrate_global_ids(), ensure_ascii=False))


if __name__ == "__main__":
    main()

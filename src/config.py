from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

# OS 신뢰저장소(맥 키체인 / 데비안 CA)를 SSL 검증에 사용한다.
# 사내망 TLS 검사(예: Zscaler) 환경에서 certifi 번들만으로는 검증이 실패하므로,
# best-effort 로 주입하고 미설치 시 certifi 로 폴백한다.
try:
    import truststore

    truststore.inject_into_ssl()
except ImportError:  # pragma: no cover
    pass


class Settings(BaseSettings):
    """환경변수 / .env 로 주입되는 설정. 시크릿은 .env 에만 둔다(커밋 금지)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 공공데이터포털 인증키
    odcloud_api_key: str = ""
    odcloud_base_url: str = "https://api.odcloud.kr/api"

    # 텔레그램 (Phase 5)
    tg_bot_token: str = ""
    tg_chat_id: str = ""

    # DB (Phase 2)
    database_url: str = "postgresql+psycopg://chungyak:changeme@localhost:5432/chungyak"

    # 웹 대시보드 로그인(외부공개 시 필수). 비우면 인증 없음(로컬 전용).
    web_user: str = ""
    web_password: str = ""
    # 세션 쿠키 서명 키(고정해야 재시작 후에도 로그인 유지)
    session_secret: str = "chungyak-alimi-dev-secret-change-me"

    # 카카오맵 JavaScript 앱키(상세페이지 지도). 도메인 제한으로 보호됨.
    kakao_js_key: str = ""


settings = Settings()

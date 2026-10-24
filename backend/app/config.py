from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str
    secret_key: str
    environment: str = "development"
    cors_origins: str = "http://localhost:3000"
    # Device QR Code Management System — the frontend origin used to build the
    # scan URL encoded into a generated QR image (e.g. "<frontend_base_url>/scan/
    # device/<token>"). Only used server-side to render the QR image; never
    # returned to the public scan endpoint itself.
    frontend_base_url: str = "http://localhost:3000"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    file_storage_path: Path = Path("storage")
    ai_provider: str = "disabled"
    access_token_expire_minutes: int = 60 * 8
    algorithm: str = "HS256"

    # Login brute-force protection (security hotfix) — in-process sliding-window
    # limiter, see services/rate_limiter.py. Both Render services run on the Free
    # plan, which is a single instance with no horizontal scaling, so an in-memory
    # counter is sufficient here; it would need a shared store (Redis etc.) if this
    # app ever moved to a multi-instance deployment. Configurable rather than
    # hard-coded so a branch behind one shared NAT'd office IP can be tuned up
    # without a code change if 5/min proves too strict in practice.
    login_rate_limit_per_minute: int = 5
    login_rate_limit_window_seconds: int = 60

    # Module 17B — configurable SMTP/email provider. All optional/blank by default;
    # sending is refused with a clear error (not a silent no-op or fake success) when
    # smtp_host is unset. Never hard-code a manager/admin email anywhere — the actual
    # send-to address always comes from a communication rule's receiver_email, never
    # from settings. Values are read only from environment/.env, never returned in any
    # API response (see routers/notifications.py — templates never echo these back).
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    smtp_from_email: str = ""
    smtp_from_name: str = "MAX Biomedical AI"

    # Module 19 Enhancement — WhatsApp Web browser-automation provider. Persistent
    # session profile lives under file_storage_path/whatsapp_session (never exposed to
    # any API response). Headless defaults to True (server environment, no display);
    # set to False only for local debugging where a visible browser window is useful.
    whatsapp_automation_headless: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        origins = [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]
        if "*" in origins:
            # main.py's CORSMiddleware always runs with allow_credentials=True (this
            # app is bearer-token, not cookie, based — see SECURITY.md — but the
            # Authorization header still needs allow_credentials for the browser to
            # send it cross-origin). A literal wildcard origin combined with
            # credentials lets any website's JS read this API's responses using a
            # token it stole/tricked from a user, from any origin — reject the
            # misconfiguration at startup rather than let it silently reach
            # CORSMiddleware. Use an explicit comma-separated origin list instead.
            raise ValueError(
                "CORS_ORIGINS must not be '*' — list explicit allowed origins "
                "(this app always sends credentials, so a wildcard origin is unsafe)."
            )
        return origins


settings = Settings()

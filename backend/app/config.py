from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://maison:maison@localhost:5433/maison_scout"
    cors_origins: str = "http://localhost:5173"
    secret_key: str = "change-me-in-production"
    allow_open_registration: bool = True
    invite_codes: str = ""
    crawl_secret: str = ""
    admin_emails: str = ""
    off_market_after_hours: int = 48
    # Every crawl/ingestion cron on this project runs on a 6h cycle (see
    # docs/PROJECT_CONTEXT.md); used by /api/sources/status to estimate the
    # next expected pass per source from its last crawl_runs row.
    crawl_interval_hours: int = 6

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def invite_code_set(self) -> set[str]:
        return {code.strip() for code in self.invite_codes.split(",") if code.strip()}

    @property
    def admin_email_set(self) -> set[str]:
        return {email.strip().lower() for email in self.admin_emails.split(",") if email.strip()}


settings = Settings()

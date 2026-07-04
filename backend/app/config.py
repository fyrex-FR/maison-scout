from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://maison:maison@localhost:5433/maison_scout"
    cors_origins: str = "http://localhost:5173"
    secret_key: str = "change-me-in-production"
    allow_open_registration: bool = True
    invite_codes: str = ""
    crawl_secret: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def invite_code_set(self) -> set[str]:
        return {code.strip() for code in self.invite_codes.split(",") if code.strip()}


settings = Settings()

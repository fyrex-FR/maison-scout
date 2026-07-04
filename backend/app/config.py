from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://maison:maison@localhost:5433/maison_scout"
    cors_origins: str = "http://localhost:5173"
    secret_key: str = "change-me-in-production"
    allow_open_registration: bool = True

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()

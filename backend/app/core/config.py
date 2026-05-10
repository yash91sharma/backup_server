from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tz: str = "UTC"

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str = "redis://localhost:6379/0"

    proxy_list: str = ""
    proxy_provider_api_url: str = ""
    proxy_provider_api_key: str = ""
    proxy_username: str = ""
    proxy_password: str = ""

    captcha_solver_provider: str = ""
    captcha_solver_api_key: str = ""

    max_concurrent_browsers: int = 10
    request_delay_min: float = 0.3
    request_delay_max: float = 0.8

    output_dir: Path = Path("./data")
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
(settings.output_dir / "json").mkdir(parents=True, exist_ok=True)
(settings.output_dir / "csv").mkdir(parents=True, exist_ok=True)
Path("logs").mkdir(parents=True, exist_ok=True)

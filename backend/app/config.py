from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: Literal["production", "dev", "test"] = "production"
    app_secret_key: str = Field(min_length=32)
    database_url: str = "sqlite:///./metabolens.db"
    token_expire_minutes: int = 60 * 24
    cors_origins: str = "http://127.0.0.1:18050,http://localhost:18050"
    auto_seed: bool = False
    bootstrap_admin_email: EmailStr | None = None
    bootstrap_admin_password: SecretStr | None = None
    example_mzml_path: str = "/app/examples/data/Metabolomics_1.mzML"
    upload_storage_backend: str = "local"
    upload_storage_dir: str = "/tmp/metabolens_uploads"
    study_package_root: str = "/tmp/metabolens_packages"
    upload_storage_s3_bucket: str = "metabolens"
    upload_storage_s3_prefix: str = "raw"
    raw_path_browse_roots: str = "/data/uploads,/raw_test"
    workflow_pipeline_root: str = "workflows/nextflow4ms-dial-msdial5-clean"
    testing_god_token: str | None = None
    testing_god_user_email: str = "admin@metabolens.local"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True)

    @model_validator(mode="after")
    def validate_auth_settings(self):
        if self.app_env != "dev" and (self.auto_seed or self.testing_god_token):
            raise ValueError("Demo seeding and authentication require APP_ENV=dev")
        if bool(self.bootstrap_admin_email) != bool(self.bootstrap_admin_password):
            raise ValueError("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD together")
        if self.bootstrap_admin_password and len(self.bootstrap_admin_password.get_secret_value()) < 16:
            raise ValueError("BOOTSTRAP_ADMIN_PASSWORD must contain at least 16 characters")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

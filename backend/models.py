from typing import Optional

from sqlmodel import Field, SQLModel


class Account(SQLModel, table=True):
    """Login identity. Password hashes are intentionally never API response models."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(index=True)  # worker or employer
    area: str = ""
    created_at: str = ""


class Worker(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    account_id: Optional[int] = Field(default=None, index=True)
    name: str
    skill: str
    experience_years: int = 0
    daily_wage: int = 0
    area: str
    region: str = ""
    available: bool = True
    location_consent: bool = False
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class Job(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    employer_account_id: Optional[int] = Field(default=None, index=True)
    title: str
    skill_required: str
    employer: str
    area: str
    region: str = ""
    daily_wage: int
    workers_needed: int = 1
    work_date: str = "Flexible"
    description: str = ""
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    is_open: bool = True


class JobApplication(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    job_id: int = Field(index=True)
    # worker_id is the authenticated worker account id. worker_account_id is
    # retained during the additive migration for compatibility with old data.
    worker_id: Optional[int] = Field(default=None, index=True)
    worker_account_id: Optional[int] = Field(default=None, index=True)
    status: str = Field(default="pending", index=True)
    created_at: str = ""

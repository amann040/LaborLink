import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr, Field as PydanticField, field_validator
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from .auth import create_access_token, get_current_account, hash_password, verify_password
from .database import create_db_and_tables, engine, get_session
from .fog_matching import match_workers_for_job, rank_workers_for_job
from .matching import haversine_km
from .models import Account, Job, JobApplication, Worker


def cleaned(value: str) -> str:
    return " ".join(value.strip().split())


class RegisterRequest(BaseModel):
    name: str = PydanticField(min_length=2, max_length=100)
    email: EmailStr
    password: str = PydanticField(min_length=8, max_length=128)
    area: str = PydanticField(default="", max_length=100)

    @field_validator("name", "area")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        return cleaned(value)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = PydanticField(min_length=1, max_length=128)


class AccountResponse(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: Literal["worker", "employer"]
    area: str


class WorkerPayload(BaseModel):
    skill: str = PydanticField(min_length=2, max_length=100)
    experience_years: int = PydanticField(default=0, ge=0, le=80)
    daily_wage: int = PydanticField(default=0, ge=0, le=100000)
    area: str = PydanticField(min_length=2, max_length=100)
    region: str = PydanticField(default="", max_length=100)
    available: bool = True
    location_consent: bool = False
    latitude: Optional[float] = PydanticField(default=None, ge=-90, le=90)
    longitude: Optional[float] = PydanticField(default=None, ge=-180, le=180)


class JobPayload(BaseModel):
    title: str = PydanticField(min_length=3, max_length=160)
    skill_required: str = PydanticField(min_length=2, max_length=100)
    area: str = PydanticField(min_length=2, max_length=100)
    region: str = PydanticField(default="", max_length=100)
    daily_wage: int = PydanticField(ge=1, le=100000)
    workers_needed: int = PydanticField(default=1, ge=1, le=1000)
    work_date: str = PydanticField(default="Flexible", max_length=100)
    description: str = PydanticField(default="", max_length=2000)
    latitude: Optional[float] = PydanticField(default=None, ge=-90, le=90)
    longitude: Optional[float] = PydanticField(default=None, ge=-180, le=180)


class ApplicationStatusUpdate(BaseModel):
    status: Literal["accepted", "rejected"]


class ApplicantResponse(BaseModel):
    id: int
    job_id: int
    job_title: str
    worker_id: int
    name: str
    skill: Optional[str] = None
    experience_years: Optional[int] = None
    area: str
    expected_daily_wage: Optional[int] = None
    status: Literal["pending", "accepted", "rejected"]
    created_at: str


class WorkerApplicationResponse(BaseModel):
    id: int
    job_id: int
    job_title: str
    employer: str
    area: str
    daily_wage: int
    status: Literal["pending", "accepted", "rejected"]
    created_at: str


class FogMatchResponse(BaseModel):
    worker_id: int
    skill: str
    area: str
    experience_years: int
    expected_daily_wage: int
    distance_km: float
    match_score: float
    explanation: str


class FogMatchResult(BaseModel):
    matches: list[FogMatchResponse]
    matching_time_ms: float
    candidates_considered: int
    radius_km: float


def application_worker_id(application: JobApplication) -> Optional[int]:
    return application.worker_id or application.worker_account_id


def applicant_response(application: JobApplication, job: Job, account: Account, worker: Optional[Worker]) -> dict:
    return {
        "id": application.id, "job_id": job.id, "job_title": job.title,
        "worker_id": application_worker_id(application), "name": account.name,
        "skill": worker.skill if worker else None,
        "experience_years": worker.experience_years if worker else None,
        "area": worker.area if worker else account.area,
        "expected_daily_wage": worker.daily_wage if worker else None,
        "status": application.status, "created_at": application.created_at,
    }


def account_response(account: Account) -> dict:
    return AccountResponse(id=account.id, name=account.name, email=account.email, role=account.role, area=account.area).model_dump()


def require_role(role: str):
    def dependency(account: Account = Depends(get_current_account)) -> Account:
        if account.role != role:
            raise HTTPException(status_code=403, detail=f"{role.title()} account required")
        return account
    return dependency


def register_account(data: RegisterRequest, role: Literal["worker", "employer"], session: Session):
    email = str(data.email).strip().lower()
    if session.exec(select(Account).where(Account.email == email)).first():
        raise HTTPException(status_code=409, detail="Email is already registered")
    account = Account(name=data.name, email=email, password_hash=hash_password(data.password), role=role, area=data.area, created_at=datetime.now(timezone.utc).isoformat())
    session.add(account)
    try:
        session.commit(); session.refresh(account)
    except Exception:
        session.rollback()
        raise HTTPException(status_code=409, detail="Could not create account")
    return {"access_token": create_access_token(account), "token_type": "bearer", "account": account_response(account)}


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_db_and_tables()
    with Session(engine) as s:
        if not s.exec(select(Worker)).first():
            s.add_all([Worker(name="Ravi Kumar", skill="Electrician", experience_years=4, daily_wage=800, area="Vellore", region="Vellore", latitude=12.9165, longitude=79.1325), Worker(name="Meena Devi", skill="Painter", experience_years=3, daily_wage=700, area="Katpadi", region="Vellore", latitude=12.9698, longitude=79.1456), Worker(name="Arun Kumar", skill="Plumber", experience_years=5, daily_wage=850, area="Vellore", region="Vellore", latitude=12.9200, longitude=79.1350)])
        if not s.exec(select(Job)).first():
            s.add_all([Job(title="Residential wiring assistance", skill_required="Electrician", employer="Sri BuildWorks", area="Vellore", region="Vellore", daily_wage=900, workers_needed=2, work_date="Tomorrow", description="Electrical installation assistance.", latitude=12.9165, longitude=79.1325), Job(title="Apartment interior painting", skill_required="Painter", employer="Green Homes", area="Katpadi", region="Vellore", daily_wage=750, workers_needed=2, work_date="Friday", description="Interior wall painting.", latitude=12.9698, longitude=79.1456), Job(title="Bathroom plumbing repair", skill_required="Plumber", employer="HomeFix Services", area="Vellore", region="Vellore", daily_wage=850, workers_needed=1, work_date="Today", description="Residential plumbing repair.", latitude=12.92, longitude=79.135)])
        s.commit()
    yield


app = FastAPI(title="LaborLink API", version="1.0.0", lifespan=lifespan)
origins = [origin.strip() for origin in os.getenv("LABORLINK_CORS_ORIGINS", "http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:8000,http://localhost:8000").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])


@app.get("/api/health")
def health(): return {"status": "ok", "service": "LaborLink API"}


@app.post("/api/auth/register/worker", status_code=201)
def register_worker(data: RegisterRequest, session: Session = Depends(get_session)): return register_account(data, "worker", session)


@app.post("/api/auth/register/employer", status_code=201)
def register_employer(data: RegisterRequest, session: Session = Depends(get_session)): return register_account(data, "employer", session)


@app.post("/api/auth/login")
def login(data: LoginRequest, session: Session = Depends(get_session)):
    account = session.exec(select(Account).where(Account.email == str(data.email).strip().lower())).first()
    if not account or not verify_password(data.password, account.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password", headers={"WWW-Authenticate": "Bearer"})
    return {"access_token": create_access_token(account), "token_type": "bearer", "account": account_response(account)}


@app.get("/api/auth/me")
def me(account: Account = Depends(get_current_account)): return account_response(account)


@app.post("/api/auth/logout", status_code=204)
def logout(_: Account = Depends(get_current_account)): return None  # JWT is discarded client-side; short token expiry limits exposure.


@app.get("/api/jobs", response_model=list[Job])
def list_jobs(skill: Optional[str] = None, area: Optional[str] = None, min_wage: Optional[int] = Query(None, ge=0), max_wage: Optional[int] = Query(None, ge=0), lat: Optional[float] = Query(None, ge=-90, le=90), lon: Optional[float] = Query(None, ge=-180, le=180), session: Session = Depends(get_session)):
    if (lat is None) != (lon is None): raise HTTPException(422, "lat and lon must be supplied together")
    items = [j for j in session.exec(select(Job)).all() if j.is_open]
    if skill: items = [j for j in items if skill.lower() in (j.skill_required + " " + j.title).lower()]
    if area: items = [j for j in items if area.lower() in j.area.lower()]
    if min_wage is not None: items = [j for j in items if j.daily_wage >= min_wage]
    if max_wage is not None: items = [j for j in items if j.daily_wage <= max_wage]
    if lat is not None:
        items.sort(key=lambda j: haversine_km(lat, lon, j.latitude, j.longitude) if j.latitude is not None and j.longitude is not None else float("inf"))
    return items


@app.get("/api/jobs/{job_id}", response_model=Job)
def job_detail(job_id: int, session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    return job


@app.post("/api/jobs", response_model=Job, status_code=201)
def create_job(data: JobPayload, account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    values = data.model_dump()
    values["region"] = data.region or data.area
    job = Job(**values, employer=account.name, employer_account_id=account.id)
    session.add(job); session.commit(); session.refresh(job)
    return job


@app.put("/api/jobs/{job_id}", response_model=Job)
def edit_job(job_id: int, data: JobPayload, account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    if job.employer_account_id != account.id: raise HTTPException(403, "You can only manage your own jobs")
    for key, value in data.model_dump().items(): setattr(job, key, value)
    job.region = data.region or data.area
    session.add(job); session.commit(); session.refresh(job)
    return job


@app.get("/api/employer/jobs")
def employer_jobs(account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    jobs = session.exec(select(Job).where(Job.employer_account_id == account.id)).all()
    return [{**job.model_dump(), "applicant_count": len(session.exec(select(JobApplication).where(JobApplication.job_id == job.id)).all())} for job in jobs]


@app.get("/api/workers", response_model=list[Worker])
def list_workers(skill: Optional[str] = None, area: Optional[str] = None, session: Session = Depends(get_session)):
    items = session.exec(select(Worker)).all()
    if skill: items = [w for w in items if skill.lower() in w.skill.lower()]
    if area: items = [w for w in items if area.lower() in w.area.lower()]
    return items


@app.put("/api/worker/profile", response_model=Worker)
def update_worker_profile(data: WorkerPayload, account: Account = Depends(require_role("worker")), session: Session = Depends(get_session)):
    worker = session.exec(select(Worker).where(Worker.account_id == account.id)).first()
    # Omitted optional coordinates stay unchanged for an existing profile.
    values = data.model_dump(exclude_unset=True); values["region"] = data.region or data.area
    if worker is None: worker = Worker(account_id=account.id, name=account.name, **values)
    else:
        for key, value in values.items(): setattr(worker, key, value)
    session.add(worker); session.commit(); session.refresh(worker)
    return worker


@app.get("/api/worker/profile", response_model=Worker)
def worker_profile(account: Account = Depends(require_role("worker")), session: Session = Depends(get_session)):
    worker = session.exec(select(Worker).where(Worker.account_id == account.id)).first()
    if not worker: raise HTTPException(404, "Create your worker profile first")
    return worker


@app.get("/api/workers/nearby")
def nearby_workers(lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180), radius_km: float = Query(10, gt=0, le=100), skill: Optional[str] = None, session: Session = Depends(get_session)):
    results = []
    for w in session.exec(select(Worker)).all():
        if not w.available or w.latitude is None or w.longitude is None: continue
        if skill and skill.lower() not in w.skill.lower(): continue
        d = haversine_km(lat, lon, w.latitude, w.longitude)
        if d <= radius_km: results.append({**w.model_dump(), "distance_km": round(d, 2)})
    return sorted(results, key=lambda x: x["distance_km"])


@app.post("/api/jobs/{job_id}/applications", status_code=201)
def apply(job_id: int, account: Account = Depends(require_role("worker")), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job or not job.is_open: raise HTTPException(404, "Open job not found")
    if session.exec(select(JobApplication).where(JobApplication.job_id == job_id, JobApplication.worker_id == account.id)).first():
        raise HTTPException(409, "You have already applied to this job")
    application = JobApplication(job_id=job_id, worker_id=account.id, worker_account_id=account.id, created_at=datetime.now(timezone.utc).isoformat())
    session.add(application)
    try:
        session.commit(); session.refresh(application)
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "You have already applied to this job")
    return application


@app.get("/api/jobs/{job_id}/applicants", response_model=list[ApplicantResponse])
def applicants(job_id: int, account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    if job.employer_account_id != account.id: raise HTTPException(403, "You can only view applicants for your own jobs")
    records = session.exec(select(JobApplication).where(JobApplication.job_id == job_id)).all()
    results = []
    for application in records:
        worker_id = application_worker_id(application)
        worker_account = session.get(Account, worker_id) if worker_id else None
        if not worker_account:
            continue
        worker = session.exec(select(Worker).where(Worker.account_id == worker_id)).first()
        results.append(applicant_response(application, job, worker_account, worker))
    return results


@app.patch("/api/applications/{application_id}")
def update_application(application_id: int, data: ApplicationStatusUpdate, account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    application = session.get(JobApplication, application_id)
    if not application: raise HTTPException(404, "Application not found")
    job = session.get(Job, application.job_id)
    if not job or job.employer_account_id != account.id: raise HTTPException(403, "You can only manage applications for your own jobs")
    application.status = data.status; session.add(application); session.commit(); session.refresh(application)
    return application


@app.get("/api/worker/applications", response_model=list[WorkerApplicationResponse])
def worker_applications(account: Account = Depends(require_role("worker")), session: Session = Depends(get_session)):
    records = session.exec(select(JobApplication).where(JobApplication.worker_id == account.id)).all()
    results = []
    for application in records:
        job = session.get(Job, application.job_id)
        if job:
            results.append({"id": application.id, "job_id": job.id, "job_title": job.title, "employer": job.employer, "area": job.area, "daily_wage": job.daily_wage, "status": application.status, "created_at": application.created_at})
    return results


@app.get("/api/jobs/{job_id}/matches")
def job_matches(job_id: int, account: Account = Depends(require_role("employer")), session: Session = Depends(get_session)):
    """Original endpoint retained; it now returns privacy-safe match records."""
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    if job.employer_account_id != account.id: raise HTTPException(403, "You can only match workers for your own jobs")
    return rank_workers_for_job(session.exec(select(Worker)).all(), job)


@app.get("/api/jobs/{job_id}/fog-matches", response_model=FogMatchResult)
def fog_matches(
    job_id: int,
    radius_km: float = Query(10, gt=0, le=100),
    min_expected_wage: Optional[int] = Query(None, ge=0),
    max_expected_wage: Optional[int] = Query(None, ge=0),
    account: Account = Depends(require_role("employer")),
    session: Session = Depends(get_session),
):
    """Fog-layer regional matching for an employer-owned job."""
    if min_expected_wage is not None and max_expected_wage is not None and min_expected_wage > max_expected_wage:
        raise HTTPException(422, "min_expected_wage cannot exceed max_expected_wage")
    job = session.get(Job, job_id)
    if not job: raise HTTPException(404, "Job not found")
    if job.employer_account_id != account.id:
        raise HTTPException(403, "You can only match workers for your own jobs")
    return match_workers_for_job(
        session.exec(select(Worker)).all(), job, radius_km,
        min_expected_wage=min_expected_wage, max_expected_wage=max_expected_wage,
    )


@app.get("/api/admin/stats")
def admin_stats(_: Account = Depends(get_current_account), session: Session = Depends(get_session)):
    return {"accounts": len(session.exec(select(Account)).all()), "workers": len(session.exec(select(Worker)).all()), "open_jobs": len([j for j in session.exec(select(Job)).all() if j.is_open]), "applications": len(session.exec(select(JobApplication)).all())}

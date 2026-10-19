import os
import tempfile
import unittest
from types import SimpleNamespace

database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
database_file.close()
os.environ["LABORLINK_DB_URL"] = f"sqlite:///{database_file.name}"
os.environ["LABORLINK_SECRET_KEY"] = "test-secret-with-at-least-32-bytes"

from fastapi.testclient import TestClient
from sqlmodel import Session
from backend.database import engine
from backend.fog_matching import match_workers_for_job
from backend.main import app
from backend.models import Worker


class LaborLinkApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)
        os.unlink(database_file.name)

    def register(self, role, email):
        return self.client.post(f"/api/auth/register/{role}", json={"name": f"{role.title()} Test", "email": email, "password": "correct-horse-battery", "area": "Vellore"})

    def test_health_and_registration_login(self):
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        email = "worker-test@example.com"
        registered = self.register("worker", email)
        self.assertEqual(registered.status_code, 201)
        self.assertNotIn("password_hash", registered.json()["account"])
        self.assertEqual(self.register("worker", email).status_code, 409)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code, 401)
        self.assertEqual(self.client.post("/api/auth/login", json={"email": email, "password": "correct-horse-battery"}).status_code, 200)

    def test_roles_jobs_and_matching(self):
        worker = self.register("worker", "worker-roles@example.com").json()["access_token"]
        employer = self.register("employer", "employer-roles@example.com").json()["access_token"]
        self.assertEqual(self.client.post("/api/jobs", json={"title": "Blocked job", "skill_required": "Painter", "area": "Vellore", "daily_wage": 700}, headers={"Authorization": f"Bearer {worker}"}).status_code, 403)
        job = self.client.post("/api/jobs", json={"title": "Test electrician needed", "skill_required": "Electrician", "area": "Vellore", "daily_wage": 900, "latitude": 12.9165, "longitude": 79.1325}, headers={"Authorization": f"Bearer {employer}"})
        self.assertEqual(job.status_code, 201)
        self.assertEqual(self.client.get("/api/jobs?skill=Electrician").status_code, 200)
        self.assertEqual(self.client.post(f"/api/jobs/{job.json()['id']}/applications", headers={"Authorization": f"Bearer {worker}"}).status_code, 201)
        self.assertEqual(self.client.get("/api/workers/nearby?lat=12.9165&lon=79.1325").status_code, 200)

    def test_complete_application_management_workflow(self):
        worker_token = self.register("worker", "workflow-worker@example.com").json()["access_token"]
        employer_token = self.register("employer", "workflow-employer@example.com").json()["access_token"]
        worker_headers = {"Authorization": f"Bearer {worker_token}"}
        employer_headers = {"Authorization": f"Bearer {employer_token}"}

        profile = self.client.put("/api/worker/profile", json={"skill": "Electrician", "experience_years": 6, "daily_wage": 950, "area": "Vellore"}, headers=worker_headers)
        self.assertEqual(profile.status_code, 200)
        job = self.client.post("/api/jobs", json={"title": "Workflow electrical work", "skill_required": "Electrician", "area": "Vellore", "daily_wage": 1000}, headers=employer_headers).json()

        applied = self.client.post(f"/api/jobs/{job['id']}/applications", headers=worker_headers)
        self.assertEqual(applied.status_code, 201)
        self.assertEqual(self.client.post(f"/api/jobs/{job['id']}/applications", headers=worker_headers).status_code, 409)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/applicants", headers=worker_headers).status_code, 403)

        applicants = self.client.get(f"/api/jobs/{job['id']}/applicants", headers=employer_headers)
        self.assertEqual(applicants.status_code, 200)
        employer_jobs = self.client.get("/api/employer/jobs", headers=employer_headers)
        self.assertEqual(employer_jobs.json()[0]["applicant_count"], 1)
        applicant = applicants.json()[0]
        self.assertEqual(applicant["name"], "Worker Test")
        self.assertEqual(applicant["skill"], "Electrician")
        self.assertEqual(applicant["status"], "pending")

        application_id = applicant["id"]
        self.assertEqual(self.client.patch(f"/api/applications/{application_id}", json={"status": "invalid"}, headers=employer_headers).status_code, 422)
        other_employer = self.register("employer", "other-workflow-employer@example.com").json()["access_token"]
        self.assertEqual(self.client.patch(f"/api/applications/{application_id}", json={"status": "accepted"}, headers={"Authorization": f"Bearer {other_employer}"}).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/applications/{application_id}", json={"status": "accepted"}, headers=employer_headers).status_code, 200)
        applications = self.client.get("/api/worker/applications", headers=worker_headers)
        self.assertEqual(applications.status_code, 200)
        self.assertEqual(applications.json()[0]["status"], "accepted")

    def test_fog_endpoint_authorization_validation_and_timing(self):
        employer_token = self.register("employer", "fog-owner@example.com").json()["access_token"]
        other_token = self.register("employer", "fog-other@example.com").json()["access_token"]
        headers = {"Authorization": f"Bearer {employer_token}"}
        job = self.client.post("/api/jobs", json={"title": "Fog electrician work", "skill_required": "Electrician", "area": "Vellore", "daily_wage": 900, "latitude": 12.9165, "longitude": 79.1325}, headers=headers).json()
        with Session(engine) as session:
            worker = Worker(name="Fog Exact", skill="Electrician", experience_years=4, daily_wage=850, area="Vellore", region="Vellore", available=True, location_consent=True, latitude=12.917, longitude=79.133)
            session.add(worker); session.commit(); session.refresh(worker)
            worker_id = worker.id
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/fog-matches?radius_km=0", headers=headers).status_code, 422)
        self.assertEqual(self.client.get(f"/api/jobs/{job['id']}/fog-matches", headers={"Authorization": f"Bearer {other_token}"}).status_code, 403)
        matches = self.client.get(f"/api/jobs/{job['id']}/fog-matches?radius_km=10", headers=headers)
        self.assertEqual(matches.status_code, 200)
        result = matches.json()
        self.assertIsInstance(result["matching_time_ms"], float)
        self.assertIn(worker_id, [match["worker_id"] for match in result["matches"]])

    def test_profile_update_preserves_omitted_coordinates(self):
        token = self.register("worker", "coordinate-preservation@example.com").json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        first = {"skill": "Electrician", "experience_years": 2, "daily_wage": 800, "area": "Vellore", "location_consent": True, "latitude": 12.9165, "longitude": 79.1325}
        self.assertEqual(self.client.put("/api/worker/profile", json=first, headers=headers).status_code, 200)
        updated = self.client.put("/api/worker/profile", json={"skill": "Electrician", "experience_years": 3, "daily_wage": 850, "area": "Vellore", "location_consent": True}, headers=headers)
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["latitude"], 12.9165)
        self.assertEqual(updated.json()["longitude"], 79.1325)


class FogMatchingUnitTests(unittest.TestCase):
    def job(self, **values):
        defaults = {"skill_required": "Electrician", "area": "Vellore", "region": "Vellore", "latitude": 12.9165, "longitude": 79.1325}
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def worker(self, worker_id=1, skill="Electrician", **values):
        defaults = {"id": worker_id, "skill": skill, "area": "Vellore", "region": "Vellore", "available": True, "location_consent": True, "latitude": 12.917, "longitude": 79.133, "experience_years": 3, "daily_wage": 800}
        defaults.update(values)
        return SimpleNamespace(**defaults)

    def match_ids(self, workers, job=None, **options):
        return [item["worker_id"] for item in match_workers_for_job(workers, job or self.job(), radius_km=10, **options)["matches"]]

    def test_synthetic_consented_electrician_within_radius_matches(self):
        self.assertEqual(self.match_ids([self.worker()]), [1])

    def test_availability_and_location_consent_are_independent_exclusions(self):
        self.assertEqual(self.match_ids([self.worker(available=False)]), [])
        self.assertEqual(self.match_ids([self.worker(location_consent=False)]), [])

    def test_coordinate_region_distance_and_wage_exclusions_are_independent(self):
        self.assertEqual(self.match_ids([self.worker(latitude=None)]), [])
        self.assertEqual(self.match_ids([self.worker(region="Chennai")]), [])
        self.assertEqual(self.match_ids([self.worker(latitude=13.5)]), [])
        self.assertEqual(self.match_ids([self.worker(daily_wage=1000)], max_expected_wage=900), [])

    def test_skill_compatibility_independently_excludes_incompatible_trade(self):
        self.assertEqual(self.match_ids([self.worker(skill="Plumber")]), [])
        self.assertEqual(self.match_ids([self.worker(skill="Electrician helper")]), [1])

    def test_missing_job_coordinates_exclude_even_a_valid_worker(self):
        self.assertEqual(self.match_ids([self.worker()], self.job(latitude=None)), [])

    def test_skill_availability_distance_and_missing_coordinate_filters(self):
        job = SimpleNamespace(skill_required="Electrician", area="Vellore", region="Vellore", latitude=12.9165, longitude=79.1325)
        def worker(worker_id, skill, **values):
            defaults = {"id": worker_id, "skill": skill, "area": "Vellore", "region": "Vellore", "available": True, "location_consent": True, "latitude": 12.917, "longitude": 79.133, "experience_years": 3, "daily_wage": 800}
            defaults.update(values)
            return SimpleNamespace(**defaults)
        exact = worker(1, "Electrician")
        partial = worker(2, "Electrician helper")
        incompatible = worker(3, "Plumber")
        unavailable = worker(4, "Electrician", available=False)
        far = worker(5, "Electrician", latitude=13.5)
        missing_location = worker(6, "Electrician", latitude=None)
        result = match_workers_for_job([exact, partial, incompatible, unavailable, far, missing_location], job, radius_km=10, max_expected_wage=900)
        self.assertEqual([item["worker_id"] for item in result["matches"]], [1, 2])
        self.assertIn("Exact", result["matches"][0]["explanation"])
        self.assertGreaterEqual(result["matching_time_ms"], 0)


if __name__ == "__main__":
    unittest.main()

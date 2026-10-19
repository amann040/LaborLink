"""Regional Fog matching: filters and ranks nearby workers before results reach the browser."""
from time import perf_counter

from .matching import haversine_km


def normalize_region(value: str) -> str:
    return " ".join((value or "").strip().lower().split())


def _skill_compatibility(required: str, offered: str):
    """Return a score and explanation, or None when trades are incompatible."""
    required_normalized, offered_normalized = normalize_region(required), normalize_region(offered)
    if required_normalized == offered_normalized:
        return 70, "Exact required-skill match"
    if required_normalized in offered_normalized or offered_normalized in required_normalized:
        return 50, "Partial required-skill match"
    if set(required_normalized.split()) & set(offered_normalized.split()):
        return 35, "Related skill keyword match"
    return None


def match_workers_for_job(workers, job, radius_km: float, min_expected_wage=None, max_expected_wage=None):
    """Return only consented, compatible regional candidates and timing metadata.

    Coordinates are used locally for calculation and are deliberately omitted
    from returned match records.
    """
    started = perf_counter()
    job_region = normalize_region(job.region or job.area)
    results, considered = [], 0
    for worker in workers:
        if not worker.available or not worker.location_consent:
            continue
        if job_region and normalize_region(worker.region or worker.area) != job_region:
            continue
        if None in (job.latitude, job.longitude, worker.latitude, worker.longitude):
            continue
        compatibility = _skill_compatibility(job.skill_required, worker.skill)
        if compatibility is None:
            continue
        considered += 1
        if min_expected_wage is not None and worker.daily_wage < min_expected_wage:
            continue
        if max_expected_wage is not None and worker.daily_wage > max_expected_wage:
            continue
        distance = haversine_km(job.latitude, job.longitude, worker.latitude, worker.longitude)
        if distance > radius_km:
            continue
        skill_score, skill_explanation = compatibility
        distance_score = max(0, 25 * (1 - distance / radius_km))
        experience_score = min(worker.experience_years, 10) * 0.5
        score = round(skill_score + distance_score + experience_score, 2)
        results.append({
            "worker_id": worker.id,
            "skill": worker.skill,
            "area": worker.area,
            "experience_years": worker.experience_years,
            "expected_daily_wage": worker.daily_wage,
            "distance_km": round(distance, 2),
            "match_score": score,
            "explanation": f"{skill_explanation}; available; {round(distance, 2)} km within the {radius_km:g} km radius.",
        })
    results.sort(key=lambda item: (-item["match_score"], item["distance_km"], item["expected_daily_wage"]))
    return {
        "matches": results,
        "matching_time_ms": round((perf_counter() - started) * 1000, 3),
        "candidates_considered": considered,
        "radius_km": radius_km,
    }


def rank_workers_for_job(workers, job):
    """Compatibility adapter for the original endpoint."""
    return match_workers_for_job(workers, job, radius_km=10)["matches"]

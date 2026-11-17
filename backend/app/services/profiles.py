"""Create, replace, and retrieve authenticated users' profile snapshots."""

import logging
import re
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app.models.profile import Certification as CertificationModel
from app.models.profile import Experience as ExperienceModel
from app.models.profile import Profile, Project as ProjectModel, Skill
from app.schemas.profiles import (
    AdditionalInformation,
    BasicInformation,
    CareerSkills,
    Certification,
    Experience,
    ProfileCreateRequest,
    ProfileResponse,
    Project,
)


class ProfileConflictError(RuntimeError):
    """Raised when concurrent or conflicting profile writes cannot be resolved."""


class ProfilePersistenceError(RuntimeError):
    """Raised when profile data cannot be read or persisted safely."""


logger = logging.getLogger(__name__)
_SQLSTATE_PATTERN = re.compile(r"[0-9A-Z]{5}\Z")


def _extract_sqlstate(error: Exception) -> str:
    """Return only a syntactically valid PostgreSQL SQLSTATE, or a fixed fallback."""
    original = getattr(error, "orig", None)
    if original is None:
        original = error

    for attribute in ("sqlstate", "pgcode"):
        try:
            candidate = getattr(original, attribute, None)
        except Exception:
            continue
        if isinstance(candidate, str):
            normalized = candidate.upper()
            if _SQLSTATE_PATTERN.fullmatch(normalized):
                return normalized
    return "unavailable"


def _log_redacted_exception(
    target_logger: logging.Logger, event_code: str, error: Exception
) -> None:
    """Log the traceback and exception class without its potentially sensitive text."""
    redacted_error = RuntimeError("Exception details redacted.")
    target_logger.exception(
        "%s exception_type=%s sqlstate=%s details=redacted",
        event_code,
        type(error).__name__,
        _extract_sqlstate(error),
        exc_info=(type(error), redacted_error, error.__traceback__),
    )


EDUCATION_LEVEL_LABELS = {
    "secondary": "Secondary School",
    "higher-secondary": "Higher Secondary / Pre-University",
    "diploma": "Diploma",
    "undergraduate": "Undergraduate",
    "postgraduate": "Postgraduate",
    "doctorate": "Doctorate",
}
_EDUCATION_LEVEL_KEYS_BY_LABEL = {
    label.casefold(): key for key, label in EDUCATION_LEVEL_LABELS.items()
}

_KNOWN_DEGREES = {
    "Secondary School Certificate",
    "Higher Secondary Certificate",
    "Pre-University Certificate",
    "Senior Secondary Certificate",
    "Diploma in Engineering",
    "Diploma in Computer Applications",
    "Diploma in Business",
    "Diploma in Design",
    "Diploma in Healthcare",
    "B.E. / B.Tech",
    "B.Sc",
    "BCA",
    "B.Com",
    "BBA",
    "B.A",
    "B.Arch",
    "B.Des",
    "MBBS",
    "BDS",
    "B.Pharm",
    "BPT",
    "B.Sc Nursing",
    "LLB",
    "B.Ed",
    "BSW",
    "M.E. / M.Tech",
    "M.Sc",
    "MCA",
    "M.Com",
    "MBA",
    "M.A",
    "M.Arch",
    "M.Des",
    "MD",
    "MS",
    "M.Pharm",
    "MPT",
    "M.Sc Nursing",
    "LLM",
    "M.Ed",
    "MSW",
    "Ph.D.",
    "Doctor of Science (D.Sc.)",
    "Doctor of Letters (D.Litt.)",
    "Professional Doctorate",
}
_KNOWN_DEGREES_CASEFOLD = {degree.casefold() for degree in _KNOWN_DEGREES}

_KNOWN_FIELDS_OF_STUDY = {
    "Computer Science",
    "Information Technology",
    "Software Engineering",
    "Data Science",
    "Artificial Intelligence",
    "Cybersecurity",
    "Information Systems",
    "Mechanical Engineering",
    "Civil Engineering",
    "Electrical Engineering",
    "Electronics & Communication",
    "Chemical Engineering",
    "Automobile Engineering",
    "Aerospace Engineering",
    "Industrial Engineering",
    "Mechatronics",
    "Biotechnology",
    "Physics",
    "Chemistry",
    "Mathematics",
    "Biology",
    "Statistics",
    "Environmental Science",
    "Microbiology",
    "Accounting",
    "Finance",
    "Marketing",
    "Human Resources",
    "Business Administration",
    "Economics",
    "International Business",
    "Supply Chain Management",
    "English",
    "History",
    "Psychology",
    "Sociology",
    "Political Science",
    "Journalism",
    "Languages",
    "Social Work",
    "Architecture",
    "Interior Design",
    "Graphic Design",
    "Product Design",
    "Fashion Design",
    "User Experience Design",
    "Urban Planning",
    "Medicine",
    "Dentistry",
    "Pharmacy",
    "Nursing",
    "Physiotherapy",
    "Public Health",
    "Medical Laboratory Science",
    "Nutrition",
    "Law",
    "Education",
    "Curriculum & Instruction",
    "Educational Leadership",
    "Science",
    "Commerce",
    "Arts / Humanities",
    "Vocational Studies",
}
_KNOWN_FIELDS_CASEFOLD = {field.casefold() for field in _KNOWN_FIELDS_OF_STUDY}


def _verified_user_uuid(value: UUID | str) -> UUID:
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        raise ValueError("The verified Supabase user ID must be a UUID.") from None


def _optional_text(value: str | None) -> str | None:
    return value if value else None


def _deduplicated_skills(skills: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for skill in skills:
        normalized = skill.casefold()
        if normalized not in seen:
            seen.add(normalized)
            unique.append(skill)
    return unique


def _stored_education_level(basic: BasicInformation) -> str:
    if basic.education_level.casefold() == "other":
        return basic.education_level_other
    return EDUCATION_LEVEL_LABELS[basic.education_level]


def _stored_degree(basic: BasicInformation) -> str:
    if basic.degree.casefold() == "other":
        return basic.degree_other
    return basic.degree


def _stored_field_of_study(basic: BasicInformation) -> str:
    if basic.field_of_study.casefold() == "other":
        return basic.field_of_study_other
    return basic.field_of_study


def _append_experiences(
    basic_career: CareerSkills,
) -> list[ExperienceModel]:
    entries: list[ExperienceModel] = []
    for experience_type, submitted_entries in (
        ("internship", basic_career.internship_experience),
        ("professional", basic_career.professional_experience),
    ):
        for entry in submitted_entries:
            entries.append(
                ExperienceModel(
                    experience_type=experience_type,
                    company_name=_optional_text(entry.company),
                    role=entry.role,
                    duration=_optional_text(entry.duration),
                    responsibilities=_optional_text(entry.responsibilities),
                    technologies_used=_optional_text(entry.technologies),
                )
            )
    return entries


def _set_profile_values(profile: Profile, request: ProfileCreateRequest) -> None:
    basic = request.basic_information
    additional = request.additional_information

    profile.first_name = basic.first_name
    profile.last_name = basic.last_name
    profile.education_level = _stored_education_level(basic)
    profile.degree = _stored_degree(basic)
    profile.field_of_study = _stored_field_of_study(basic)
    profile.career_status = request.career_skills.career_status
    profile.achievements = _optional_text(additional.achievements)
    profile.updated_at = datetime.now(timezone.utc)

    profile.skills = [
        Skill(skill_name=skill)
        for skill in _deduplicated_skills(request.career_skills.technical_skills)
    ]
    profile.experiences = _append_experiences(request.career_skills)
    profile.projects = [
        ProjectModel(
            project_name=project.name,
            project_description=project.description,
            technologies_used=project.technologies,
        )
        for project in additional.projects
    ]
    profile.certifications = [
        CertificationModel(
            certification_name=certification.name,
            issuing_organization=certification.organization,
        )
        for certification in additional.certifications
    ]


def _education_form_values(stored_value: str) -> tuple[str, str]:
    education_key = _EDUCATION_LEVEL_KEYS_BY_LABEL.get(stored_value.casefold())
    if education_key is not None:
        return education_key, ""
    return "other", stored_value


def _degree_form_values(stored_value: str) -> tuple[str, str]:
    if stored_value.casefold() in _KNOWN_DEGREES_CASEFOLD:
        return stored_value, ""
    return "other", stored_value


def _field_form_values(stored_value: str) -> tuple[str, str]:
    if stored_value.casefold() in _KNOWN_FIELDS_CASEFOLD:
        return stored_value, ""
    return "other", stored_value


def _to_profile_response(profile: Profile) -> ProfileResponse:
    education_level, education_other = _education_form_values(profile.education_level)
    degree, degree_other = _degree_form_values(profile.degree)
    field_of_study, field_other = _field_form_values(profile.field_of_study)

    return ProfileResponse(
        id=profile.id,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        basic_information=BasicInformation(
            first_name=profile.first_name,
            last_name=profile.last_name,
            education_level=education_level,
            education_level_other=education_other,
            degree=degree,
            degree_other=degree_other,
            field_of_study=field_of_study,
            field_of_study_other=field_other,
        ),
        career_skills=CareerSkills(
            career_status=profile.career_status,
            technical_skills=[skill.skill_name for skill in profile.skills],
            internship_experience=[
                Experience(
                    company=experience.company_name or "",
                    role=experience.role or "",
                    duration=experience.duration or "",
                    responsibilities=experience.responsibilities or "",
                    technologies=experience.technologies_used or "",
                )
                for experience in profile.experiences
                if experience.experience_type == "internship"
            ],
            professional_experience=[
                Experience(
                    company=experience.company_name or "",
                    role=experience.role or "",
                    duration=experience.duration or "",
                    responsibilities=experience.responsibilities or "",
                    technologies=experience.technologies_used or "",
                )
                for experience in profile.experiences
                if experience.experience_type == "professional"
            ],
        ),
        additional_information=AdditionalInformation(
            projects=[
                Project(
                    name=project.project_name,
                    description=project.project_description,
                    technologies=project.technologies_used,
                )
                for project in profile.projects
            ],
            certifications=[
                Certification(
                    name=certification.certification_name,
                    organization=certification.issuing_organization,
                )
                for certification in profile.certifications
            ],
            achievements=profile.achievements,
        ),
    )


def _profile_query(user_id: UUID):
    return (
        select(Profile)
        .where(Profile.supabase_user_id == user_id)
        .options(
            selectinload(Profile.skills),
            selectinload(Profile.experiences),
            selectinload(Profile.projects),
            selectinload(Profile.certifications),
        )
    )


def save_profile(
    db: Session,
    supabase_user_id: UUID | str,
    profile_data: ProfileCreateRequest,
) -> ProfileResponse:
    """Create or replace one user's complete profile snapshot atomically."""
    user_id = _verified_user_uuid(supabase_user_id)

    # A second attempt turns a concurrent first-create uniqueness race into an
    # update of the row that won the race. Every attempt is its own transaction.
    for attempt in range(2):
        try:
            with db.begin():
                profile = db.scalar(
                    _profile_query(user_id).with_for_update()
                )
                if profile is None:
                    profile = Profile(supabase_user_id=user_id)
                    db.add(profile)
                else:
                    # Delete the old snapshot before inserting its replacement;
                    # otherwise an unchanged skill can temporarily collide with
                    # the case-insensitive unique index during flush ordering.
                    profile.skills = []
                    profile.experiences = []
                    profile.projects = []
                    profile.certifications = []
                    db.flush()

                _set_profile_values(profile, profile_data)
                db.flush()
                response = _to_profile_response(profile)
            return response
        except IntegrityError:
            if attempt == 0:
                continue
            raise ProfileConflictError(
                "A concurrent profile save conflicted. Please retry the request."
            ) from None
        except SQLAlchemyError as exc:
            _log_redacted_exception(logger, "profile.save.persistence_failed", exc)
            raise ProfilePersistenceError("Profile data could not be saved.") from exc

    raise ProfileConflictError("A concurrent profile save conflicted. Please retry the request.")


def get_profile(
    db: Session,
    supabase_user_id: UUID | str,
) -> ProfileResponse | None:
    """Retrieve one user's profile and all child collections, or return None."""
    user_id = _verified_user_uuid(supabase_user_id)
    try:
        profile = db.scalar(_profile_query(user_id))
        return _to_profile_response(profile) if profile is not None else None
    except SQLAlchemyError as exc:
        _log_redacted_exception(logger, "profile.get.persistence_failed", exc)
        raise ProfilePersistenceError("Profile data could not be retrieved.") from exc

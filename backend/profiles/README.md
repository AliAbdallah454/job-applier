# Candidate profiles

`models.py` defines the detailed, user-supplied career record. Preserve narrative
descriptions of responsibilities, decisions, challenges, contributions and outcomes;
there is no CV-length limit. Structured lists supplement those descriptions.
Achievements may include metrics when the user knows them; none are generated here.

Profiles contain contact details, background, experience, projects, education,
skills, languages, certifications, links, additional information and job preferences.
Preferences describe what the user wants, rather than implying qualifications.

Experience, project, education and certification entries get stable IDs. Retain
these IDs when editing or round-tripping JSON, so future generated documents can
reference their source facts. Dates accept `YYYY` or `YYYY-MM`; missing dates stay
null and `current` is explicit. Unknown proficiency or achievements need not be filled.

```python
from backend.profiles.models import CandidateProfile, Experience

profile = CandidateProfile(
    user_id="example-user",
    full_name="Example Candidate",
    experiences=[
        Experience(
            organization="Example Company",
            role="Software engineering intern",
            start_date="2025-03",
            end_date="2025-08",
            description=(
                "Built Python scripts to collect supplier data each morning. "
                "Added retry handling for intermittent request failures and "
                "stored the records in PostgreSQL."
            ),
            technologies=["Python", "PostgreSQL"],
        )
    ],
)

payload = profile.model_dump_json(indent=2)
restored = CandidateProfile.model_validate_json(payload)
```

This first step adds models only: no database, API, document generation or AWS
resources. `version` identifies a profile revision; the future update service must
increment it after edits. `schema_version` identifies the data format. When an API
is added, derive `user_id` from the authenticated identity.

Document generation will read a selected profile revision and a job posting, then
produce separate outputs. It must preserve the profile and use only supported facts.
An original CV or Overleaf template can be attached through document storage later.

Never commit real profiles or personal CV data. Keep test examples fictional.

"""Profile facts survive edits and serialization without inventing missing details."""

import unittest

from pydantic import ValidationError

from backend.documents.models import GenerationInput
from backend.profiles.models import (
    CandidateProfile,
    Education,
    Experience,
    JobPreferences,
    Project,
    upgrade_profile,
)
from backend.profiles.skill_catalogue import CATEGORIES, OTHER_NAMES
from backend.profiles.skills import KNOWN_NAMES, SKILL_CATEGORIES, Skills, catalogue_entries


class ProfileTests(unittest.TestCase):
    def experience(self, **overrides):
        return Experience.model_validate(
            {
                "organization": "Example Company",
                "role": "Engineering intern",
                "description": "Built supplier data collectors, added retries and used PostgreSQL.",
                **overrides,
            }
        )

    def test_descriptive_facts_and_stable_ids_survive_json_round_trip(self):
        experience = self.experience(
            start_date="2024",
            end_date="2025-02",
            responsibilities=["Maintained Python collection scripts"],
            technologies=["Python", "PostgreSQL"],
        )
        profile = CandidateProfile(
            user_id="example-user",
            full_name="Example Candidate",
            experiences=[experience],
            projects=[Project(name="Example project", description="A personal learning project")],
            education=[Education(institution="Example University", qualification="BSc")],
        )
        restored = CandidateProfile.model_validate_json(profile.model_dump_json())
        self.assertEqual(profile, restored)
        self.assertEqual(restored.experiences[0].id, experience.id)
        self.assertEqual(restored.experiences[0].description, experience.description)
        self.assertEqual(restored.experiences[0].achievements, [])
        self.assertIsNone(restored.education[0].start_date)

    def test_invalid_dates_and_current_entries_are_rejected(self):
        for dates in [
            {"start_date": "2025-13"},
            {"start_date": "2025-01-01"},
            {"start_date": "2025-07", "end_date": "2025-06"},
            {"start_date": "2025", "end_date": "2024"},
            {"current": True, "end_date": "2025-06"},
        ]:
            with self.subTest(dates=dates), self.assertRaises(ValidationError):
                self.experience(**dates)

    def test_year_precision_does_not_invent_or_reject_overlapping_months(self):
        self.experience(start_date="2025-07", end_date="2025")
        self.experience(start_date="2025", end_date="2025-01")

    def test_duplicate_source_ids_are_rejected(self):
        with self.assertRaises(ValidationError):
            CandidateProfile(
                user_id="example-user",
                full_name="Example Candidate",
                experiences=[self.experience(id="same")],
                projects=[Project(id="same", name="Project", description="Description")],
            )

    def test_required_facts_and_unknown_fields_are_validated(self):
        for overrides in ({"description": "  "}, {"role": ""}, {"invented_field": "value"}):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                self.experience(**overrides)

    def test_preferences_are_separate_and_lists_are_not_shared(self):
        first = CandidateProfile(user_id="first", full_name="Example One")
        second = CandidateProfile(user_id="second", full_name="Example Two")
        first.preferences.contract_types.append("internship")
        self.assertEqual(second.preferences.contract_types, [])
        self.assertEqual(first.experiences, [])
        with self.assertRaises(ValidationError):
            JobPreferences(seniorities=["intern"])


class SkillTests(unittest.TestCase):
    def old_profile(self, skills, **fields):
        return {
            "user_id": "example-user",
            "full_name": "Example Candidate",
            "schema_version": 1,
            "skills": skills,
            **fields,
        }

    def test_each_skill_is_in_one_category_and_every_field_has_a_label(self):
        names = [skill.casefold() for category in CATEGORIES for skill in category.skills]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual([category.key for category in SKILL_CATEGORIES], list(Skills.model_fields))
        self.assertTrue(all(category.label and category.skills for category in SKILL_CATEGORIES))

    def test_only_a_listed_skill_in_its_own_category_is_accepted(self):
        skills = Skills(programming_languages=["Python", "C++"], databases=["PostgreSQL"])
        self.assertEqual(skills.model_dump(mode="json")["programming_languages"], ["Python", "C++"])
        for wrong in (
            {"programming_languages": ["Cobol 2"]},
            {"databases": ["Python"]},
            {"programming_languages": ["python"]},
            {"programming_languages": ["Python", "Python"]},
            {"cooking": ["Python"]},
        ):
            with self.subTest(wrong), self.assertRaises(ValidationError):
                Skills.model_validate(wrong)

    def test_a_profile_refuses_the_old_free_text_skills(self):
        for skills in ([{"name": "Python"}], ["Python"]):
            with self.subTest(skills), self.assertRaises(ValidationError):
                CandidateProfile(user_id="u", full_name="Name", skills=skills)

    def test_old_skills_move_to_their_categories(self):
        profile = CandidateProfile.model_validate(
            upgrade_profile(
                self.old_profile(
                    [
                        {"name": "Python"},
                        {"name": "scikit-learn", "description": None, "proficiency": None},
                        {"name": "C/C++"},
                        {"name": "CI/CD"},
                        {"name": "postgresql"},
                        {"name": "PYTHON"},
                    ]
                )
            )
        )
        self.assertEqual(profile.schema_version, 2)
        self.assertEqual(profile.skills.programming_languages, ["Python", "C", "C++"])
        self.assertEqual(profile.skills.ml_and_ai, ["Scikit-learn"])
        self.assertEqual(profile.skills.devops_and_tools, ["CI/CD"])
        self.assertEqual(profile.skills.databases, ["PostgreSQL"])
        self.assertIsNone(profile.additional_information)

    def test_no_fact_is_lost_when_the_catalogue_cannot_hold_an_old_skill(self):
        profile = CandidateProfile.model_validate(
            upgrade_profile(
                self.old_profile(
                    [
                        {"name": "Python", "proficiency": "Advanced", "description": "Daily use"},
                        {"name": "Fortran/Cobol"},
                        {"name": "Basket weaving", "description": "Regional finalist"},
                    ],
                    additional_information="Happy to relocate.",
                )
            )
        )
        self.assertEqual(profile.skills.programming_languages, ["Python"])
        self.assertEqual(
            profile.additional_information,
            "Happy to relocate.\n\nSkill details: Python: Advanced, Daily use; Fortran/Cobol; "
            "Basket weaving: Regional finalist",
        )

    def test_a_profile_in_todays_shape_passes_through_unchanged(self):
        data = {"user_id": "u", "full_name": "Name", "skills": {"databases": ["Redis"]}}
        self.assertEqual(upgrade_profile(data)["skills"], data["skills"])
        self.assertIsNone(upgrade_profile(data)["additional_information"])
        self.assertEqual(upgrade_profile("not a profile"), "not a profile")

    def test_every_aws_service_is_named_the_same_way(self):
        names = [skill for category in CATEGORIES for skill in category.skills]
        self.assertEqual([name for name in names if name.startswith("Amazon")], [])
        for spelling in ("Amazon ECS", "ECS", "AWS ECS", "amazon ecs"):
            self.assertEqual(
                catalogue_entries(spelling), [("cloud_and_infrastructure", "AWS ECS")], spelling
            )
        self.assertEqual(catalogue_entries("DynamoDB"), [("databases", "AWS DynamoDB")])
        self.assertEqual(catalogue_entries("Amazon DynamoDB"), [("databases", "AWS DynamoDB")])

    def test_other_names_lead_to_a_skill_in_the_catalogue(self):
        for other, name in OTHER_NAMES.items():
            self.assertEqual(KNOWN_NAMES[other.casefold()][1], name)
        self.assertEqual(catalogue_entries("Amazon Dynamo"), [("databases", "AWS DynamoDB")])

    def test_a_saved_profile_follows_a_renamed_skill(self):
        profile = CandidateProfile.model_validate(
            upgrade_profile(
                {
                    "user_id": "u",
                    "full_name": "Name",
                    "skills": {
                        "cloud_and_infrastructure": ["Amazon ECS", "AWS EC2", "AWS ECS"],
                        "databases": ["DynamoDB"],
                    },
                }
            )
        )
        self.assertEqual(profile.skills.cloud_and_infrastructure, ["AWS ECS", "AWS EC2"])
        self.assertEqual(profile.skills.databases, ["AWS DynamoDB"])

    def test_a_skill_taken_off_the_list_is_kept_as_a_note(self):
        data = {"user_id": "u", "full_name": "Name", "skills": {"testing": ["Gone", "pytest"]}}
        profile = CandidateProfile.model_validate(upgrade_profile(data))
        self.assertEqual(profile.skills.testing, ["pytest"])
        self.assertEqual(profile.additional_information, "Skill details: Gone")

    def test_a_profile_can_only_save_current_names(self):
        with self.assertRaises(ValidationError):
            Skills.model_validate({"cloud_and_infrastructure": ["Amazon ECS"]})

    def test_a_snapshot_frozen_before_the_catalogue_can_still_be_read(self):
        request = GenerationInput.model_validate(
            {
                "profile": self.old_profile([{"name": "Rust"}, {"name": "Basket weaving"}]),
                "job": {"job_id": "job", "title": "Engineer", "description": "Build things"},
                "model_id": "model",
                "cv_latex": "\\documentclass{article}",
                "prompt_version": 13,
            }
        )
        self.assertEqual(request.profile.skills.programming_languages, ["Rust"])
        self.assertIn("Basket weaving", request.profile.additional_information)

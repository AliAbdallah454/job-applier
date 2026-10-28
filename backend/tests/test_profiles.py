"""Profile facts survive edits and serialization without inventing missing details."""

import unittest

from pydantic import ValidationError

from backend.profiles.models import CandidateProfile, Education, Experience, JobPreferences, Project


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

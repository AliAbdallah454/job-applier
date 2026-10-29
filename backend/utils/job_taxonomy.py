"""Job labels shared by classification results and user job preferences."""

from typing import Literal

Seniority = Literal["entry", "junior", "mid", "senior"]
ContractType = Literal[
    "permanent",
    "fixed_term",
    "internship",
    "apprenticeship",
    "freelance",
    "temporary",
]
JobCategory = Literal[
    "software_engineer",
    "devops_engineer",
    "cloud_engineer",
    "ai_engineer",
    "ml_engineer",
    "data_engineer",
    "data_scientist",
    "security_engineer",
    "qa_engineer",
    "systems_engineer",
    "technical_support",
    "technical_management",
    "other_tech",
    "non_technical",
    "unknown",
]

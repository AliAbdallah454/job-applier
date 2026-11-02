"""The skills a user can pick, one enum per category; this is the only list of them.

The web app shows them through /api/options and gets their types from the OpenAPI schema, so it
never holds its own copy. To add a skill, add it here and run `npm run types`. A name must appear
in one category only, as old free-text skills are matched to the catalogue by name.
"""

from enum import StrEnum
from typing import get_args

from pydantic import BaseModel, ConfigDict, Field, model_validator


def skill_enum(name, *skills):
    return StrEnum(name, {skill: skill for skill in skills})


ProgrammingLanguage = skill_enum(
    "ProgrammingLanguage",
    "Python",
    "JavaScript",
    "TypeScript",
    "Java",
    "C",
    "C++",
    "C#",
    "Go",
    "Rust",
    "SQL",
    "Bash",
    "Kotlin",
    "Swift",
    "PHP",
    "Ruby",
    "Scala",
    "R",
    "MATLAB",
    "Dart",
    "PowerShell",
    "Lua",
    "Perl",
    "Haskell",
    "Elixir",
    "Julia",
    "Objective-C",
    "Solidity",
    "Assembly",
)

FrontendSkill = skill_enum(
    "FrontendSkill",
    "React",
    "Next.js",
    "Vue.js",
    "Nuxt",
    "Angular",
    "Svelte",
    "HTML",
    "CSS",
    "Sass",
    "Tailwind CSS",
    "Bootstrap",
    "Redux",
    "TanStack Query",
    "Vite",
    "Webpack",
    "jQuery",
    "D3.js",
    "Three.js",
    "WebAssembly",
    "Accessibility",
    "React Native",
    "Flutter",
    "SwiftUI",
    "Jetpack Compose",
    "Electron",
)

BackendSkill = skill_enum(
    "BackendSkill",
    "REST APIs",
    "GraphQL",
    "gRPC",
    "WebSockets",
    "HTTP",
    "Microservices",
    "Event-driven architecture",
    "Node.js",
    "Express",
    "NestJS",
    "FastAPI",
    "Django",
    "Flask",
    "Pydantic",
    "SQLAlchemy",
    "Celery",
    "Spring Boot",
    "Hibernate",
    ".NET",
    "ASP.NET Core",
    "Laravel",
    "Ruby on Rails",
    "Prisma",
    "OAuth",
    "RabbitMQ",
)

DatabaseSkill = skill_enum(
    "DatabaseSkill",
    "PostgreSQL",
    "MySQL",
    "MariaDB",
    "SQLite",
    "Microsoft SQL Server",
    "Oracle Database",
    "MongoDB",
    "Redis",
    "DynamoDB",
    "Cassandra",
    "Elasticsearch",
    "OpenSearch",
    "Neo4j",
    "ClickHouse",
    "RocksDB",
    "Firestore",
    "Supabase",
    "InfluxDB",
    "TimescaleDB",
    "pgvector",
    "Pinecone",
    "Weaviate",
    "Qdrant",
    "Milvus",
    "Chroma",
)

MlAiSkill = skill_enum(
    "MlAiSkill",
    "PyTorch",
    "TensorFlow",
    "Keras",
    "JAX",
    "Scikit-learn",
    "XGBoost",
    "LightGBM",
    "Hugging Face Transformers",
    "LangChain",
    "LangGraph",
    "LlamaIndex",
    "OpenCV",
    "spaCy",
    "NLTK",
    "PEFT",
    "LoRA",
    "Optuna",
    "MLflow",
    "Metaflow",
    "Weights & Biases",
    "ONNX",
    "vLLM",
    "Ollama",
    "CUDA",
    "LLM integration",
    "RAG",
    "Prompt engineering",
    "Fine-tuning",
    "AI agents",
    "Vector search",
    "Generative AI",
    "Deep learning",
    "Computer vision",
    "NLP",
    "Reinforcement learning",
)

DataEngineeringSkill = skill_enum(
    "DataEngineeringSkill",
    "NumPy",
    "Pandas",
    "Polars",
    "Spark",
    "PySpark",
    "Kafka",
    "Flink",
    "Airflow",
    "Dagster",
    "Prefect",
    "dbt",
    "Snowflake",
    "BigQuery",
    "Redshift",
    "Databricks",
    "Hadoop",
    "Hive",
    "Trino",
    "ETL",
    "Data modeling",
    "Data warehousing",
    "Power BI",
    "Tableau",
    "Looker",
)

CloudInfrastructureSkill = skill_enum(
    "CloudInfrastructureSkill",
    "AWS",
    "AWS EC2",
    "AWS Lambda",
    "Amazon S3",
    "Amazon ECS",
    "Amazon EKS",
    "AWS Fargate",
    "Amazon RDS",
    "Amazon SQS",
    "Amazon SNS",
    "Amazon VPC",
    "Amazon CloudWatch",
    "Amazon API Gateway",
    "AWS IAM",
    "AWS CDK",
    "AWS CloudFormation",
    "AWS Step Functions",
    "AWS Bedrock",
    "Amazon SageMaker",
    "Google Cloud",
    "Microsoft Azure",
    "Cloudflare",
    "Vercel",
    "DigitalOcean",
    "Docker",
    "Kubernetes",
    "Helm",
    "Terraform",
    "Pulumi",
    "Ansible",
    "Linux",
    "Nginx",
    "Serverless",
    "Networking",
)

DevOpsToolSkill = skill_enum(
    "DevOpsToolSkill",
    "Git",
    "GitHub",
    "GitHub Actions",
    "GitLab CI",
    "CI/CD",
    "Jenkins",
    "CircleCI",
    "Argo CD",
    "Observability",
    "Prometheus",
    "Grafana",
    "Datadog",
    "OpenTelemetry",
    "ELK Stack",
    "Sentry",
    "OWASP",
    "Postman",
    "Jira",
    "Agile",
    "Scrum",
)

TestingSkill = skill_enum(
    "TestingSkill",
    "pytest",
    "unittest",
    "JUnit",
    "Jest",
    "Vitest",
    "Testing Library",
    "Cypress",
    "Playwright",
    "Selenium",
    "k6",
    "Test-driven development",
)


def category(title):
    return Field(default_factory=list, title=title)


class Skills(BaseModel):
    """The user's skills by category; the field order is the order the web app shows."""

    model_config = ConfigDict(extra="forbid")

    programming_languages: list[ProgrammingLanguage] = category("Programming languages")
    ml_and_ai: list[MlAiSkill] = category("ML & AI")
    backend: list[BackendSkill] = category("Backend")
    frontend: list[FrontendSkill] = category("Frontend & mobile")
    databases: list[DatabaseSkill] = category("Databases")
    data_engineering: list[DataEngineeringSkill] = category("Data engineering")
    cloud_and_infrastructure: list[CloudInfrastructureSkill] = category("Cloud & infrastructure")
    devops_and_tools: list[DevOpsToolSkill] = category("DevOps & tools")
    testing: list[TestingSkill] = category("Testing")

    @model_validator(mode="after")
    def no_repeats(self):
        for key in type(self).model_fields:
            skills = getattr(self, key)
            if len(skills) != len(set(skills)):
                raise ValueError(f"{key} lists the same skill twice")
        return self


class SkillCategory(BaseModel):
    """One group of the catalogue, as offered to the web app."""

    key: str
    label: str
    skills: list[str]


SKILL_CATEGORIES = [
    SkillCategory(
        key=key,
        label=field.title,
        skills=[skill.value for skill in get_args(field.annotation)[0]],
    )
    for key, field in Skills.model_fields.items()
]
CATALOGUE = {
    skill.casefold(): (group.key, skill) for group in SKILL_CATEGORIES for skill in group.skills
}


def catalogue_entries(name):
    """The (category, skill) pairs for a free-text name; "C/C++" is two, and an unknown one none."""
    for parts in ([name], name.split("/")):
        entries = [CATALOGUE.get(part.strip().casefold()) for part in parts]
        if all(entries):
            return entries
    return []

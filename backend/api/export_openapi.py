"""Print the OpenAPI schema; the web app's TypeScript types are generated from it."""

import json

from backend.api.app import create_app

if __name__ == "__main__":
    print(json.dumps(create_app().openapi(), indent=2, sort_keys=True))

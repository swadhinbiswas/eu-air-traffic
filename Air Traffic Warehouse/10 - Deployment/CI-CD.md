[« Back to Index](../00%20-%20Index.md)

# CI/CD (Continuous Integration / Continuous Deployment)

Automated pipelines ensure code quality.

## CI Pipeline (`ci.yml`)
Runs on every Pull Request:
1. Checks formatting (`ruff format --check`)
2. Lints code (`ruff check`)
3. Runs Unit Tests (`pytest`)

## CD Pipeline
Merges to main trigger the deployment of updated documentation or docker images.

---
[« Back to Index](../00%20-%20Index.md)

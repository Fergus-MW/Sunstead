# Lambda container image for the Central KG API.
# Built linux/amd64 (matches Lambda's x86_64 runtime).
FROM public.ecr.aws/lambda/python:3.12

# Install build deps for any wheels that need them, then drop them again.
# (asyncpg / pydantic-core ship manylinux wheels for x86_64/py3.12, but
# keeping gcc around is cheap insurance.)
RUN microdnf install -y gcc && microdnf clean all

# Install deps first so layer caching works across code edits.
COPY pyproject.toml ${LAMBDA_TASK_ROOT}/
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir \
      "fastapi>=0.115" \
      "mangum>=0.18" \
      "pydantic>=2.7" \
      "pydantic-settings>=2.4" \
      "asyncpg>=0.29" \
      "sqlalchemy[asyncio]>=2.0.30" \
      "pgvector>=0.3.6" \
      "anthropic>=0.40" \
      "python-dotenv>=1.0" \
      "httpx>=0.27" \
      "orjson>=3.10"

# App code.
COPY app ${LAMBDA_TASK_ROOT}/app

# Lambda calls handler = app.main.handler (Mangum wrapping FastAPI).
CMD ["app.main.handler"]

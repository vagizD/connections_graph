FROM python:3.12-slim

# Install uv for fast, reliable dependency installation
COPY --from=ghcr.io/astral-sh/uv:0.6.5 /uv /uvx /bin/

WORKDIR /app

# Configure Python and application environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PROTOTYPE_MODE=C \
    HOST=0.0.0.0 \
    PORT=8000

# Copy dependency definition files first for optimized layer caching
COPY pyproject.toml uv.lock .python-version ./

# Install locked dependencies into local virtual environment (.venv)
RUN uv sync --frozen

# Place virtual environment on PATH
ENV PATH="/app/.venv/bin:$PATH"

# Pre-cache the lightweight ONNX embedding model inside the image for instant offline startup
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

# Copy application code, tests, synthetic fixtures, and entrypoint
COPY app/ ./app/
COPY tests/ ./tests/
COPY data/fixtures/ ./data/fixtures/
COPY run.py seed_demo.py ./

EXPOSE 8000

# Default launcher respects PROTOTYPE_MODE (A, B, or C)
CMD ["python", "run.py", "--host", "0.0.0.0", "--port", "8000"]

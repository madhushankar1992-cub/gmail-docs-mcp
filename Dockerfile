FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MCP_TRANSPORT=http \
    MCP_HOST=0.0.0.0

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install .

# Railway injects PORT; the server reads it when MCP_PORT is unset.
CMD ["python", "-m", "gmail_docs_mcp", "--transport", "http"]

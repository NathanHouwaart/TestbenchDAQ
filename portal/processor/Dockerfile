FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY tbdaq /app/tbdaq
RUN pip install --no-cache-dir .
ENV PYTHONPATH=/app
CMD ["python", "-m", "tbdaq.processor", "--data-root", "/data"]

FROM python:3.12-slim
WORKDIR /app
COPY portal/api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY tbdaq/portal.py /app/tbdaq/portal.py
COPY portal/api/app.py /app/portal/api/app.py
ENV PYTHONPATH=/app
ENV TBDAQ_DATA_ROOT=/data
EXPOSE 8000
CMD ["uvicorn", "portal.api.app:app", "--host", "0.0.0.0", "--port", "8000"]

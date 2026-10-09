FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .
USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status == 200 else 1)"

# Single process on purpose: game state lives in memory (see README "Scaling limits").
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]

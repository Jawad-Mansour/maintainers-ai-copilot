FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install CPU-only torch first so sentence-transformers/transformers
# don't pull the 3GB CUDA build from PyPI default index.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

RUN pip install --no-cache-dir -r requirements.txt

# Download spaCy model used by NER (Phase 7)
RUN python -m spacy download en_core_web_sm

COPY . .

EXPOSE 8001
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]

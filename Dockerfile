FROM python:3.11-slim

WORKDIR /app

# Prevent Python from writing pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create downloads directory and seen_ids file to avoid permission issues
RUN mkdir -p downloads && touch seen_ids.txt

EXPOSE 5000

# Run with Gunicorn (4 workers, binding to 0.0.0.0:5000)
CMD ["gunicorn", "--workers=4", "--bind=0.0.0.0:5000", "app:app"]
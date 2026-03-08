.PHONY: build run-flask run-gunicorn up up-oidc down clean

IMAGE_NAME = skt-proxy

build:
	docker build -t $(IMAGE_NAME) .

run-flask:
	uv run flask --app app run --debug --host=0.0.0.0 --port=5002

run-gunicorn:
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run gunicorn --workers=1 --threads=4 --bind=0.0.0.0:5002 --access-logfile - --error-logfile - --log-level debug app:app

up:
	docker compose up -d skt-proxy

up-oidc:
	docker compose up -d

down:
	docker compose down

clean:
	rm -rf .venv cache.db seen_ids.txt downloads/ static/covers/

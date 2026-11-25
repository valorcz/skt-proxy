.PHONY: build build-frontend run-flask run-gunicorn test up down clean

IMAGE_NAME = skt-proxy

build:
	docker build -t $(IMAGE_NAME) .

build-frontend:
	cd frontend && npm ci && npm run build

run-flask:
	uv run flask --app skt_proxy.app run --debug --host=0.0.0.0 --port=5002

run-gunicorn:
	OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run gunicorn --workers=1 --threads=4 --bind=0.0.0.0:5002 --access-logfile - --error-logfile - --log-level debug skt_proxy.app:app

test:
	uv run pytest -v

up:
	docker compose up -d skt-proxy

down:
	docker compose down

clean:
	rm -rf .venv data/cache.db data/covers/* frontend/dist src/skt_proxy/static/dist

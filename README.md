
## Configuration

Use the following `.env` file content:

```
SKT_USERNAME=<username>
SKT_PASSWORD=<password>
```

## Running locally

```bash
OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES uv run gunicorn --workers=1 --threads=4 --bind=127.0.0.1:5000 app:app
```

The `OBJC_DISABLE_INITIALIZE_FORK_SAFETY` variable is for `gunicorn` to run well on MacOS.


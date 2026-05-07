# SMMS Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
uvicorn app.main:app --reload --port 8000
```

Then open http://localhost:8000/docs.

To get a dev token:
```bash
curl -X POST http://localhost:8000/api/v1/auth/dev-token | jq -r .token
```
Use it as `Authorization: Bearer <token>`.

Run the worker:
```bash
celery -A app.infrastructure.queue.celery_app.celery_app worker -Q workflows,publish -l info
```

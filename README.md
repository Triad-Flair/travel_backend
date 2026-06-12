# TripSync FastAPI Backend

## Production deployment

- Use `fastapi_backend/deploy.sh` for a Vultr Ubuntu VPS.
- Copy `fastapi_backend/.env.production.example` to `fastapi_backend/.env` on the server and fill all secrets.
- Default production domain is `api.travellersin.com`.

Example:

```bash
REPO_URL=git@github.com:your-org/TripSync.git \
GIT_BRANCH=main \
DOMAIN=api.travellersin.com \
FRONTEND_ORIGIN=https://travellersin.com \
LETSENCRYPT_EMAIL=admin@travellersin.com \
bash fastapi_backend/deploy.sh
```

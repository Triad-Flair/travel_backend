# TripSync FastAPI Backend

## Production deployment

- Use `fastapi_backend/deploy.sh` for a Vultr Ubuntu VPS.
- Copy `fastapi_backend/.env.production.example` to `fastapi_backend/.env` on the server and fill all secrets.
- Default production domain is `api.trawellbuddy.com`.

Example:

```bash
REPO_URL=git@github.com:your-org/TripSync.git \
GIT_BRANCH=main \
DOMAIN=api.trawellbuddy.com \
FRONTEND_ORIGIN=https://trawellbuddy.com \
LETSENCRYPT_EMAIL=admin@trawellbuddy.com \
bash fastapi_backend/deploy.sh
```
# tf_backend

"""Auth router package.

Routes
------
POST /api/v1/auth/login                     (routes_session)
POST /api/v1/auth/logout                    (routes_session)
POST /api/v1/auth/demo-login                (routes_session)
GET  /api/v1/auth/status                    (routes_session)
POST /api/v1/auth/active-profile            (routes_session)
POST /api/v1/auth/invites/lookup            (routes_invites — token in body)
POST /api/v1/auth/invites/redeem            (routes_invites — token + password in body)
GET  /api/v1/auth/google/login              (routes_google)
GET  /api/v1/auth/google/callback           (routes_google)
POST /api/v1/auth/invites/google            (routes_google — token in body)
GET  /api/v1/auth/google/invite-callback    (routes_google)

All auth/invite responses carry ``Cache-Control: no-store`` to prevent
credential caching by proxies or browsers.  CSRF: all unsafe methods check
the Origin or Referer header against the configured public base URL
(AuthPathMiddleware); SameSite=Lax is the belt, the check is the suspenders.
"""

from fastapi import APIRouter

from snore.api.routers.auth import routes_google, routes_invites, routes_session

router = APIRouter()
router.include_router(routes_session.router)
router.include_router(routes_invites.router)
router.include_router(routes_google.router)

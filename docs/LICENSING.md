# ManuCodeAI licensing service

The desktop app remains a local PySide6 application. The licensing API is a separate FastAPI service deployed alongside a persistent PostgreSQL database.

## Commercial rules

- A Clerk account receives one server-recorded 15-minute trial, starting at its first successful `GET /api/v1/me`.
- Trial state is keyed by the immutable Clerk user ID, not the computer, installation, or local clock.
- A successful KES 250 Paystack transaction grants 14 days.
- Each 14-day renewal is a new Paystack checkout. This keeps M-PESA available; M-PESA reusable authorizations are not available for automatic recurring charges in Kenya.
- Suspended accounts are denied access even if a trial or paid period remains.
- The configured admin email receives the `super_admin` capability after Clerk identity and verified primary email are checked.

## API endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /health` | Render health check |
| `GET /api/v1/me` | Create/read trial and return current entitlement |
| `POST /api/v1/billing/checkout` | Initialize a KES 250 Paystack checkout |
| `GET /api/v1/billing/payments` | Current user's payment history |
| `POST /api/v1/paystack/webhook` | Validate Paystack signature and verify successful transactions |
| `GET /api/v1/admin/overview` | Admin totals and revenue |
| `GET /api/v1/admin/users` | User/trial/subscription list |
| `GET /api/v1/admin/payments` | Payment ledger |
| `PATCH /api/v1/admin/users/{clerk_user_id}` | Suspend or restore an account |
| `POST /api/v1/admin/users/{clerk_user_id}/grant` | Grant a number of access days |
| `GET /admin` | Browser admin dashboard |
| `GET /api/docs` | API documentation |

## Clerk setup

1. Create a Clerk application.
2. Create a **public OAuth application** for ManuCodeAI.
3. Enable **Device Authorization Grant** for that OAuth application.
4. Assign scopes `openid profile email offline_access`.
5. Configure its issuer, JWKS URL, Frontend API URL, OAuth client ID, publishable key, and secret key as service environment variables.
6. Set `MANUCODEAI_ADMIN_EMAILS=kiptooe142@gmail.com`.
7. Set `CLERK_AUTHORIZED_PARTIES` to the exact browser origins used by the admin dashboard. The desktop OAuth token is checked against the configured OAuth client ID.

Clerk's Device Authorization Grant is intended for native applications and CLI clients: the desktop displays a short user code, opens the verification page in a browser, and polls for tokens. Never embed a Clerk client secret in the desktop app.

## Paystack setup

1. Activate the Paystack business account and enable the payment methods you intend to accept.
2. Add the server-side secret key to the Render environment only.
3. Set `PAYSTACK_CALLBACK_URL` to a public completion page on the licensing host.
4. Configure the Paystack webhook URL as `https://YOUR-LICENSING-HOST/api/v1/paystack/webhook`.
5. Use the test secret key and test transactions before switching to live mode.

The desktop client must never receive the Paystack secret key. The webhook handler checks the HMAC signature and independently verifies the transaction's status, reference, amount, and currency before extending access.

## Render deployment

The repository includes `render.yaml`. Create the service from the blueprint, attach a persistent PostgreSQL database (or provide a Neon PostgreSQL connection string), then set all `sync: false` environment variables in the Render dashboard.

Required environment values:

- `DATABASE_URL`
- `CLERK_ISSUER`
- `CLERK_JWKS_URL`
- `CLERK_FRONTEND_API_URL`
- `CLERK_OAUTH_CLIENT_ID`
- `CLERK_SECRET_KEY`
- `CLERK_PUBLISHABLE_KEY`
- `CLERK_AUTHORIZED_PARTIES`
- `MANUCODEAI_ADMIN_EMAILS`
- `PAYSTACK_SECRET_KEY`
- `PAYSTACK_CALLBACK_URL`

The Render blueprint intentionally does not contain secrets. Do not commit real credentials.

## Local development

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pip install -r desktop\requirements.txt
Copy-Item .env.example .env
uvicorn licensing_api.main:app --reload
```

The default local database is SQLite. Production should use PostgreSQL. Run the entitlement tests with:

```powershell
python -m unittest tests.test_licensing -v
```

## Implementation status

The licensing API and admin dashboard are the initial backend implementation. The remaining integration work is to connect the desktop Account page to Clerk's device authorization flow, store tokens with the OS credential manager, check entitlements before and during coding sessions, open Paystack checkout in the system browser, and test the complete payment/webhook lifecycle in Paystack test mode.

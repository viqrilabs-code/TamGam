# Go-Live UAT Report

Date: 2026-03-12
Workspace: TamGam

## 1) Automated Validation (Completed)

| ID | Area | Command | Result |
|---|---|---|---|
| A-01 | Full backend unit suite | `venv\\Scripts\\python.exe -m pytest -q` | PASS (80 passed, 0 failed) |
| A-02 | Coverage gate | `venv\\Scripts\\python.exe -m pytest -q --cov=app.core.config --cov=app.core.security --cov=app.core.dependencies --cov=app.services.plan_limits --cov=app.services.razorpay_service --cov=app.services.gemini_key_manager --cov=app.schemas.tutor --cov-report=term-missing --cov-fail-under=70` | PASS (80.34%) |
| A-03 | Subscription/payment regression subset | `venv\\Scripts\\python.exe -m pytest -q tests/test_services_razorpay_service.py tests/test_subscriptions_plans.py tests/test_subscriptions_helpers.py tests/test_core_dependencies.py` | PASS (20 passed) |
| A-04 | Python syntax compile | `venv\\Scripts\\python.exe -m compileall -q app tests` | PASS |
| A-05 | App import smoke (CI-style env) | `venv\\Scripts\\python.exe -c "from app.main import app; print('app_ok', bool(app))"` | PASS (`app_ok True`) |

## 1.1) Live Endpoint Smoke (Executed)

| ID | Endpoint | Result |
|---|---|---|
| L-01 | `https://tamgam.in/` | PASS (HTTP 200) |
| L-02 | `https://tamgam.in/api/v1/subscriptions/plans` | FAIL (HTTP 503) |
| L-03 | `https://tamgam.in/api/v1/subscriptions/me` | FAIL (HTTP 503) |
| L-04 | `https://tamgam-api-404162620746.asia-south1.run.app/health` | FAIL (HTTP 503) |
| L-05 | `https://tamgam-api-404162620746.asia-south1.run.app/api/docs` | FAIL (HTTP 503) |
| L-06 | `https://tamgam-api-404162620746.asia-south1.run.app/api/v1/subscriptions/plans` | FAIL (HTTP 503) |

### Live Status Note

- Current external smoke indicates API availability/routing issue (`503`) and is a **go-live blocker** until resolved.

## 1.2) Infrastructure Diagnostics (Executed)

| ID | Check | Result |
|---|---|---|
| I-01 | Cloud Run IAM policy for `tamgam-api` | Initially missing invoker bindings (blocked public traffic/webhooks). |
| I-02 | Applied fix | Added `roles/run.invoker` for `allUsers` on `tamgam-api`. |
| I-03 | Cloud Run request logs | New request failed with HTTP 500 and message: `The request failed because billing is disabled for this project.` |
| I-04 | Billing account status | `billingAccounts/016C0F-0B1E95-1596E5` is `open: false` (closed). |

### Infra Blockers

- Webhook calls from Razorpay previously failed with HTTP 403 due unauthenticated invocation policy.
- Current hard blocker is billing: Cloud Run cannot reliably serve API traffic while linked billing account is closed.

## 2) Critical Recent Changes Covered

- Subscription fallback pull-sync from Razorpay in `/subscriptions/me`.
- Webhook handling for `payment.failed` and `payment.captured` fallback activation.
- Razorpay handled events expanded.
- Teacher verification trust banner UI emphasis update.

## 3) Mandatory Production UAT (Run Before Launch)

| ID | Scenario | Expected Result | Status | Evidence |
|---|---|---|---|---|
| P-01 | Teacher signup/login | Teacher reaches teacher dashboard without errors | PENDING |  |
| P-02 | Teacher billing activation (live Razorpay) | Payment succeeds and teacher becomes subscribed | PENDING |  |
| P-03 | Webhook delivery (subscription + payment) | Events visible in Razorpay logs and backend logs | PENDING |  |
| P-04 | Post-payment redirect | Successful payment flow lands on dashboard and premium actions unlock | PENDING |  |
| P-05 | Failed payment handling | Subscription moves to past due and UI messaging is correct | PENDING |  |
| P-06 | Verification request flow | Teacher can send verification requests and progress updates correctly | PENDING |  |
| P-07 | Verified badge trust visibility | T badge appears in public-facing places after approval | PENDING |  |
| P-08 | Student core flow | Student can browse, enroll, access notes/assessments/Diya as per plan gates | PENDING |  |
| P-09 | Tuition requests flow | Incoming request accept/reject works and reflects in UI | PENDING |  |
| P-10 | Admin verification workflow | Admin can approve/reject verification and teacher status updates | PENDING |  |
| P-11 | Cross-browser smoke | Chrome + Edge desktop/mobile show no blocking UI issues | PENDING |  |
| P-12 | Android smoke | Login + dashboard + key actions work on Android app | PENDING |  |

## 4) Go/No-Go Rule

- GO only when all `P-*` rows are PASS with evidence.
- NO-GO if payment-webhook sync, post-payment unlock, or admin verification has any failure.

## 5) Notes

- Localhost environments do not receive Razorpay webhooks unless tunneled.
- Production verification must be run against the deployed production API/frontend.

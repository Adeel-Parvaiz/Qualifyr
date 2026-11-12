# Deploy Qualifyr on Render

The repository-root `render.yaml` defines two web services. Keep the existing Supabase
Postgres database and GitHub Actions discovery workflow; no new database is required.
PayPal remains Sandbox-only.

## Create the services

1. Push the application changes and `render.yaml` to `Adeel-Parvaiz/Qualifyr` on `main`.
   Never commit `.env`, `web/.env.local`, or a Firebase service-account file.
2. In Render, choose **New → Blueprint**, connect that repository, and use `render.yaml`.
3. Enter the requested environment variables in Render. The two services use different
   runtimes; the Python service's Root Directory stays empty, and the Node service uses `web`.
4. Confirm the actual service URLs in Render. The examples below assume
   `https://qualifyr-api.onrender.com` and `https://qualifyr-web.onrender.com`;
   use the assigned URLs if those names are unavailable.

## Python API environment

Copy these values from your local configuration into Render's **qualifyr-api → Environment**:

- `GTM_DATABASE_URL`: existing Supabase connection string, one line without surrounding quotes.
- `FIREBASE_PROJECT_ID`: the same project used by the frontend.
- `FIREBASE_SERVICE_ACCOUNT_JSON`: the complete contents of the Firebase service-account JSON
  file, preferably compacted to one line. A Windows `GOOGLE_APPLICATION_CREDENTIALS` path
  cannot work on Render.
- `GTM_ENCRYPTION_KEY`: the existing encryption key, unchanged, so saved user API keys decrypt.
- `GTM_GITHUB_TOKEN`: the verified token with repository/workflow access.
- `GTM_PAYPAL_CLIENT_ID`, `GTM_PAYPAL_CLIENT_SECRET`, `GTM_PAYPAL_WEBHOOK_ID`: Sandbox app values.
- `GTM_PAYPAL_CHECKOUT_RETURN_URL` and `GTM_PAYPAL_CHECKOUT_CANCEL_URL`:
  `https://qualifyr-web.onrender.com/paypal/return`.

Add any operator-level LLM or mailbox environment variables you want to keep using. User
API keys saved in Settings remain in the existing database. Never set `GTM_AUTH_DISABLED`
in production. Do not set `VERCEL` on either Render service.

## Next.js environment

Set these on **qualifyr-web → Environment**:

- `API_PROXY_TARGET=https://qualifyr-api.onrender.com` (the API's public HTTPS URL).
- `NEXT_PUBLIC_API_URL=/api` (browser requests stay on the frontend's origin).
- `NEXT_PUBLIC_SITE_URL=https://qualifyr-web.onrender.com`.
- All four `NEXT_PUBLIC_FIREBASE_*` values from `web/.env.local`.
- `FIREBASE_PROJECT_ID` and `FIREBASE_SERVICE_ACCOUNT_JSON`, matching the Python API.

`API_PROXY_TARGET` and `NEXT_PUBLIC_*` values are consumed during the Next.js build.
Rebuild/redeploy the frontend after changing them. Runtime-only restarts are insufficient.
Use the backend's public URL here; these free-plan services do not rely on private networking.

## External callbacks

1. Firebase Console → Authentication → Settings → Authorized domains: add the actual frontend
   hostname, for example `qualifyr-web.onrender.com` (no scheme or path).
2. PayPal Developer Dashboard → Sandbox app → Webhooks: change the existing webhook URL to
   `https://qualifyr-web.onrender.com/api/webhooks/paypal`. Preserve its event subscriptions.
   If you create a new webhook instead, update `GTM_PAYPAL_WEBHOOK_ID` on Render to its new ID.
3. Keep `GTM_DATABASE_URL` and `GTM_ENCRYPTION_KEY` configured as GitHub Actions secrets.
   Deploying to Render does not copy those secrets or publish local workflow changes.

## Verify

- API: `https://qualifyr-api.onrender.com/health` returns JSON with `status: ok`.
- Frontend proxy: `https://qualifyr-web.onrender.com/api/health` returns the same health JSON.
- Sign in, open Campaigns, and verify saved leads and API-key settings load.
- Start one campaign and watch its GitHub Actions job.
- Test a new PayPal checkout using a Sandbox Personal buyer account. Confirm the return page
  and payment status before claiming the payment flow works end to end.

Free instances can sleep when idle, so initial requests can be slow. Local exports and caches
are temporary on Render; Postgres and GitHub Actions artifacts remain the durable records.

Render Free web services block outbound SMTP ports 465 and 587. The Gmail SMTP **Send now**
flow therefore needs a paid API instance or a separate supported sending service; this
blueprint's free API can still preview drafts, run dry runs, and handle PayPal Sandbox.
Both free web services also share the workspace's monthly free instance-hour allowance.
See https://render.com/docs/free for the current limits.

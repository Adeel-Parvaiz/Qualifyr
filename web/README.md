# Qualifyr web UI

Next.js 16 + Tailwind v4 + shadcn/ui front end for the GTM Lead Engine. Talks to the FastAPI
backend (`python -m uvicorn gtm_engine.api.main:app --reload`, port 8000). Uses Supabase authentication.

```bash
npm install
npm run dev          # http://localhost:3000
```

Copy `.env.local.example` to `.env.local` and fill in the public Supabase URL and anon key.
`NEXT_PUBLIC_API_URL=/api` keeps browser requests on the same origin. Next.js forwards
`/api/*` to `API_PROXY_TARGET` (default `http://127.0.0.1:8000`) locally; Vercel uses
its Python function instead.

For one-domain ngrok testing:

1. Run the backend from the repository root: `python -m uvicorn gtm_engine.api.main:app --host 127.0.0.1 --port 8000`.
2. Set `NGROK_DOMAIN` in `.env.local` to your assigned hostname, without a scheme or path.
3. Run `npm.cmd run dev` from `web/`.
4. Stop the old tunnel before starting `ngrok http 3000 --url=YOUR-HOST.ngrok-free.dev`.
5. Register `https://YOUR-HOST.ngrok-free.dev/api/webhooks/paypal` in the same PayPal Sandbox app as your credentials.
6. Save its webhook ID as `GTM_PAYPAL_WEBHOOK_ID` in the root `.env` and restart the backend.
7. Add `https://YOUR-HOST.ngrok-free.dev/auth/callback` to Supabase Auth's allowed redirect URLs
   (also allow the callback with `?next=/reset-password` if testing password resets).

Opening the webhook URL in a browser uses GET and returns 405; PayPal uses POST.
Unsigned POST requests must fail signature verification. For normal UI testing, stay on
one origin for sign-in and navigation so the session cookies belong to that host.

Pages: Dashboard · Campaigns (run discovery, download CSV) · Leads (filter, inspect reasons,
suppress) · Outreach (review queue: preview, edit, approve/reject, send; sequence; activity).

Layout and components come from https://github.com/ceyhanmolla/shadcn-crm-dashboard (MIT).

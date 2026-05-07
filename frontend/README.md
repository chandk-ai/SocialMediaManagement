# SMMS Frontend (Next.js 14)

```bash
cd frontend
npm install
cp ../.env.example ../.env  # OKTA_*, NEXTAUTH_SECRET, BACKEND_URL
npm run dev
```

App is at http://localhost:3000.

The frontend never talks to the backend directly from the browser — every
request goes through `/api/proxy/*` which attaches the bearer token from
the NextAuth (Okta) session.

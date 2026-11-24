# Setting up Google sign-in (local)

The "Continue with Google" button appears only when a Google client ID is configured. Facebook sign-in is built but stays hidden until `GD_FACEBOOK_APP_ID` and `GD_FACEBOOK_APP_SECRET` are set.

## 1. Create the client ID

1. Open <https://console.cloud.google.com> and create a project (for example "Good Deeds").
2. Go to **APIs & Services → OAuth consent screen**:
   - User type: **External**.
   - Fill in the app name and your email.
   - Add your own Google account as a **test user**.
3. Go to **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**.
   - Authorized JavaScript origins: `http://localhost:5173` and `http://localhost`.
   - Authorized redirect URIs: leave empty. The app uses Google's popup button, which needs none.
4. Copy the **Client ID** (it ends in `.apps.googleusercontent.com`). The client secret is not used.

## 2. Configure the app

Copy `.env.example` to `.env` in the repo root and set:

```
GD_GOOGLE_CLIENT_ID=<your client id>
```

Then restart the stack:

```
docker compose up -d
```

When running the backend outside Docker, put the same line in `backend/.env` instead.

## 3. Check it

- `curl http://localhost:5173/api/auth/config` returns your client ID.
- The sign-in page at <http://localhost:5173> shows "Continue with Google".
- Signing in with your test-user account takes you to onboarding (age and consent, then phone).

## Notes

- New origins can take a few minutes to take effect in Google.
- For production, add the real `https://` origin to the same client and set `GD_GOOGLE_CLIENT_ID` in the deployment environment.
- If an account with the same email already exists (password or another provider), sign-in is refused with `email_exists`. Accounts are never linked automatically.

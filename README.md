# Accomplishment Portfolio — cloud edition

This version preserves your dashboard, project editing, categories, deliverable tags, evidence files, links and activity log. Records live in PostgreSQL and attachments in your private Supabase bucket `TomosAccomplishments`. The app never silently falls back to local storage. Keep the current local app and its data as a separate backup until migration is verified.

## 1. Set up database tables

1. Open your Supabase project.
2. Open **SQL Editor > New query**.
3. Open the included **setup.sql**, copy its complete contents into the editor and click **Run**.
4. Confirm it succeeds. This creates only objects whose names start with `portfolio_`. It does not import records yet.
5. Under Storage, verify **TomosAccomplishments** is private. Keep its exact capitalization. No public access policies are needed for this app. The trusted server accesses storage after user authentication.

If SQL reports an incompatible existing table, stop and keep the error. Do not drop tables containing records. Re-running setup.sql is supported only when existing portfolio tables use this package's schema.

## 2. Create your sign-in account

This app uses **approved email + password through Supabase Auth**, separate from your Supabase dashboard account or database password. It does not depend on Streamlit's private-app allowance. There is no public registration screen.

1. In Supabase open **Authentication > Users**.
2. Choose **Add user > Create new user** (labels can vary).
3. Enter your approved email, e.g. `tomo@earthrise.com`, and set an account password.
4. Enable automatic email confirmation when creating this owner-controlled account.
5. In Authentication sign-up settings, disable new-user sign-ups if available. This app never creates users automatically.

To grant another person access later, create their Auth account AND add their email to `allowed_emails` in Streamlit Secrets. Every approved user can view, edit, import and delete the shared portfolio. There are no separate viewer roles or per-person portfolios in this release. For a personal portfolio, leave only your own email approved. Share passwords privately or use your organization's account-provisioning process.

## 3. Get project settings privately

Find the project URL and API keys in the Supabase **Connect** dialog or **Project Settings > API Keys**.

You need:
- Project URL, `https://...supabase.co`.
- Publishable key, normally starting `sb_publishable_`.
- Secret API key, normally starting `sb_secret_`. The code also accepts a legacy service_role JWT.

The secret API key is powerful. Keep it only in server-side Streamlit Secrets. It never belongs in GitHub, screenshots, chat or application source. You do not need to supply the database password to this app. The publishable key is used for sign-in, while the server key accesses the shared records after the app verifies the account and approved email on each rerun.

## 4. Upload code to GitHub

Create a new **private** repository, e.g. `accomplishment-portfolio-cloud`.

Upload the contents of this package so these paths appear at the repository root:

    app.py
    auth_ui.py
    cloud_store.py
    setup.sql
    requirements.txt
    secrets.example.toml
    README.md
    .gitignore
    .streamlit/config.toml
    tests/test_cloud_store.py

Do NOT upload your portfolio.db, backup ZIP, attachments or a filled secrets.toml. Upload data only through the authenticated import tool after deployment. If Windows hides `.streamlit`, use GitHub's Add file > Create new file and enter `.streamlit/config.toml` as the filename.

## 5. Deploy on Streamlit

1. Open https://share.streamlit.io/ and choose **Create app**.
2. Select the new repository, branch `main`, main file `app.py`.
3. Select Python **3.12** in advanced settings.
4. In **Secrets**, paste the contents of `secrets.example.toml` and privately replace all placeholders with your real settings.
5. Keep bucket set to `TomosAccomplishments` and max_file_mb at 50 unless your bucket actually permits more.
6. Set allowed_emails to the accounts you approved.
7. Deploy. You should see a login page, not portfolio records.
8. Sign in using the Supabase Auth email and password created in step 2.

If you already deployed, enter secrets under App settings > Secrets and reboot. Streamlit's own private sharing may be used as an additional gate if available, but Supabase account sign-in remains required.

Local testing is optional: in a NEW folder copy the package, save credentials as `.streamlit/secrets.toml`, then run:

    python -m pip install -r requirements.txt
    python -m streamlit run app.py

## 6. Import your existing full backup

1. Open **Data & backup** in the cloud app.
2. Select your full local backup ZIP. Do not upload the application-source ZIP.
3. The app checks the SQLite database and every registered attachment without executing the SQL dump.
4. Review project, deliverable, link, attachment and activity counts.
5. Tick the confirmation and click **Import backup**.
6. Wait for uploads and the database transaction to finish.
7. The app checks record counts after import.
8. Open **Project library**, verify several projects, and use **Load download** followed by the download button to test attachments.
9. Test creating and editing a small temporary project. Verify it is still present after rebooting the Streamlit app. Then remove the temporary project if desired.

Import requires an **empty cloud portfolio**. It preserves IDs and avoids merging or overwriting existing records. Import tables commit together. Files upload first. If the connection fails during database commit, completion may be uncertain: refresh and inspect project counts before retrying. The code retains uploaded objects in that situation to avoid deleting files belonging to committed records. An incomplete import can leave unused objects under `imports/<id>/`; remove them only after checking which paths existing file records reference.

Current import limits: 200 MB ZIP, 400 MB expanded content, 50 MB SQLite database, 100,000 rows per table, 20,000 archive entries, and the configured per-file bucket limit. If any registered attachment is missing, too large, or has an inconsistent size, import stops before the first upload. Unregistered extra files also stop migration so evidence is not silently discarded.

## 7. Back up the cloud copy

Under **Data & backup**, click **Prepare full backup**, then **Download full backup (ZIP)**. The ZIP includes portfolio.db, portfolio.sql and registered attachments using the local format. Prepare a new ZIP after changes. Do not edit/delete files from another session during backup.

Cloud persistence does not replace backups. PostgreSQL database backups do not include the binary contents of Storage objects. Keep full portfolio ZIP copies separately and periodically test them.

## Troubleshooting

- Missing setup: run setup.sql, confirm Data API is enabled for public schema, then refresh.
- Sign-in failed: the user must exist in Supabase Authentication, have a confirmed email, and appear in allowed_emails. Their password is not the database password.
- Access denied: verify the publishable/server key pairing and project URL. New secret keys use only the apikey header. Never use a browser user token as the server key.
- Missing bucket/file: exact bucket name is `TomosAccomplishments`.
- Size limit: max_file_mb must not exceed the bucket/project limit. API and plan quotas still apply.
- Network failure: keep the backup and avoid repeat clicks while a request is running. A create/save outcome may be ambiguous after a timeout; refresh to check before trying again.
- All authorized people share full access. Revoke their email from Secrets and reboot to prevent further app use. Auth sessions are also verified with Supabase on every rerun.

## Validation and limits

Automated offline tests cover original-format backup import, ID/content preservation, unsafe ZIP and missing-file rejection, pagination, file cleanup, ambiguous-commit handling, authentication gating and request construction. No connection to your live Supabase project was made. Database SQL and hosted storage must be verified during the guided setup. UI behavior was checked structurally against the uploaded file; this build environment has no Streamlit runtime.

Reference documentation:
- https://supabase.com/docs/guides/getting-started/api-keys
- https://supabase.com/docs/guides/storage/security/access-control
- https://supabase.com/docs/guides/auth/users
- https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management

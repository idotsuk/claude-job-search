# Exporting your LinkedIn contacts for `/network-scan`

`/network-scan` needs a CSV of your LinkedIn connections (name, current company, position) to know which of your tracked listings' companies you already have a contact at. LinkedIn doesn't expose this via a simple "download connections" button — you request it through **Get a copy of your data**, a general data-export tool that also happens to include a `Connections.csv`.

## 1. Request the export

1. On LinkedIn (desktop web), click your profile photo → **Settings & Privacy**.
2. **Data privacy** → **Get a copy of your data**.
3. Choose **"Want something in particular? Select the data files you're most interested in"** (the fast path — a targeted export, not your full archive).
4. Check **Connections** only (unless you want other data too — it doesn't hurt, just makes the download bigger).
5. Click **Request archive**. LinkedIn emails you when it's ready — usually within a few minutes, occasionally up to 24 hours.

## 2. Download and locate the file

1. Open the email LinkedIn sends ("Your LinkedIn data export is ready") and click through, or go back to **Settings & Privacy → Data privacy → Get a copy of your data** — a **Download archive** link appears there once it's ready.
2. Download the `.zip`, extract it. Inside you'll find `Connections.csv`.

## 3. What the file looks like

```
Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing..."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Jane,Smith,https://www.linkedin.com/in/janesmith,,Google,PM Director,05 Aug 2026
...
```

Two things to know:
- **There's a preamble** before the real header row (a `Notes:` line and a disclaimer). `/network-scan` handles this automatically — it scans for the line starting `First Name,Last Name,...` rather than assuming row 1 is the header. If you ever inspect the file yourself, don't be surprised by the extra lines at the top.
- **Email addresses are frequently blank** — only shown for connections who opted in to sharing them. `/network-scan` doesn't need email; it only reads `Company` and `Position`.

## 4. Save it and point config.yaml at it

Save the extracted `Connections.csv` somewhere stable (LinkedIn's export contains real personal data about your connections — treat it like `config.yaml`, keep it out of git). Two reasonable options:

- `data/linkedin-contacts.csv` — inside this repo's `data/` tree, which is entirely gitignored already, so no extra setup needed.
- Anywhere else on disk (e.g. `~/Downloads/Connections.csv`) — also fine, as long as the path below points at it.

Then set in `config.yaml`:

```yaml
search:
  linkedin_contacts_csv_path: ~/Downloads/Connections.csv    # or: data/linkedin-contacts.csv
```

(`config.yaml.example` documents this field with the same default path — see the `search:` block.)

## 5. Re-export periodically

This is a point-in-time snapshot — LinkedIn doesn't keep it live-synced. Re-request the export every few months if your network has grown or changed jobs, and re-save it to the same path so `/network-scan` picks up the update on its next run.

## Privacy note

`Connections.csv` contains names, current employers, job titles, and (sometimes) email addresses of real people in your network. It's exactly the kind of file the repo's PII-guard CI check and `.gitignore` are meant to keep out of version control — never commit it, and keep it in a path that's already gitignored (`data/`) or otherwise outside the repo.

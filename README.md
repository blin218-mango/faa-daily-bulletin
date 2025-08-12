# FAA Daily Bulletin (free, GitHub Pages + Actions)

This project publishes a **daily, plain‑English bulletin** of FAA airport delays and events using the official **NAS Status XML** feed. It runs **for free** on GitHub Actions and is served by **GitHub Pages**.

---

## How it works
1. A scheduled GitHub Action runs the Python script once a day.
2. It fetches **airport events** from the NAS Status XML endpoint (`https://nasstatus.faa.gov/api/airport-status-information`).
3. The script converts technical terms (GDP, GS, AFP, etc.) to friendly language and builds `docs/index.html`.
4. GitHub Pages serves the HTML from the `docs/` folder.

> Source for the machine‑readable XML is documented in the FAA NAS Status User Guide (the site’s footer links to an XML page).

---

## Quick start (no coding experience needed)
1. **Create a GitHub repo** (e.g., `faa-daily-bulletin`).
2. **Upload everything** from this starter folder to your repo (keep the folder structure the same).
3. In your GitHub repo: **Settings → Pages → Build and deployment**  
   - **Source**: *Deploy from a branch*  
   - **Branch**: `main` and **Folder**: `/docs`
4. Go to **Actions** tab and enable workflows if prompted.
5. The Action runs on a schedule; to run now: **Actions → Build FAA Bulletin → Run workflow**.
6. Open your site at `https://<your-username>.github.io/faa-daily-bulletin/`.

---

## Change the run time
- The workflow uses **UTC**. Default is **14:15 UTC** (07:15 Los Angeles).
- Edit `.github/workflows/build.yml` → change the `cron:` line.

---

## Local test (optional)
- Install Python 3.10+.
- `pip install -r requirements.txt`
- `python build_site.py`  
- Open `docs/index.html` in your browser.

---

## Notes
- If the FAA changes XML tags, the script won’t break—it's defensive—but fields shown may be limited until updated.
- You can style the page by editing the HTML template inside `build_site.py`.
- This is **read‑only**; no personal data stored. History is not kept to keep the repo small (you can add a `/data` folder + CSV if you want).

---

## License
MIT (do whatever, no warranty).

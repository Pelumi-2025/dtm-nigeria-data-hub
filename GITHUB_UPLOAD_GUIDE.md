# Start afresh: delete the old repository and publish the DTM Nigeria Data Hub

Time needed: about 20 minutes of clicking, then 1–3 hours for the first automatic run.
Use Chrome or Edge on a computer, signed in to GitHub as **Pelumi-2025**.

---------------------------------------------------------------------
## PART A – Delete the old repository
1. Open https://github.com/Pelumi-2025/dtm-nigeria-data-hub/settings
2. Scroll to the bottom, to the red **Danger Zone**.
3. Click **Delete this repository** → **I want to delete this repository** → **I have read and understand these effects**.
4. Type `Pelumi-2025/dtm-nigeria-data-hub` exactly as shown, then click **Delete this repository**
   (GitHub may ask for your password or a code).
5. If you had connected Netlify to it: Netlify → the site → Site configuration → **Delete this site**.

Your older tracker (`dtm-nigeria-tracker`) is a different repository and is not affected.

---------------------------------------------------------------------
## PART B – Prepare the files on your computer
6. Download `dtm-nigeria-data-hub.zip` and unzip it (right-click → Extract All).
7. Open the unzipped `dtm-hub` folder. You should see:
   `data`, `dist`, `harvester`, `site`, `README.md`, `GITHUB_UPLOAD_GUIDE.md`, `requirements.txt`, `netlify.toml`
   (and a hidden `.github` folder – you will add that one by hand in Part D).

---------------------------------------------------------------------
## PART C – Create the new repository and upload the files
8. Open https://github.com/new
   - Repository name: `dtm-nigeria-data-hub`
   - Public
   - Tick **Add a README file**
   - Click **Create repository**
9. Click **Add file → Upload files**
   (or open https://github.com/Pelumi-2025/dtm-nigeria-data-hub/upload/main).
10. In the `dtm-hub` folder, select these 8 items together (Ctrl+click) and drag them onto the page:
    `data`, `dist`, `harvester`, `site`, `README.md`, `GITHUB_UPLOAD_GUIDE.md`, `requirements.txt`, `netlify.toml`
11. Wait until every file is listed (about 45 files). When asked about README.md, the uploaded one replaces the empty one.
12. In "Commit changes" type `Upload DTM Nigeria Data Hub` → **Commit changes**.
13. Check: the repository now shows the folders `data`, `dist`, `harvester`, `site`.
    Open `data/raw` – it must contain the four Excel files.

---------------------------------------------------------------------
## PART D – Add the workflow (the automatic harvester)
14. Click **Add file → Create new file**.
15. In the name box type exactly: `.github/workflows/harvest.yml`
    (each `/` creates a folder – you will see `.github` › `workflows` appear).
16. Open the separate `harvest.yml` file you downloaded (with Notepad), select all, copy, and paste it into the big box.
17. **Commit changes** → **Commit changes**.

---------------------------------------------------------------------
## PART E – Settings (two switches)
18. **Settings → Actions → General** → scroll to *Workflow permissions* → choose
    **Read and write permissions** → **Save**.
19. **Settings → Pages** → *Build and deployment* → *Source*: choose **GitHub Actions**.
20. Optional (more data): **Settings → Secrets and variables → Actions → New repository secret**
    - Name `DTM_API_KEY`, value = your free key from https://dtm-apim-portal.iom.int/

---------------------------------------------------------------------
## PART F – First run
21. Open the **Actions** tab. If you see a yellow notice, click
    **I understand my workflows, go ahead and enable them**.
22. Click **Harvest DTM Nigeria and publish the Data Hub** (left) → **Run workflow** → **Run workflow**.
23. Wait for the green tick (1–3 hours the first time). The run:
    - reads your Excel files,
    - crawls every page of dtm.iom.int/nigeria and ReliefWeb,
    - downloads every Mobility Tracking data file from HDX and dtm.iom.int into `data/raw/online/`,
    - reads report PDFs (ETT numbers, flash reports, biometric registration figures),
    - builds and publishes the site.
24. Your platform is live at **https://pelumi-2025.github.io/dtm-nigeria-data-hub/**
    (Settings → Pages shows the address). It then updates itself every 6 hours.

---------------------------------------------------------------------
## PART I – Switch on the AI chat box ("Ask the data")
The chat box answers any question about the dashboard data with Claude, which reads the exact numbers
from the dashboard (rounds, states, LGAs, camps/host communities, returnees, reasons, distinct counts,
ETT, flash reports, floods, biometric registration, publications, cross-checks).
1. Get an Anthropic API key: https://console.anthropic.com → API Keys → Create key (billing must be set up).
2. Open your live site, click **Ask the data** (bottom right) → **AI settings**.
3. Paste the key → **Save key**. The key stays in that browser only (it is never uploaded to GitHub).
   Every person who wants AI answers adds their own key; without one, the chat gives offline answers.
Never put the key in any file in the repository.

---------------------------------------------------------------------
## Adding files later
- Data files (several at once, any rounds): https://github.com/Pelumi-2025/dtm-nigeria-data-hub/upload/main/data/raw
  Name them `ne_mobility_….xlsx` or `nwnc_mobility_….xlsx` (also `ett_….xlsx`, `flood_….xlsx`, `incidents_….xlsx`).
- Report PDFs: https://github.com/Pelumi-2025/dtm-nigeria-data-hub/upload/main/data/reports
- Check runs: https://github.com/Pelumi-2025/dtm-nigeria-data-hub/actions

## If something goes wrong
- Red cross on *Save the harvest*: Part E step 18 was missed.
- 404 page: Pages source is not **GitHub Actions**, or no run has finished yet.
- "No publications harvested yet": the first full run has not finished.
- A file was not uploaded: open the folder in the repository and upload the missing file into it.

# Installing PEBP Studio

> **Research use only. Not for clinical use.** PEBP Studio is not a medical device. It has not been cleared or approved by any regulator or clinically validated, and nothing designed with it has been printed, cast or bench-tested yet. Clinical choices stay with the clinician: the software stores and flags them and never makes them.

PEBP Studio runs entirely on your own computer. It answers requests from this computer only (127.0.0.1) and sends nothing anywhere. Python is not needed for the download.

## Download

From the [Releases page](https://github.com/kkoka1116/pebp-designer/releases), pick the file for your computer:

| Computer | File |
| --- | --- |
| Mac with Apple silicon (M1 or later) | `PEBP-Studio-<version>-macos-apple-silicon.zip` |
| Mac with an Intel processor | `PEBP-Studio-<version>-macos-intel.zip` |
| Windows 10 or 11, 64-bit | `PEBP-Studio-<version>-windows-x64.zip` |

To check which Mac you have: Apple menu > About This Mac. "Chip: Apple M…" means Apple silicon, and "Processor: Intel" means Intel.

Each release also lists a SHA-256 checksum per file in `SHA256SUMS.txt`.

## macOS

1. Double-click the zip to unpack it, then drag **PEBP Studio.app** into Applications.
2. The app is not signed with an Apple developer certificate yet, so macOS blocks it the first time:
   - Open it once. When macOS says it cannot check the app, click **Done** (or **Cancel**).
   - Open **System Settings > Privacy & Security**, scroll to the message about PEBP Studio, and click **Open Anyway**. Confirm with your password.
   - From then on it opens normally.

   If you prefer Terminal: `xattr -dr com.apple.quarantine "/Applications/PEBP Studio.app"`.
3. PEBP Studio opens in its own window. The first start takes a little longer while it makes your starter folder.

Your starter folder is **Desktop/PEBP Studio**. If your Desktop syncs to iCloud, it is **PEBP Studio** in your home folder instead, so scans never leave the Mac. The app keeps a log in `~/Library/Logs/PEBP Studio.log`.

## Windows

1. Right-click the zip and choose **Extract All**. Keep the extracted **PEBP Studio** folder together: the program needs the files beside it.
2. Open the folder and double-click **PEBP Studio.exe**.
3. Windows SmartScreen may say it protected your PC, because the program is not code-signed yet. Click **More info**, then **Run anyway**.
4. A small console window opens, then PEBP Studio opens in your web browser. Keep the console window open while you work, and close it to stop PEBP Studio.

Your starter folder is **PEBP Studio** in your user folder (`C:\Users\<you>\PEBP Studio`). It is deliberately not on the Desktop, which Windows often syncs to OneDrive.

## The starter folder

| Item | What it is |
| --- | --- |
| `1 START HERE - walkthrough.md` | the step-by-step walkthrough |
| `2 Case intake sheet.md` | what to have ready for each case |
| `Sample scans/` | two synthetic palates, not from any patient, to learn the app |
| `Scans to design/` | where to put a real scan before you add it |
| `Cases/` | one folder per case, with every file the app makes |

## Patient data

Real intraoral scans and everything made from them are patient data. Keep the starter folder off iCloud Drive, OneDrive, Dropbox and email, and do not upload real scans, outputs or screenshots of them anywhere, including issues on this repository. Name cases with a code, never the patient's name.

## Running from source instead

Developers can run the same app from a checkout with Python 3.12; see the README. `python -m pebp studio` opens it in the browser.

## Updating and removing

Download the newer zip and replace the app (or the folder on Windows). Your starter folder and cases are kept. To remove PEBP Studio, delete the app or folder; your cases stay in the starter folder until you delete it.

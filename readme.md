# Windows Fixer Pro 🛠️

**Windows Fixer Pro (v3.0)** is an All-in-One Windows Repair and Optimization Manager packed into a **single, pure Python file**. It provides a sleek, dark-themed GUI for executing dozens of common Windows fixes, network resets, and cleanup tasks. 

Built with standard libraries only (Tkinter, ctypes, subprocess), it requires **zero installation** or third-party dependencies.

## 🛡️ The "No Harm" Safety Model
Unlike many "cleaner" or "tweaker" apps that can accidentally brick a system, Windows Fixer Pro is built around a strict safety philosophy:

1. **No Personal Files Touched:** It *never* deletes personal files, formats drives, or modifies the registry (the registry is only read for proxy info).
2. **Hard-Coded Deletion Whitelist:** File deletion is strictly limited to a hard-coded whitelist of easily recreatable cache and Temp folders. Active files from the last hour are skipped to prevent breaking running installers. Symlinks and junctions are never followed.
3. **Informed Consent:** Every action displays a plain-English explanation of *what will happen* and *why it is safe* before asking for permission.
4. **Safety Nets:** Medium-risk actions offer an automatic System Restore point creation before proceeding.
5. **Native Tools Only:** Under the hood, it relies entirely on authentic Microsoft utilities (`SFC`, `DISM`, `CHKDSK`, `netsh`, `winget`, `PowerShell`).
6. **Crash-Proof Execution:** System calls are safely wrapped. Failing commands won't crash the app, and long-running tasks can be cleanly aborted using the global **CANCEL** button.

## 🚀 Features

### 🧹 Cleanup & Speed
* **Deep Temp & Cache Cleaner:** Safely empties Windows/User Temp, old crash dumps, and flushes DNS.
* **Component Store Cleanup:** Reclaims space using native DISM `/StartComponentCleanup`.
* **Cache Rebuilders:** Rebuilds Font cache, Explorer Icon/Thumbnail cache, and the Windows Search Index.

### ⚙️ System Repair
* **DISM & SFC Auto-Repair:** Combined or standalone execution of the official Windows image repair tools.
* **Disk Health & Scan:** Reads WMI S.M.A.R.T. data and runs an online, read-only `chkdsk /scan`.
* **WMI & Shell Apps Fix:** Verifies the WMI repository and re-registers core Windows Shell apps (Start Menu, Settings, Search).

### 🌐 Network & Internet
* **Quick & Full Resets:** Flush DNS, reset Winsock, TCP/IP stack, and clear ARP cache. Includes automated ping/DNS tests.
* **Proxy & Time Sync:** Reset leftover WinHTTP proxies and force a Windows Time service re-sync.

### 📦 Updates & Apps
* **Winget Pro Manager:** A built-in GUI wrapper for `winget`. Scans for outdated apps and lets you batch-update them silently with a single click.
* **Update Fixer:** Clears the Windows Update `SoftwareDistribution\Download` cache and restarts stuck services.
* **Store Repair:** Resets the Microsoft Store and re-registers UWP packages.

### 🖨️ Devices & Services
* **Spooler & Audio:** Fixes stuck print queues by clearing spool files and restarts audio endpoints.
* **Bluetooth & Power:** Restarts Bluetooth services and allows restoration of default Windows power plans (with automatic backups).
* **Defender:** Forces a signature update and runs a quick scan.

### 📊 Live Telemetry Dashboard
* Real-time monitoring of CPU Usage, RAM Usage, Drive C: Free Space, System Uptime, and Disk S.M.A.R.T. health directly in the UI.

## 📋 Requirements
* **OS:** Windows 10 or Windows 11 (64-bit recommended)
* **Python:** Python 3.8 or newer (must include the standard `tkinter` library, which is selected by default during Python installation).

## 📥 How to Use

1. Clone or download this repository.
2. Ensure you have Python installed.
3. Run the script:
   ```cmd
   python WindowsFixerPro_GUI_V2.py
   ```
   *Alternatively, you can just double-click the file in Windows Explorer.*
4. **Administrator Rights:** The script will automatically detect if it lacks privileges and will prompt you via Windows UAC to elevate itself.

**💡 Tip - Run without a console window:**
Rename `WindowsFixerPro_GUI_V2.py` to `WindowsFixerPro_GUI_V2.pyw`. When you double-click a `.pyw` file, Windows runs it purely as a GUI app without opening a black command prompt in the background.

## 📸 Screenshots
*(You can add screenshots of your GUI here by uploading them to your repo and linking them like this: `![Main GUI](docs/main_gui.png)`)*

## ⚠️ Disclaimer
While this tool is designed to be as non-destructive and safe as possible, you use it at your own risk. Always ensure you have backups of your important data before performing system repairs.
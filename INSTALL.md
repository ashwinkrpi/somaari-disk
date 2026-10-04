# Installing somaari-disk (test builds)

These steps run the app from source on a test machine. For release builds (a `.deb` for
Debian/Ubuntu/Raspberry Pi OS, a Windows installer or zip) see Install in
[README.md](README.md). From source you need Python 3.11 or newer and about 200 MB of disk space. The app reads your drives but never
changes them; the only write is "Move to Trash", and it always asks first.

Replace `<repo-url>` below with the clone URL of this repository. If the repository is
private, sign in to Git first, or download it as a ZIP and unpack it to a folder named
`somaari-disk`.

- [Linux](#linux)
- [Windows](#windows)
- [Checking the install](#checking-the-install)
- [Removing it](#removing-it)
- [Troubleshooting](#troubleshooting)

## Linux

The window uses GTK and WebKitGTK from your distribution. They must match the system Python,
so use the distribution's own `python3`, not one from pyenv, conda or a PPA.

### Supported distributions

| Distribution | Python | Status |
|---|---|---|
| Raspberry Pi OS / Twister OS (Bookworm, 64-bit) | 3.11 | Main test platform |
| Debian 12 or newer | 3.11+ | Expected to work |
| Ubuntu 24.04 or newer (also Mint 22, Pop!_OS 24.04) | 3.12+ | Expected to work |
| Fedora 39 or newer | 3.12+ | Expected to work |
| Arch, Manjaro | current | Expected to work |
| Ubuntu 22.04, Debian 11 and older | 3.10 or older | Not supported (Python too old) |

Check your Python version with `python3 --version`.

### 1. Install system packages

Debian, Ubuntu, Raspberry Pi OS, Mint:

```bash
sudo apt update
sudo apt install git python3-venv python3-gi python3-gi-cairo gir1.2-gtk-3.0 gir1.2-webkit2-4.1
```

Fedora:

```bash
sudo dnf install git python3 python3-gobject gtk3 webkit2gtk4.1
```

Arch, Manjaro:

```bash
sudo pacman -S git python python-gobject gtk3 webkit2gtk-4.1
```

### 2. Get the code and set it up

```bash
git clone <repo-url> somaari-disk
cd somaari-disk
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e ".[dev]"
```

`--system-site-packages` is required: it lets the app use the PyGObject that you installed
in step 1. Without it, the window cannot open.

### 3. Run it

```bash
.venv/bin/python -m somaari_disk
```

From an SSH session into a machine with a desktop, add `DISPLAY=:0` in front of that
command.

### Updating

```bash
cd somaari-disk
git pull
.venv/bin/pip install -e ".[dev]"
```

## Windows

Windows 10 (version 1803 or newer) or Windows 11, on 64-bit Intel or AMD. Windows on ARM is
not tested yet.

### 1. Install Python and Git

1. Install Python 3.12 from python.org. In the installer, tick **Add python.exe to PATH**.
   Or, in PowerShell: `winget install Python.Python.3.12`
2. Install Git from git-scm.com, or with `winget install Git.Git`. Skip this if you use the
   ZIP download.
3. Close and reopen PowerShell, then check: `py -3.12 --version`

Python 3.12 is recommended because the window library's Windows parts (pythonnet) publish
wheels for it. Use 3.11 if 3.12 isn't available.

### 2. Check the WebView2 Runtime

The window uses Microsoft Edge WebView2. It comes with Windows 11 and with updated
Windows 10. If the app later says that WebView2 is missing, install the **Evergreen
Bootstrapper** from Microsoft's WebView2 download page, then start the app again.

### 3. Get the code and set it up

In PowerShell (no admin rights needed):

```powershell
git clone <repo-url> somaari-disk
cd somaari-disk
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -e ".[dev]"
```

You don't need to "activate" the venv, so PowerShell's script execution policy doesn't
matter.

### 4. Run it

```powershell
.venv\Scripts\python -m somaari_disk
```

Run it as a normal user, not "Run as administrator", because that's how real users run it.

### Updating

```powershell
cd somaari-disk
git pull
.venv\Scripts\pip install -e ".[dev]"
```

## Checking the install

The commands are the same on both systems. On Windows, use `.venv\Scripts\python` in place
of `.venv/bin/python`.

| Command | What you should see |
|---|---|
| `.venv/bin/python -m somaari_disk --demo` | The window with a generated sample drive. Use this for screenshots. |
| `.venv/bin/python -m somaari_disk volumes` | One line per drive, with its size, file system and flags (removable, read-only, network). |
| `.venv/bin/python -m somaari_disk scan <folder>` | The largest items in that folder, in the terminal. |
| `.venv/bin/python -m somaari_disk --debug` | The window, with web developer tools enabled. |
| `.venv/bin/pytest -q` | Automated tests. All pass; a few are skipped on each OS. |

Manual test checklists are in [docs/TESTING.md](docs/TESTING.md). There is a daily checklist
for Linux and a separate one for Windows.

### When you report a problem

Include:

- the OS and version, the CPU type (x86-64 or ARM), and the Python version
- the drive type (internal, USB, SD card or network) and its file system
- what you did, what you expected, and what happened
- the app log (see below)

Never include real file names, folder names or paths, in text or screenshots. Describe them
instead ("a folder with an emoji in its name"), or reproduce the problem in `--demo` mode.

## Where the app keeps its files

| | Linux | Windows |
|---|---|---|
| Log | `~/.config/somaari-disk/logs/app.log` | `%LOCALAPPDATA%\somaari-disk\somaari-disk\logs\app.log` |
| Saved scans | `~/.cache/somaari-disk/scans/` | `%LOCALAPPDATA%\somaari-disk\somaari-disk\Cache\scans\` |

The log replaces your home folder with `~` and keeps at most 4 files of 512 KiB. You can
delete saved scans at any time; the app scans again.

## Removing it

1. Delete the `somaari-disk` folder that you cloned.
2. Delete the folders listed in the table above (on Windows, the whole `%LOCALAPPDATA%\somaari-disk` folder).

On Linux, the system packages from step 1 are shared with other apps. Leave them installed
unless you know nothing else uses them.

## Troubleshooting

| Problem | Fix |
|---|---|
| Linux: `No module named 'gi'` | The venv was made without `--system-site-packages`, or with a different Python than the system one. Delete `.venv` and redo step 2 with the system `python3`. |
| Linux: `Namespace WebKit2 not available` | Install `gir1.2-webkit2-4.1` (Fedora: `webkit2gtk4.1`, Arch: `webkit2gtk-4.1`). |
| Linux: the window opens but stays blank or flickers | Graphics driver issue in WebKitGTK. Try starting the app with `WEBKIT_DISABLE_DMABUF_RENDERER=1` in front of the run command. On Wayland, also try `GDK_BACKEND=x11`. |
| Linux, over SSH: `cannot open display` | Add `DISPLAY=:0` in front of the run command, and stay logged in to the desktop. |
| Windows: `py` is not recognized | Reinstall Python with "Add python.exe to PATH" ticked, or use the full path to `python.exe`. |
| Windows: an error about pythonnet or `clr` during install or start | Use Python 3.12. Make sure .NET Framework 4.7.2 or newer is installed (it is on current Windows 10 and 11). |
| Windows: a dialog says WebView2 is missing | Install the Evergreen WebView2 Runtime (see Windows step 2). |
| Drives are missing from the list | Empty card readers and DVD drives aren't listed. Network shares that don't answer within 2 seconds show with zero size. |
| "No permission" on some folders | Expected for system folders and other users' files. The scan skips them and keeps going. |

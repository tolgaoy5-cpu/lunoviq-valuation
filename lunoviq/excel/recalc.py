"""
Recalculate a workbook with the installed Microsoft Excel (via xlwings), then
save it so every formula has a cached value that Python can read and validate.

Excel runs hidden in its own instance, driven from a child process, on a copy
inside Excel's sandbox folder (no file-access prompt). Only pass copies or
pipeline output, never the master template.

If Excel does not finish within the time limit (typically because a dialog is
waiting in Excel, e.g. document recovery or a warning on a file the user just
opened), only the hidden instance started here is stopped and RecalcTimeout is
raised, instead of waiting forever. The user's own Excel windows are never touched.

Usage:
    python -m lunoviq.excel.recalc <in.xlsx> [--out <out.xlsx>]   (default: in place)
"""
import argparse
import os
import shutil
import signal
import threading
from pathlib import Path

TIMEOUT = 180            # seconds; a normal model takes 10-40 s

# Excel for Mac is sandboxed: opening a file elsewhere can raise a "Grant File Access" prompt,
# which a hidden instance never shows, so Excel waits forever. Files inside its own container
# are always allowed, so the workbook is recalculated there and copied back.
SANDBOX = Path.home() / "Library" / "Containers" / "com.microsoft.Excel" / "Data"


class RecalcTimeout(RuntimeError):
    pass


def _stop(pid, grace=5.0):
    """Stop only the hidden Excel instance started by recalc(): polite first, then forced
    (a dialog-blocked Excel ignores SIGTERM)."""
    import time
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.kill(pid, sig)
        except OSError:
            return
        end = time.time() + grace
        while time.time() < end:
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.2)


def _stop_if_alive(pid, wait=5.0):
    import time
    end = time.time() + wait
    while time.time() < end:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.2)
    _stop(pid)


def _work(path):
    """Runs in a child process (see recalc): prints the Excel pid, then recalculates and saves."""
    import xlwings as xw
    app = xw.App(visible=False, add_book=False)
    print("PID", app.pid, flush=True)
    try:
        app.display_alerts = False
        app.screen_updating = False
        wb = app.books.open(str(path), update_links=False)
        app.calculation = "automatic"
        app.calculate()                      # template has fullCalcOnLoad=1 as well
        wb.save()
        wb.close()
    finally:
        try:
            app.quit()
        except Exception:                    # noqa: BLE001 - the parent checks the pid
            pass


def recalc(path_in, path_out=None, timeout=TIMEOUT):
    """Excel is driven from a child process: if Excel hangs, killing the child as well means
    no pending Apple event is left behind to relaunch Excel."""
    src = Path(path_in).resolve()
    dst = Path(path_out).resolve() if path_out else src
    if dst != src:
        shutil.copy2(src, dst)
    stage = None
    if SANDBOX.is_dir():
        (SANDBOX / "Lunoviq").mkdir(exist_ok=True)
        stage = SANDBOX / "Lunoviq" / ("%d_%s" % (os.getpid(), dst.name))
        shutil.copy2(dst, stage)
    try:
        _run_child(stage or dst, timeout)
        if stage:
            shutil.copy2(stage, dst)
    finally:
        if stage and stage.exists():
            stage.unlink()
    return dst


def _run_child(path, timeout):
    import subprocess
    import sys

    root = str(Path(__file__).resolve().parents[2])
    child = subprocess.Popen([sys.executable, "-W", "ignore", "-m", "lunoviq.excel.recalc", "--worker", str(path)],
                             cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pid = {}

    def read_pid():
        for line in child.stdout:
            if line.startswith("PID "):
                pid["excel"] = int(line.split()[1])

    reader = threading.Thread(target=read_pid, daemon=True)
    reader.start()
    try:
        child.wait(timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()
        reader.join(2)
        if "excel" in pid:
            _stop(pid["excel"])
        raise RecalcTimeout("Excel did not finish within %d s. A dialog may be waiting in Excel "
                            "(for example document recovery); close it and try again." % timeout)
    reader.join(2)
    if "excel" in pid:
        _stop_if_alive(pid["excel"])         # never leave a hidden Excel behind: it can block later runs
    if child.returncode != 0:
        err = child.stderr.read().strip().splitlines()
        raise RuntimeError("Excel recalculation failed: %s" % (err[-1] if err else "exit %d" % child.returncode))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("book")
    ap.add_argument("--out")
    ap.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.worker:
        return _work(a.book)
    print("Recalculated:", recalc(a.book, a.out))


if __name__ == "__main__":
    main()

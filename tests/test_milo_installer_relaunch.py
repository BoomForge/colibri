"""Protect install-complete relaunch and safe updater handoff.

Actual Windows process waiting and installer compilation are exercised by
.github/workflows/milo-09-preview-installer.yml.
"""
from pathlib import Path
import unittest


REPO = Path(__file__).resolve().parents[1]


class InstallerRelaunchTests(unittest.TestCase):
    def test_relaunch_only_after_install_operations(self):
        installer = (REPO / "desktop" / "install" / "Milo.nsi").read_text(
            encoding="utf-8")
        install = installer.split('Section "Install Milo" SEC_MAIN', 1)[1]
        install = install.split("SectionEnd", 1)[0]
        self.assertIn('!define VERSION "0.9.6"', installer)
        self.assertIn('File /r "..\\..\\dist\\Milo\\*"', install)
        self.assertIn('IfSilent skip_launch', install)
        self.assertIn("Exec '\"$INSTDIR\\Milo.exe\"'", install)
        self.assertLess(install.index("File /r "), install.index("Exec "))
        self.assertLess(install.index('"DisplayVersion"'), install.index("Exec "))
        self.assertIn("skip_launch:", install)
        uninstall = installer.split('Section "Uninstall"', 1)[1]
        self.assertNotIn('Exec ', uninstall)

    def test_old_pid_wait_is_explicit_and_installer_is_user_approved(self):
        installer = (REPO / "desktop" / "install" / "Milo.nsi").read_text(
            encoding="utf-8")
        desktop = (REPO / "desktop" / "milo_desktop.py").read_text(
            encoding="utf-8")
        self.assertIn('/WAITFORPID=', installer)
        self.assertIn("WaitForSingleObject", installer)
        self.assertIn('i 120000', installer)
        self.assertIn("Abort", installer)
        self.assertIn('"/WAITFORPID=" + str(os.getpid())', desktop)
        self.assertIn('"Verified update ready"', desktop)
        self.assertIn("messagebox.askyesno(", desktop)

    def test_silent_ci_installs_do_not_start_app(self):
        workflow = (
            REPO / ".github" / "workflows" / "milo-09-preview-installer.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('"/S", "/WAITFORPID=$($held.Id)"', workflow)
        self.assertIn('Start-Sleep -Seconds 5', workflow)
        self.assertIn('Installer did not wait for previous process exit', workflow)
        self.assertIn('Silent setup unexpectedly started Milo.exe', workflow)
        self.assertIn('Milo-Setup-0.9.6.exe', workflow)


if __name__ == "__main__":
    unittest.main()

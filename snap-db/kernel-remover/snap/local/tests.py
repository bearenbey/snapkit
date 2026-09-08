#!/usr/bin/env python3
"""Tests for the planning logic of kernel_remover.py. Standard library only:

    python3 snap/local/tests.py

Nothing here touches apt, dpkg or /boot; the planner reads everything through
a Host, and these build one by hand. pack.py runs them before packing."""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kernel_remover as kr  # noqa: E402


def pkg(name: str, status: str = "ii", size: int = 1024) -> kr.Pkg:
    m = kr.PKG_RE.match(name)
    return kr.Pkg(name, status, size, m.group("ver") if m else None, m.group("flavor") if m else None)


def host(**overrides) -> kr.Host:
    base = dict(
        running="6.8.0-45",
        packages=[
            pkg("linux-image-6.8.0-47-generic"),
            pkg("linux-headers-6.8.0-47-generic"),
            pkg("linux-image-6.8.0-45-generic"),
            pkg("linux-image-6.8.0-40-generic", size=100 * 1024),
            pkg("linux-hwe-6.8-headers-6.8.0-40", size=50 * 1024),
            pkg("linux-image-6.8.0-38-generic"),
            pkg("linux-modules-6.8.0-38-generic", status="hi"),
            pkg("linux-tools-6.8.0-38"),
            pkg("linux-generic-hwe-24.04"),
            pkg("linux-firmware"),
            pkg("linux-image-6.5.0-10-generic", status="rc", size=0),
            pkg("some-app", status="rc", size=0),
        ],
        held={"linux-tools-6.8.0-38"},
        boot_files=[
            "grub",
            "vmlinuz-6.8.0-47-generic",
            "initrd.img-6.8.0-47-generic",
            "initrd.img-6.8.0-40-generic",
            "vmlinuz-6.8.0-45-generic.old",
            "vmlinuz-6.5.0-10-generic",
            "initrd.img-6.5.0-10-generic",
            "config-6.5.0-10-generic",
        ],
        owned=lambda path: "6.8.0-47" in path or path.endswith("config-6.5.0-10-generic"),
        is_root=False,
    )
    base.update(overrides)
    return kr.Host(**base)


class PatternTests(unittest.TestCase):
    def test_versioned_kernel_packages(self):
        cases = {
            "linux-image-6.8.0-45-generic": ("image", "6.8.0-45", "generic"),
            "linux-headers-6.8.0-45": ("headers", "6.8.0-45", None),
            "linux-hwe-6.8-headers-6.8.0-45": ("hwe-6.8-headers", "6.8.0-45", None),
            "linux-modules-extra-6.8.0-45-generic": ("modules-extra", "6.8.0-45", "generic"),
            "linux-modules-nvidia-535-6.8.0-45-generic": ("modules-nvidia-535", "6.8.0-45", "generic"),
            "linux-image-6.11.0-1007-oem": ("image", "6.11.0-1007", "oem"),
        }
        for name, (kind, ver, flavor) in cases.items():
            m = kr.PKG_RE.match(name)
            self.assertIsNotNone(m, name)
            self.assertEqual((m.group("kind"), m.group("ver"), m.group("flavor")), (kind, ver, flavor), name)

    def test_metapackages_and_others_never_match(self):
        for name in ("linux-generic-hwe-24.04", "linux-image-generic", "linux-headers-generic-hwe-26.04",
                     "linux-modules-nvidia-610-generic-hwe-26.04", "linux-firmware", "linux-libc-dev",
                     "linux-base", "linux-tools-common"):
            self.assertIsNone(kr.PKG_RE.match(name), name)

    def test_boot_files(self):
        self.assertEqual(kr.BOOT_FILE_RE.match("vmlinuz-6.8.0-45-generic").group("ver"), "6.8.0-45")
        self.assertEqual(kr.BOOT_FILE_RE.match("initrd.img-6.8.0-45-generic").group("ver"), "6.8.0-45")
        self.assertEqual(kr.BOOT_FILE_RE.match("System.map-6.8.0-45-generic").group("ver"), "6.8.0-45")
        for name in ("grub", "vmlinuz", "vmlinuz-6.8.0-45-generic.old", "memtest86+x64.bin", "efi"):
            self.assertIsNone(kr.BOOT_FILE_RE.match(name), name)

    def test_release(self):
        self.assertEqual(kr.RELEASE_RE.match("6.8.0-45-generic").group("ver"), "6.8.0-45")
        self.assertEqual(kr.RELEASE_RE.match("7.0.0-31-generic").group("ver"), "7.0.0-31")
        self.assertIsNone(kr.RELEASE_RE.match("6.12.3"))


class HelperTests(unittest.TestCase):
    def test_version_key_orders_numerically(self):
        versions = ["6.8.0-45", "6.8.0-9", "6.10.0-1", "6.8.0-100"]
        self.assertEqual(sorted(versions, key=kr.version_key), ["6.8.0-9", "6.8.0-45", "6.8.0-100", "6.10.0-1"])

    def test_human(self):
        self.assertEqual(kr.human(0), "0B")
        self.assertEqual(kr.human(1023), "1023B")
        self.assertEqual(kr.human(1024), "1K")
        self.assertEqual(kr.human(1536), "1.5K")
        self.assertEqual(kr.human(10 * 1024 ** 2), "10M")
        self.assertEqual(kr.human(-2 * 1024 ** 3), "-2G")

    def test_plural(self):
        self.assertEqual(kr.plural(1, "package"), "1 package")
        self.assertEqual(kr.plural(2, "package"), "2 packages")

    def test_collateral(self):
        transcript = "\n".join([
            "NOTE: This is only a simulation!",
            "Purg linux-image-6.8.0-40-generic [6.8.0-40.40]",
            "Remv linux-generic-hwe-24.04 [6.8.0-40.40]",
            "Purg linux-modules-6.8.0-40-generic [6.8.0-40.40]",
            "Conf something",
        ])
        asked = ["linux-image-6.8.0-40-generic", "linux-modules-6.8.0-40-generic"]
        self.assertEqual(kr.collateral(asked, transcript), ["linux-generic-hwe-24.04"])
        self.assertEqual(kr.collateral(asked, "Purg linux-image-6.8.0-40-generic"), [])


class PlanTests(unittest.TestCase):
    def test_keep_and_running_are_protected(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.versions, ["6.8.0-47", "6.8.0-45", "6.8.0-40", "6.8.0-38"])
        self.assertEqual(plan.protected, ["6.8.0-47", "6.8.0-45"])
        self.assertEqual(list(plan.old), ["6.8.0-38", "6.8.0-40"])

    def test_running_kernel_protected_beyond_keep(self):
        plan = kr.build_plan(host(running="6.8.0-38"), keep=1)
        self.assertEqual(plan.protected, ["6.8.0-47", "6.8.0-38"])
        self.assertEqual(list(plan.old), ["6.8.0-40", "6.8.0-45"])

    def test_running_kernel_without_package_is_still_protected(self):
        plan = kr.build_plan(host(running="6.9.0-1"), keep=1)
        self.assertIn("6.9.0-1", plan.protected)
        self.assertNotIn("6.9.0-1", plan.versions)

    def test_keep_covers_everything(self):
        plan = kr.build_plan(host(), keep=10)
        self.assertEqual(plan.old, {})
        self.assertEqual(plan.held, [])

    def test_removable_packages_are_grouped_and_sorted(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual([p.name for p in plan.old["6.8.0-40"]],
                         ["linux-hwe-6.8-headers-6.8.0-40", "linux-image-6.8.0-40-generic"])
        self.assertEqual([p.name for p in plan.old["6.8.0-38"]], ["linux-image-6.8.0-38-generic"])

    def test_metapackages_and_unversioned_never_appear(self):
        plan = kr.build_plan(host(), keep=1)
        names = {p.name for pkgs in plan.old.values() for p in pkgs} | {p.name for p in plan.held}
        self.assertNotIn("linux-generic-hwe-24.04", names)
        self.assertNotIn("linux-firmware", names)

    def test_holds_from_apt_mark_and_dpkg(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual([p.name for p in plan.held],
                         ["linux-modules-6.8.0-38-generic", "linux-tools-6.8.0-38"])

    def test_rc_split(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual([p.name for p in plan.kernel_rc], ["linux-image-6.5.0-10-generic"])
        self.assertEqual([p.name for p in plan.other_rc], ["some-app"])

    def test_orphans_skip_installed_versions_and_owned_files(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual([path for path, _ in plan.orphans],
                         ["/boot/initrd.img-6.5.0-10-generic", "/boot/vmlinuz-6.5.0-10-generic"])

    def test_sizes(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.size_of(["pkg:linux-image-6.8.0-40-generic"]), 100 * 1024)
        self.assertEqual(plan.size_of(["pkg:linux-hwe-6.8-headers-6.8.0-40", "pkg:missing"]), 50 * 1024)


class SelectionTests(unittest.TestCase):
    def test_defaults(self):
        plan = kr.build_plan(host(), keep=1)
        keys = kr.default_selection(plan, kr.Options())
        self.assertEqual(keys, {
            "pkg:linux-hwe-6.8-headers-6.8.0-40", "pkg:linux-image-6.8.0-40-generic",
            "pkg:linux-image-6.8.0-38-generic", "rc:linux-image-6.5.0-10-generic",
        })

    def test_options_widen_the_default(self):
        plan = kr.build_plan(host(), keep=1)
        keys = kr.default_selection(plan, kr.Options(all_rc=True, purge_orphans=True))
        self.assertIn("rc:some-app", keys)
        self.assertIn("file:/boot/vmlinuz-6.5.0-10-generic", keys)

    def test_from_keys(self):
        sel = kr.Selection.from_keys({"file:/boot/x", "pkg:b", "pkg:a", "rc:c"})
        self.assertEqual((sel.pkgs, sel.rc, sel.files), (["a", "b"], ["c"], ["/boot/x"]))
        self.assertFalse(sel.empty())
        self.assertTrue(kr.Selection().empty())


class PreflightTests(unittest.TestCase):
    def test_refuses_without_root(self):
        plan = kr.build_plan(host(), keep=1)
        refusal = kr.preflight(kr.Selection(rc=["some-app"]), plan, dry_run=False)
        self.assertEqual(refusal.title, "Permission")

    def test_dry_run_without_packages_needs_nothing(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertIsNone(kr.preflight(kr.Selection(rc=["some-app"]), plan, dry_run=True))


class ArgTests(unittest.TestCase):
    def test_defaults(self):
        opts = kr.parse_args([])
        self.assertEqual(opts, kr.Options())

    def test_flags(self):
        opts = kr.parse_args(["-k", "3", "-n", "-p", "-y", "--all-rc", "--purge-orphans"])
        self.assertEqual(opts, kr.Options(keep=3, dry_run=True, plain=True, yes=True,
                                          all_rc=True, purge_orphans=True))

    def test_keep_must_be_positive(self):
        # argparse reports the error on stderr; keep it out of the test run.
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            kr.parse_args(["--keep", "0"])


if __name__ == "__main__":
    unittest.main(verbosity=1)

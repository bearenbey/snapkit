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

K = 1024


def pkg(name: str, status: str = "ii", size: int = K) -> kr.Pkg:
    m = kr.PKG_RE.match(name)
    return kr.Pkg(name, status, size, m.group("ver") if m else None)


def host(**overrides) -> kr.Host:
    base = dict(
        running="6.8.0-45",
        packages=[
            pkg("linux-image-6.8.0-47-generic"),
            pkg("linux-headers-6.8.0-47-generic"),
            pkg("linux-image-6.8.0-45-generic"),
            pkg("linux-image-6.8.0-40-generic", size=100 * K),
            pkg("linux-hwe-6.8-headers-6.8.0-40", size=50 * K),
            pkg("linux-image-6.8.0-38-generic"),
            pkg("linux-modules-6.8.0-38-generic", status="hi"),
            pkg("linux-tools-6.8.0-38"),
            pkg("linux-generic-hwe-24.04"),
            pkg("linux-firmware"),
            pkg("linux-image-6.5.0-10-generic", status="rc", size=0),
            pkg("some-app", status="rc", size=0),
        ],
        held={"linux-tools-6.8.0-38"},
        boot_files={
            "grub": 0,
            "vmlinuz-6.8.0-47-generic": 15 * K,
            "initrd.img-6.8.0-47-generic": 80 * K,
            "initrd.img-6.8.0-40-generic": 80 * K,
            "vmlinuz-6.8.0-45-generic.old": 15 * K,
            "vmlinuz-6.5.0-10-generic": 14 * K,
            "initrd.img-6.5.0-10-generic": 70 * K,
            "config-6.5.0-10-generic": 1 * K,
        },
        owned=lambda path: "6.8.0-47" in path or path.endswith("config-6.5.0-10-generic"),
        is_root=False,
    )
    base.update(overrides)
    return kr.Host(**base)


def names(items) -> list[str]:
    return [i.name for i in items]


class PatternTests(unittest.TestCase):
    def test_versioned_kernel_packages(self):
        cases = {
            "linux-image-6.8.0-45-generic": "6.8.0-45",
            "linux-headers-6.8.0-45": "6.8.0-45",
            "linux-hwe-6.8-headers-6.8.0-45": "6.8.0-45",
            "linux-modules-extra-6.8.0-45-generic": "6.8.0-45",
            "linux-modules-nvidia-535-6.8.0-45-generic": "6.8.0-45",
            "linux-image-6.11.0-1007-oem": "6.11.0-1007",
        }
        for name, ver in cases.items():
            m = kr.PKG_RE.match(name)
            self.assertIsNotNone(m, name)
            self.assertEqual(m.group("ver"), ver, name)

    def test_metapackages_and_others_never_match(self):
        for name in ("linux-generic-hwe-24.04", "linux-image-generic", "linux-headers-generic-hwe-26.04",
                     "linux-modules-nvidia-610-generic-hwe-26.04", "linux-firmware", "linux-libc-dev",
                     "linux-base", "linux-tools-common"):
            self.assertIsNone(kr.PKG_RE.match(name), name)

    def test_boot_files(self):
        for name in ("vmlinuz-6.8.0-45-generic", "initrd.img-6.8.0-45-generic", "System.map-6.8.0-45-generic",
                     "config-6.8.0-45-generic", "retpoline-6.8.0-45-generic"):
            self.assertEqual(kr.BOOT_FILE_RE.match(name).group("ver"), "6.8.0-45", name)
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
        self.assertEqual(kr.human(K), "1K")
        self.assertEqual(kr.human(1.5 * K), "1.5K")
        self.assertEqual(kr.human(10 * K ** 2), "10M")
        self.assertEqual(kr.human(-2 * K ** 3), "-2G")

    def test_plural(self):
        self.assertEqual(kr.plural(1, "package"), "1 package")
        self.assertEqual(kr.plural(2, "package"), "2 packages")

    def test_total(self):
        self.assertEqual(kr.total([kr.Item("pkg", "a", 3), kr.Item("file", "b", 4)]), 7)
        self.assertEqual(kr.total([]), 0)

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

    def test_removable_packages_are_grouped_sorted_and_sized(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.old["6.8.0-40"], [
            kr.Item("pkg", "linux-hwe-6.8-headers-6.8.0-40", 50 * K),
            kr.Item("pkg", "linux-image-6.8.0-40-generic", 100 * K),
        ])
        self.assertEqual(names(plan.old["6.8.0-38"]), ["linux-image-6.8.0-38-generic"])

    def test_metapackages_and_unversioned_never_appear(self):
        plan = kr.build_plan(host(), keep=1)
        seen = {i.name for group in plan.old.values() for i in group} | set(plan.held)
        self.assertNotIn("linux-generic-hwe-24.04", seen)
        self.assertNotIn("linux-firmware", seen)

    def test_holds_from_apt_mark_and_dpkg(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.held, ["linux-modules-6.8.0-38-generic", "linux-tools-6.8.0-38"])

    def test_rc_split(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.kernel_rc, [kr.Item("rc", "linux-image-6.5.0-10-generic")])
        self.assertEqual(plan.other_rc, [kr.Item("rc", "some-app")])

    def test_orphans_skip_installed_versions_and_owned_files(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(plan.orphans, [
            kr.Item("file", "/boot/initrd.img-6.5.0-10-generic", 70 * K),
            kr.Item("file", "/boot/vmlinuz-6.5.0-10-generic", 14 * K),
        ])


class SelectionTests(unittest.TestCase):
    def test_defaults(self):
        plan = kr.build_plan(host(), keep=1)
        self.assertEqual(sorted(names(kr.default_selection(plan, kr.Options()))), [
            "linux-hwe-6.8-headers-6.8.0-40", "linux-image-6.5.0-10-generic",
            "linux-image-6.8.0-38-generic", "linux-image-6.8.0-40-generic",
        ])

    def test_options_widen_the_default(self):
        plan = kr.build_plan(host(), keep=1)
        items = kr.default_selection(plan, kr.Options(all_rc=True, purge_orphans=True))
        self.assertIn(kr.Item("rc", "some-app"), items)
        self.assertIn(kr.Item("file", "/boot/vmlinuz-6.5.0-10-generic", 14 * K), items)

    def test_of_groups_and_sorts(self):
        sel = kr.Selection.of({kr.Item("file", "/boot/x"), kr.Item("pkg", "b"), kr.Item("pkg", "a"),
                               kr.Item("rc", "c")})
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


class RowTests(unittest.TestCase):
    def test_section_with_and_without_group(self):
        items = [kr.Item("rc", "a"), kr.Item("rc", "b")]
        rows = kr.Tui.section("Title", items, group="all", unit="package")
        self.assertEqual([r.kind for r in rows], ["blank", "section", "group", "item", "item"])
        self.assertEqual(rows[2].text, "all (2 packages)")
        self.assertEqual(rows[2].items, tuple(items))
        self.assertEqual(rows[3].items, (items[0],))
        rows = kr.Tui.section("Title", [])
        self.assertEqual([r.kind for r in rows], ["blank", "section", "info"])

    def test_row_size_sums_its_items(self):
        row = kr.Row("group", "x", (kr.Item("pkg", "a", 2), kr.Item("pkg", "b", 3)))
        self.assertEqual(row.size, 5)
        self.assertEqual(kr.Row("info", "x").size, 0)


class ArgTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual(kr.parse_args([]), kr.Options())

    def test_flags(self):
        opts = kr.parse_args(["-k", "3", "-n", "-p", "-y", "--all-rc", "--purge-orphans"])
        self.assertEqual(opts, kr.Options(keep=3, dry_run=True, plain=True, yes=True,
                                          all_rc=True, purge_orphans=True))

    def test_keep_must_be_positive(self):
        # argparse reports the error on stderr; keep it out of the test run.
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            kr.parse_args(["--keep", "0"])


class PlannerEdges(unittest.TestCase):
    """The cases that would have removed the wrong thing."""

    def test_an_install_is_collateral_too(self):
        # Purging one flavour let apt satisfy the metapackage with another.
        transcript = "\n".join([
            "Purg linux-image-6.8.0-40-generic [6.8.0-40.40]",
            "Inst linux-image-6.8.0-40-lowlatency (6.8.0-40.40 Ubuntu:24.04)",
        ])
        self.assertEqual(kr.collateral(["linux-image-6.8.0-40-generic"], transcript),
                         ["linux-image-6.8.0-40-lowlatency"])

    def test_headers_only_version_does_not_take_a_keep_slot(self):
        h = host(packages=[
            pkg("linux-headers-6.9.0-1-generic"),        # no image: not bootable
            pkg("linux-image-6.8.0-47-generic"),
            pkg("linux-image-6.8.0-45-generic"),
            pkg("linux-image-6.8.0-40-generic"),
        ])
        plan = kr.build_plan(h, keep=1)
        self.assertEqual(plan.protected, ["6.8.0-47", "6.8.0-45"])
        self.assertIn("6.9.0-1", plan.old, "the stray headers are removable")
        self.assertNotIn("6.8.0-47", plan.old, "the newest bootable kernel stays")

    def test_half_installed_kernel_keeps_its_boot_files(self):
        h = host(
            packages=[pkg("linux-image-6.8.0-47-generic"),
                      pkg("linux-image-6.8.0-45-generic"),
                      pkg("linux-image-6.9.0-1-generic", status="iF")],
            boot_files={"vmlinuz-6.9.0-1-generic": 15 * K,
                        "initrd.img-6.9.0-1-generic": 80 * K},
            owned=lambda path: "vmlinuz" in path,
        )
        plan = kr.build_plan(h, keep=1)
        self.assertEqual(plan.orphans, [], "a half-installed kernel is not an orphan")
        self.assertNotIn("6.9.0-1", plan.versions, "and not installed either")

    def test_status_shapes(self):
        self.assertTrue(pkg("a", status="iU").present)
        self.assertTrue(pkg("a", status="iF").present)
        self.assertFalse(pkg("a", status="rc").present)
        self.assertFalse(pkg("a", status="un").present)
        self.assertFalse(pkg("a", status="iU").installed)

    def test_release_and_package_patterns(self):
        for release in ("6.8.0-1017-azure", "6.11.0-061100-generic",
                        "6.8.0-45-generic", "6.8.0-45-lowlatency"):
            self.assertIsNotNone(kr.RELEASE_RE.match(release), release)
        for name in ("linux-azure-6.8-headers-6.8.0-1017",
                     "linux-image-6.8.0-45-generic-64k",
                     "linux-modules-extra-6.8.0-45-generic"):
            self.assertIsNotNone(kr.PKG_RE.match(name), name)
        self.assertIsNone(kr.PKG_RE.match("linux-firmware"))


if __name__ == "__main__":
    unittest.main(verbosity=1)

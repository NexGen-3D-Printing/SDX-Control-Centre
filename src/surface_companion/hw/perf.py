"""Performance / power profile.

Modern kernels expose the Surface Aggregator performance modes through the generic
ACPI platform_profile interface. Older linux-surface kernels expose a perf_mode attribute.

Some systems (like Surface Book 1) have a nested platform-profile interface under
/sys/bus/surface_aggregator/devices/...

On Bazzite and other systems using tuned-ppd/power-profiles-daemon (PPD), the actual
CPU scaling (governor, intel_pstate min/max perf, EPP) is controlled by the system
power profile. On these systems we MUST set the PPD profile via D-Bus in addition to
(or instead of) writing platform_profile directly, otherwise clock speeds won't change.
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import sysfs

PLATFORM = "/sys/firmware/acpi/platform_profile"
CHOICES = "/sys/firmware/acpi/platform_profile_choices"
LEGACY_GLOB = "/sys/bus/surface_aggregator/devices/01:03:01:00:01/perf_mode"
LEGACY = {1: "balanced", 2: "low-power", 3: "balanced-performance", 4: "performance"}

# Nested platform-profile interface for Surface Book 1
NESTED_PLATFORM_GLOB = "/sys/bus/surface_aggregator/devices/*/platform-profile/platform-profile-*/profile"
NESTED_PLATFORM_CHOICES_GLOB = "/sys/bus/surface_aggregator/devices/*/platform-profile/platform-profile-*/choices"

# PPD (tuned-ppd / power-profiles-daemon) D-Bus interface
PPD_BUS = "net.hadess.PowerProfiles"
PPD_PATH = "/net/hadess/PowerProfiles"
PPD_IFACE = "net.hadess.PowerProfiles"

# Map Surface/platform profiles <-> PPD profiles (power-saver, balanced, performance)
TO_PPD = {
    "low-power": "power-saver",
    "quiet": "power-saver",
    "cool": "power-saver",
    "balanced": "balanced",
    "balanced-performance": "performance",
    "performance": "performance",
}
# Reverse map for reading PPD state (prefer the canonical Surface profile name)
FROM_PPD = {
    "power-saver": "low-power",
    "balanced": "balanced",
    "performance": "performance",
}

LABELS = {
    "low-power": ("Battery Saver", "Quietest and coolest. Best battery life."),
    "quiet": ("Quiet", "Keeps the fan down."),
    "cool": ("Cool", "Keeps the chassis cooler."),
    "balanced": ("Recommended", "Good balance of speed, noise and battery."),
    "balanced-performance": ("Better Performance", "More speed, more heat and fan noise."),
    "performance": ("Best Performance", "Full speed. Fan will be audible. Mains power recommended."),
}
ORDER = ["low-power", "quiet", "cool", "balanced", "balanced-performance", "performance"]


def _find_nested_platform() -> tuple[str, str] | None:
    """Find nested platform-profile interface paths.

    Returns (profile_path, choices_path) if found, None otherwise.
    """
    profile_paths = sysfs.glob(NESTED_PLATFORM_GLOB)
    if not profile_paths:
        return None
    for profile_path in profile_paths:
        match = re.search(r'/devices/([^/]+)/platform-profile/platform-profile-[^/]+/profile$', str(profile_path))
        if match:
            device_name = match.group(1)
            choices_path = Path(f"/sys/bus/surface_aggregator/devices/{device_name}/platform-profile/platform-profile-*/choices")
            choices = sysfs.glob(str(choices_path))
            if choices:
                return (str(profile_path), str(choices[0]))
    return None


def _ppd_bus():
    """Return a system Gio bus connection if PPD is available, else None."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        # Check that the PPD name is owned
        res = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", None, None, Gio.DBusCallFlags.NONE, 2000, None,
        )
        if res and res.unpack()[0]:
            return bus
    except Exception:
        pass
    # Try simple call as fallback
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        res = bus.call_sync(
            PPD_BUS, PPD_PATH, "org.freedesktop.DBus.Properties", "Get",
            None, None, Gio.DBusCallFlags.NONE, 2000, None,
        )
        return bus
    except Exception:
        return None


def _ppd_available() -> bool:
    """Check if tuned-ppd/power-profiles-daemon is available on the system bus."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        # Query NameHasOwner for PPD bus name
        variant = GLib.Variant("(s)", (PPD_BUS,))
        res = bus.call_sync(
            "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
            "NameHasOwner", variant, GLib.VariantType("(b)"),
            Gio.DBusCallFlags.NONE, 2000, None,
        )
        return bool(res.unpack()[0]) if res else False
    except Exception:
        return False


def _ppd_get() -> str | None:
    """Get current PPD ActiveProfile (power-saver/balanced/performance) or None."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        variant = GLib.Variant("(ss)", (PPD_IFACE, "ActiveProfile"))
        res = bus.call_sync(
            PPD_BUS, PPD_PATH, "org.freedesktop.DBus.Properties", "Get",
            variant, GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 3000, None,
        )
        if res:
            # (v,) where v contains the string variant
            val = res.unpack()[0]
            if isinstance(val, str):
                return val
    except Exception:
        pass
    return None


def _ppd_set(profile: str) -> bool:
    """Set PPD ActiveProfile. Returns True on success."""
    try:
        import gi
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
        # Set takes (interface, property, value-variant)
        variant = GLib.Variant("(ssv)", (PPD_IFACE, "ActiveProfile", GLib.Variant("s", profile)))
        bus.call_sync(
            PPD_BUS, PPD_PATH, "org.freedesktop.DBus.Properties", "Set",
            variant, None, Gio.DBusCallFlags.NONE, 5000, None,
        )
        return True
    except Exception:
        return False


def _platform_state() -> tuple[str | None, str | None, str]:
    """Return (current, choices_raw, source) from platform_profile interfaces."""
    cur = sysfs.read(PLATFORM)
    raw = sysfs.read(CHOICES)
    source = "platform_profile"
    if cur is not None and raw is not None:
        return cur, raw, source
    nested = _find_nested_platform()
    if nested:
        cur = sysfs.read(nested[0])
        raw = sysfs.read(nested[1])
        return cur, raw, "nested_platform_profile"
    legacy = sysfs.glob(LEGACY_GLOB)
    if legacy:
        n = sysfs.read_int(legacy[0])
        cur = LEGACY.get(n or 1)
        raw = " ".join(LEGACY.values())
        return cur, raw, "legacy"
    return None, None, source


def _write_platform(profile: str, source: str) -> bool:
    """Write profile to the underlying platform_profile interface. Returns True on success."""
    try:
        if source == "legacy":
            n = {v: k for k, v in LEGACY.items()}[profile]
            sysfs.write(sysfs.glob(LEGACY_GLOB)[0], n)
        elif source == "nested_platform_profile":
            nested = _find_nested_platform()
            if nested:
                sysfs.write(nested[0], profile)
            else:
                return False
        elif source == "ppd":
            # PPD-only: no platform_profile to write
            return True
        else:
            sysfs.write(PLATFORM, profile)
        return True
    except Exception:
        return False


def state() -> dict:
    # 1) Try platform_profile interfaces (Surface/ACPI)
    cur, raw, source = _platform_state()

    # 2) If PPD is available, it is the authoritative control for CPU behavior on
    #    Bazzite/tuned systems. Prefer PPD's active profile for "current".
    ppd_cur = _ppd_get() if _ppd_available() else None
    if ppd_cur:
        if cur is None and raw is None:
            # No platform_profile exposed, but PPD is - use PPD
            source = "ppd"
            choices = ["low-power", "balanced", "performance"]
            cur = FROM_PPD.get(ppd_cur, ppd_cur)
            raw = " ".join(choices)
        else:
            # Both available: platform_profile gives us the full choice list (4 modes on Surface),
            # PPD gives us the effective CPU state. Use PPD's mapping as current so UI reflects reality.
            mapped = FROM_PPD.get(ppd_cur)
            if mapped and mapped in (raw or "").split():
                cur = mapped
            # Keep source as platform_profile but note PPD is driving
            source = "platform_profile"  # choices come from here

    if cur is None:
        # Last resort: PPD only (no platform_profile at all)
        if ppd_cur:
            choices = ["low-power", "balanced", "performance"]
            return {
                "available": True,
                "source": "ppd",
                "current": FROM_PPD.get(ppd_cur, "balanced"),
                "choices": [{"id": c, "label": LABELS.get(c, (c.title(), ""))[0],
                             "hint": LABELS.get(c, ("", ""))[1]} for c in choices],
            }
        return {"available": False}

    choices = sorted((raw or cur).split(), key=lambda c: ORDER.index(c) if c in ORDER else 99)
    return {
        "available": True,
        "source": source,
        "current": cur,
        "choices": [{"id": c, "label": LABELS.get(c, (c.title(), ""))[0], "hint": LABELS.get(c, ("", ""))[1]}
                    for c in choices],
    }


def set_profile(profile: str) -> None:
    st = state()
    if not st["available"]:
        raise RuntimeError("This kernel does not expose a performance profile")
    valid = [c["id"] for c in st["choices"]]
    if profile not in valid:
        raise ValueError(f"Unsupported profile: {profile}")

    source = st["source"]

    # On systems with PPD (tuned-ppd/power-profiles-daemon), the CPU scaling behavior
    # is controlled by PPD. We MUST set PPD profile for the change to take visible effect
    # (clocks, governor, EPP, intel_pstate limits). This is the primary control on Bazzite.
    ppd_target = TO_PPD.get(profile)
    ppd_ok = False
    if ppd_target and _ppd_available():
        ppd_ok = _ppd_set(ppd_target)
        # PPD typically syncs platform_profile itself, but we also write it explicitly
        # below for cases where PPD doesn't (or for nested/legacy interfaces).

    # Write to the underlying platform_profile interface as well (if applicable).
    # Skip if PPD-only source. For standard platform_profile, PPD may have already set it,
    # but writing again is harmless and ensures the exact requested mode when possible.
    plat_ok = False
    if source == "ppd":
        plat_ok = True  # nothing to write
    elif source in ("platform_profile", "nested_platform_profile", "legacy"):
        # If PPD is active it may overwrite platform_profile asynchronously; write after.
        plat_ok = _write_platform(profile, source)
        # If platform write failed but PPD succeeded, that's still a win for CPU behavior
        if not plat_ok and ppd_ok:
            plat_ok = True

    if not ppd_ok and not plat_ok:
        raise RuntimeError(f"Failed to set performance profile to {profile}")
    # If we have PPD and it failed but platform worked, we at least set platform_profile
    # (better than nothing) - don't raise, the write succeeded.

from . import base
from .decorators import *
import tuned.logs

import glob
import os
import errno
from tuned.utils.commands import commands

log = tuned.logs.get()
cmd = commands()

# Glob pattern for the AMD 3D V-Cache mode sysfs node.
# The ACPI device name (e.g. AMDI0101:00) can vary across boards,
# so a wildcard is used to locate all present devices.
_X3D_MODE_GLOB = "/sys/bus/platform/drivers/amd_x3d_vcache/*/amd_x3d_mode"
_VALID_MODES = frozenset(["cache", "frequency"])


def _find_x3d_paths():
	"""Return a list of resolved sysfs paths for amd_x3d_mode."""
	return glob.glob(_X3D_MODE_GLOB)


class AMDX3DPlugin(base.Plugin):
	"""
	Controls the AMD 3D V-Cache scheduling mode on dual-CCD processors
	such as the Ryzen 9 7950X3D, 7900X3D, 9950X3D, and 9900X3D.

	The kernel exposes a per-device sysfs knob that biases the CPU
	scheduler toward one CCD or the other:

	* `cache` -- scheduler prefers the CCD with 3D V-Cache (larger L3).
	  Best for games and cache-sensitive workloads.
	* `frequency` -- scheduler prefers the CCD *without* 3D V-Cache,
	  which reaches higher boost clocks.  Best for throughput-oriented or
	  compute workloads.  This is the kernel default.

	Requires the `amd_x3d_vcache` kernel driver (CONFIG_AMD_3D_VCACHE)
	and BIOS CPPC set to *Driver* mode.  On systems without a dual-CCD
	3D V-Cache CPU the plug-in does nothing and logs an informational
	message.

	`mode`:::
	Selects the scheduling preference.  Accepted values: `cache`,
	`frequency`.

	.Gaming profile — prefer the X3D CCD
	====
	----
	[amd_x3d]
	mode=cache
	----
	====

	.Desktop / productivity profile — prefer the high-frequency CCD
	====
	----
	[amd_x3d]
	mode=frequency
	----
	====
	"""

	@classmethod
	def _get_config_options(cls):
		return {
			"mode": None,
		}

	def _instance_init(self, instance):
		instance._has_static_tuning = True
		instance._has_dynamic_tuning = False

	def _instance_cleanup(self, instance):
		pass

	def _x3d_paths(self):
		return _find_x3d_paths()

	@command_set("mode")
	def _set_mode(self, value, instance, sim, remove):
		if value not in _VALID_MODES:
			if not sim:
				log.warning("amd_x3d: invalid mode '%s', expected one of: %s"
						% (value, ", ".join(sorted(_VALID_MODES))))
			return None

		paths = self._x3d_paths()
		if not paths:
			if not sim:
				log.info("amd_x3d: no AMD 3D V-Cache device found, skipping")
			return None

		if not sim:
			for path in paths:
				log.info("amd_x3d: setting mode to '%s' on %s" % (value, path))
				cmd.write_to_file(path, value,
						no_error=[errno.ENOENT] if remove else False)
		return value

	@command_get("mode")
	def _get_mode(self, instance):
		paths = self._x3d_paths()
		if not paths:
			return None

		# All CCD pairs share the same mode; read from the first found path.
		data = cmd.read_file(paths[0]).strip()
		if not data:
			return None
		return cmd.get_active_option(data)

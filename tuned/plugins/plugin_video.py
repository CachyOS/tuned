from . import base
from .decorators import *
import tuned.logs
from tuned.utils.commands import commands
import os
import errno
import re

log = tuned.logs.get()

class VideoPlugin(base.Plugin):
	"""
	Sets various power saving features on video cards.
	Radeon cards are supported.
	The powersave level can be specified
	by using the [option]`radeon_powersave` option. Supported values are:

	* `default`
	* `auto`
	* `low`
	* `mid`
	* `high`
	* `dynpm`
	* `dpm-battery`
	* `dpm-balanced`
	* `dpm-perfomance`

	For additional detail, see
	link:https://www.x.org/wiki/RadeonFeature/#kmspowermanagementoptions[KMS Power Management Options].

	NOTE: This plug-in is experimental and the option might change in future releases.

	.Setting powersave level for the Radeon video card to high
	====
	----
	[video]
	radeon_powersave=high
	----
	====

	Mobile hardware with amdgpu driven eDP panels can be configured
	with the [option]`panel_power_savings` option.
	This accepts a value range from 0 to 4, where 4 is the highest power savings
	but will trade off color accuracy.

	The [option]`dpm_perf_level` option controls the amdgpu
	`power_dpm_force_performance_level` sysfs knob. Supported values are:

	* `auto` — driver-managed automatic power state (default)
	* `low` — force lowest power state
	* `high` — force highest performance state
	* `manual` — manual power level control (used with `pp_power_profile_mode`)
	* `profile_standard`
	* `profile_min_sclk`
	* `profile_min_mclk`
	* `profile_peak`
	* `perf_determinism`

	.Forcing peak GPU performance
	====
	----
	[video]
	dpm_perf_level=profile_peak
	----
	====
	"""

	def __init__(self, *args, **kwargs):
		super(VideoPlugin, self).__init__(*args, **kwargs)

	def _init_devices(self):
		self._devices_supported = True
		self._free_devices = set()
		self._assigned_devices = set()

		# Add any radeon and amdgpu hardware with /any/ supported attributes present
		for device in self._hardware_inventory.get_devices("drm").match_sys_name("card*-*"):
			attrs = self._files(device.sys_name)
			for attr in attrs:
				if os.path.exists(attrs[attr]):
					self._free_devices.add(device.sys_name)
		self._cmd = commands()

	def _get_device_objects(self, devices):
		return [self._hardware_inventory.get_device("drm", x) for x in devices]

	@classmethod
	def _get_config_options(self):
		return {
			"radeon_powersave" : None,
			"panel_power_savings": None,
			"dpm_perf_level": None,
		}

	def _instance_init(self, instance):
		instance._has_dynamic_tuning = False
		instance._has_static_tuning = True

	def _instance_cleanup(self, instance):
		pass

	_DPM_PERF_LEVELS = frozenset([
		"auto", "low", "high", "manual",
		"profile_standard", "profile_min_sclk", "profile_min_mclk",
		"profile_peak", "perf_determinism",
	])
	_DPM_PERF_LEVELS_STR = ", ".join(sorted(_DPM_PERF_LEVELS))

	def _files(self, device):
		# power_dpm_force_performance_level is a PCI device attribute,
		# only accessible via the card node (card0), not via connectors
		# (card0-DP-1) whose device/ symlink points to the DRM card node
		card = device.split("-", 1)[0]
		return {
			"method" : "/sys/class/drm/%s/device/power_method" % device,
			"profile": "/sys/class/drm/%s/device/power_profile" % device,
			"dpm_state": "/sys/class/drm/%s/device/power_dpm_state" % device,
			"panel_power_savings": "/sys/class/drm/%s/amdgpu/panel_power_savings" % device,
			"dpm_perf_level": "/sys/class/drm/%s/device/power_dpm_force_performance_level" % card,
		}

	def apply_panel_power_saving_target(self, device, target, instance, sim=False):
		"""Apply the target value to the panel_power_savings file if it doesn't already have it"""

		# if we don't have the file, we might be radeon not amdgpu
		if not os.path.exists(self._files(device)["panel_power_savings"]):
			return None

		# make sure the value is different (avoids unnecessary kernel modeset)
		current = int(self._get_panel_power_savings(device, instance))
		if current == target:
			log.info(
				"panel_power_savings for %s already %s" % (device, target)
			)
			return target

		# flush it out
		log.info("%s panel_power_savings -> %s" % (device, target))
		if sim or self._cmd.write_to_file(self._files(device)["panel_power_savings"], target):
			return target
		return None

	@command_set("radeon_powersave", per_device=True)
	def _set_radeon_powersave(self, value, device, instance, sim, remove):
		sys_files = self._files(device)
		va = str(re.sub(r"(\s*:\s*)|(\s+)|(\s*;\s*)|(\s*,\s*)", " ", value)).split()
		if not os.path.exists(sys_files["method"]):
			if not sim:
				log.debug("radeon_powersave is not supported on '%s'" % device)
				return None
		for v in va:
			if v in ["default", "auto", "low", "mid", "high"]:
				if not sim:
					if (self._cmd.write_to_file(sys_files["method"], "profile", \
						no_error = [errno.ENOENT] if remove else False) and
						self._cmd.write_to_file(sys_files["profile"], v, \
							no_error = [errno.ENOENT] if remove else False)):
								return v
			elif v == "dynpm":
				if not sim:
					if (self._cmd.write_to_file(sys_files["method"], "dynpm", \
						no_error = [errno.ENOENT] if remove else False)):
							return "dynpm"
			# new DPM profiles, recommended to use if supported
			elif v in ["dpm-battery", "dpm-balanced", "dpm-performance"]:
				if not sim:
					state = v[len("dpm-"):]
					if (self._cmd.write_to_file(sys_files["method"], "dpm", \
						no_error = [errno.ENOENT] if remove else False) and
						self._cmd.write_to_file(sys_files["dpm_state"], state, \
							no_error = [errno.ENOENT] if remove else False)):
								return v
			else:
				if not sim:
					log.warning("Invalid option for radeon_powersave.")
				return None
		return None

	@command_get("radeon_powersave")
	def _get_radeon_powersave(self, device, instance, ignore_missing = False):
		sys_files = self._files(device)
		if not os.path.exists(sys_files["method"]):
			log.debug("radeon_powersave is not supported on '%s'" % device)
			return None
		method = self._cmd.read_file(sys_files["method"], no_error=ignore_missing).strip()
		if method == "profile":
			return self._cmd.read_file(sys_files["profile"]).strip()
		elif method == "dynpm":
			return method
		elif method == "dpm":
			return "dpm-" + self._cmd.read_file(sys_files["dpm_state"]).strip()
		else:
			return None

	@command_set("panel_power_savings", per_device=True)
	def _set_panel_power_savings(self, value, device, instance, sim, remove):
		"""Set the panel_power_savings value"""
		try:
			value = int(value, 10)
		except ValueError:
			log.warning("Invalid value %s for panel_power_savings" % value)
			return None
		if value in range(0, 5):
			return self.apply_panel_power_saving_target(device, value, instance, sim)
		else:
			log.warning("Invalid value %s for panel_power_savings" % value)
		return None

	@command_get("panel_power_savings")
	def _get_panel_power_savings(self, device, instance, ignore_missing=False):
		"""Get the current panel_power_savings value"""
		if not os.path.exists(self._files(device)["panel_power_savings"]):
			log.debug("panel_power_savings is not supported on '%s'" % device)
			return None
		fname = self._files(device)["panel_power_savings"]
		return self._cmd.read_file(fname, no_error=ignore_missing).strip()

	@command_set("dpm_perf_level", per_device=True)
	def _set_dpm_perf_level(self, value, device, instance, sim, remove):
		"""Set power_dpm_force_performance_level for amdgpu devices"""
		sys_files = self._files(device)
		if not os.path.exists(sys_files["dpm_perf_level"]):
			log.debug("dpm_perf_level is not supported on '%s'" % device)
			return None
		value = value.strip()
		if value not in self._DPM_PERF_LEVELS:
			log.warning("Invalid value '%s' for dpm_perf_level on '%s'. "
				"Valid values: %s" % (value, device, self._DPM_PERF_LEVELS_STR))
			return None
		if not sim:
			if not self._cmd.write_to_file(sys_files["dpm_perf_level"], value,
					no_error=[errno.ENOENT] if remove else False):
				return None
		return value

	@command_get("dpm_perf_level")
	def _get_dpm_perf_level(self, device, instance, ignore_missing=False):
		"""Get the current power_dpm_force_performance_level value"""
		sys_files = self._files(device)
		if not os.path.exists(sys_files["dpm_perf_level"]):
			log.debug("dpm_perf_level is not supported on '%s'" % device)
			return None
		return self._cmd.read_file(sys_files["dpm_perf_level"], no_error=ignore_missing).strip()
